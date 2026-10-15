import json
import threading
import time

import httpx
import pytest

from app.services import embed
from app.utils.cancel import Cancelled


def vector(index, length=4):
    return {"index": index, "embedding": [index * 10 + i for i in range(length)]}


def ok(body):
    return httpx.Response(
        200, json={"data": [vector(i) for i in range(len(body["input"]))]}
    )


@pytest.fixture
def mock_api(monkeypatch):
    """
    入参：reply(body) -> httpx.Response，body 是解析后的请求体。
    出参：calls 列表，每发一次请求追加一个 httpx.Request。
    config 的值在 embed 导入时就绑定成模块变量，所以要改 embed 模块上的名字，改环境变量没用。
    httpx.Client 换成带 MockTransport 的版本，get_client 自己拼的 base_url 和请求头也能被断言到，且不会访问外网。
    """
    monkeypatch.setattr(embed, "RAG_API_KEY", "rag-test-key")
    monkeypatch.setattr(embed, "RAG_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(embed, "EMBED_MODEL", "BAAI/bge-m3")
    monkeypatch.setattr(embed, "EMBED_DIM", "4")
    monkeypatch.setattr(embed, "client", None)
    real_client = httpx.Client

    def install(reply):
        calls = []

        def handler(request):
            calls.append(request)
            return reply(json.loads(request.content))

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            embed.httpx,
            "Client",
            lambda **kwargs: real_client(transport=transport, **kwargs),
        )
        return calls

    return install


def test_passage_text_prefixes_stock_and_heading():
    """块前面加上股票名称和小节标题，原文接在下一行"""
    text = embed.passage_text(
        {
            "stock_name": "广汇能源",
            "heading": "盈利预测与估值",
            "content": "目标价 12.5 元。",
        }
    )
    assert text == "广汇能源｜盈利预测与估值\n目标价 12.5 元。"


def test_passage_text_skips_empty_parts():
    """空的名称或标题不留下多余的分隔符"""
    assert (
        embed.passage_text(
            {"stock_name": "广汇能源", "heading": None, "content": "维持买入。"}
        )
        == "广汇能源\n维持买入。"
    )
    assert (
        embed.passage_text({"stock_name": "", "heading": " 估值 ", "content": "合理。"})
        == "估值\n合理。"
    )
    assert embed.passage_text({"content": "只有正文"}) == "只有正文"


def test_query_sent_as_is(mock_api):
    """问题原样送出，不加 query 前缀，带上密钥和地址"""
    calls = mock_api(ok)
    result = embed.embed_query("往后产量靠什么上去")
    assert result == [0, 1, 2, 3]
    assert len(calls) == 1
    assert str(calls[0].url) == "https://api.siliconflow.cn/v1/embeddings"
    assert calls[0].headers["Authorization"] == "Bearer rag-test-key"
    assert json.loads(calls[0].content) == {
        "model": "BAAI/bge-m3",
        "input": ["往后产量靠什么上去"],
    }


def test_chunks_sent_in_batches_of_16(mock_api):
    """块按 16 条一批送出，并带上前缀"""
    calls = mock_api(ok)
    chunks = [{"heading": "产能", "content": f"第{i}段。"} for i in range(17)]
    vectors = embed.embed_chunks(chunks, stock_name="广汇能源")
    inputs = [json.loads(call.content)["input"] for call in calls]
    assert len(calls) == 2
    assert len(inputs[0]) == 16
    assert inputs[0][0] == "广汇能源｜产能\n第0段。"
    assert inputs[1] == ["广汇能源｜产能\n第16段。"]
    assert len(vectors) == 17


def test_vectors_follow_index_not_response_order(mock_api):
    """接口返回的行顺序打乱时，按 index 对回原文本"""
    mock_api(lambda body: httpx.Response(200, json={"data": [vector(1), vector(0)]}))
    assert embed.embed_texts(["甲", "乙"]) == [[0, 1, 2, 3], [10, 11, 12, 13]]


def test_retry_twice_then_raise(mock_api):
    """失败后再试 2 次，第三次仍失败就抛错"""
    calls = mock_api(lambda body: httpx.Response(500, json={"error": "上游超时"}))
    with pytest.raises(httpx.HTTPStatusError):
        embed.embed_query("目标价")
    assert len(calls) == 3


def test_count_mismatch_is_retried(mock_api):
    """条数不对按普通失败处理，同样重试"""
    calls = mock_api(lambda body: httpx.Response(200, json={"data": []}))
    with pytest.raises(embed.EmbedCountsError):
        embed.embed_query("目标价")
    assert len(calls) == 3


def test_wrong_dim_raises_without_retry(mock_api):
    """维度不对立即抛错，不再重试"""
    calls = mock_api(lambda body: httpx.Response(200, json={"data": [vector(0, 3)]}))
    with pytest.raises(embed.EmbedDimError, match="向量维度是 3，需要 4"):
        embed.embed_query("目标价")
    assert len(calls) == 1


def test_empty_query_rejected(mock_api):
    calls = mock_api(ok)
    with pytest.raises(ValueError, match="问题不能为空"):
        embed.embed_query("   ")
    assert calls == []


def test_missing_api_key(mock_api, monkeypatch):
    mock_api(ok)
    monkeypatch.setattr(embed, "RAG_API_KEY", None)
    with pytest.raises(ValueError, match="缺少 RAG_API_KEY"):
        embed.embed_query("目标价")


def test_cancelled_before_call_hits_nothing(mock_api):
    """已取消的请求不再打到接口"""
    calls = mock_api(ok)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        embed.embed_query("目标价", cancel=cancel)
    assert calls == []


def test_cancel_during_retry_wait_stops_retries(mock_api):
    """
    第一次请求失败后进入 0.2 秒的重试等待，50 毫秒时拨动开关。
    等待要提前结束，接口只被调用 1 次而不是 3 次。
    """
    cancel = threading.Event()

    def fail_and_schedule_cancel(body):
        threading.Timer(0.05, cancel.set).start()
        return httpx.Response(500)

    calls = mock_api(fail_and_schedule_cancel)
    started = time.monotonic()
    with pytest.raises(Cancelled):
        embed.embed_query("目标价", cancel=cancel)
    assert len(calls) == 1
    assert time.monotonic() - started < 0.2
