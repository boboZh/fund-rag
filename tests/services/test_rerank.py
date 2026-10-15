import json
import threading
import time

import httpx
import pytest

from app.services import rerank
from app.utils.cancel import Cancelled


@pytest.fixture
def mock_api(monkeypatch):
    """
    入参：reply(body) -> httpx.Response，body 是解析后的请求体。
    出参：calls 列表，每发一次请求追加一个 httpx.Request。
    config 的值在 rerank 导入时就绑定成模块变量，所以要改 rerank 模块上的名字。
    """
    monkeypatch.setattr(rerank, "RAG_API_KEY", "rag-test-key")
    monkeypatch.setattr(rerank, "RAG_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(rerank, "RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    monkeypatch.setattr(rerank, "client", None)
    real_client = httpx.Client

    def install(reply):
        calls = []

        def handler(request):
            calls.append(request)
            return reply(json.loads(request.content))

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            rerank.httpx,
            "Client",
            lambda **kwargs: real_client(transport=transport, **kwargs),
        )
        return calls

    return install


def test_returns_index_and_score(mock_api):
    """问题和每段正文一起交给重排接口，按下标把分数对回去，不按分数重排"""
    calls = mock_api(
        lambda body: httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.2},
                    {"index": 0, "relevance_score": 0.9},
                ]
            },
        )
    )

    scores = rerank.rerank_passages("目标价", ["甲", "乙"])

    assert len(calls) == 1
    assert str(calls[0].url) == "https://api.siliconflow.cn/v1/rerank"
    assert calls[0].headers["Authorization"] == "Bearer rag-test-key"
    assert json.loads(calls[0].content) == {
        "model": "BAAI/bge-reranker-v2-m3",
        "query": "目标价",
        "documents": ["甲", "乙"],
        "top_n": 2,
        "return_documents": False,
    }
    assert scores == [
        {"index": 1, "score": 0.2},
        {"index": 0, "score": 0.9},
    ]


def test_zero_score_is_kept(mock_api):
    """relevance_score 为 0 时保留 0，不改读旁边的 score"""
    mock_api(
        lambda body: httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 0, "score": 0.8}]},
        )
    )
    assert rerank.rerank_passages("目标价", ["甲"]) == [{"index": 0, "score": 0.0}]


def test_falls_back_to_score_field(mock_api):
    """没有 relevance_score 时用 score"""
    mock_api(
        lambda body: httpx.Response(
            200, json={"results": [{"index": 1.0, "score": 0.3}]}
        )
    )
    assert rerank.rerank_passages("目标价", ["甲", "乙"]) == [{"index": 1, "score": 0.3}]


def test_drops_non_integer_index(mock_api):
    """1.5 截成 1 会对到另一段，这种下标丢掉"""
    mock_api(
        lambda body: httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1.5, "relevance_score": 0.9},
                    {"index": 2, "relevance_score": 0.4},
                ]
            },
        )
    )
    assert rerank.rerank_passages("目标价", ["甲", "乙", "丙"]) == [
        {"index": 2, "score": 0.4}
    ]


def test_drops_non_finite_score():
    """无穷和缺失分数不能拿去和阈值比较"""
    assert rerank._scored_rows(
        [
            {"index": 0, "relevance_score": float("inf")},
            {"index": 1, "relevance_score": None},
            {"index": 2, "score": float("nan")},
        ]
    ) == []


def test_empty_documents_skip_request(mock_api):
    calls = mock_api(lambda body: httpx.Response(200, json={"results": []}))
    assert rerank.rerank_passages("目标价", []) == []
    assert calls == []


def test_client_error_is_not_retried(mock_api):
    """400 是参数错误，只请求一次，并把接口的 message 抛出去"""
    calls = mock_api(
        lambda body: httpx.Response(400, json={"message": "模型不存在"})
    )
    with pytest.raises(ValueError, match="模型不存在"):
        rerank.rerank_passages("目标价", ["甲"])
    assert len(calls) == 1


def test_server_error_is_retried(mock_api, monkeypatch):
    """503 会再试，下一次成功就返回分数"""
    monkeypatch.setattr(rerank, "wait_or_cancel", lambda seconds, cancel: None)
    state = {"n": 0}

    def reply(body):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503, json={})
        return httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": 0.4}]}
        )

    calls = mock_api(reply)
    assert rerank.rerank_passages("目标价", ["甲"]) == [{"index": 0, "score": 0.4}]
    assert len(calls) == 2


def test_empty_results_are_retried(mock_api, monkeypatch):
    """results 为空按失败处理，重试两次后抛「重排没有返回分数」"""
    monkeypatch.setattr(rerank, "wait_or_cancel", lambda seconds, cancel: None)
    calls = mock_api(lambda body: httpx.Response(200, json={"results": []}))
    with pytest.raises(ValueError, match="重排没有返回分数"):
        rerank.rerank_passages("目标价", ["甲"])
    assert len(calls) == 3


def test_rate_limit_is_retried(mock_api, monkeypatch):
    """429 是限流，要重试"""
    monkeypatch.setattr(rerank, "wait_or_cancel", lambda seconds, cancel: None)
    state = {"n": 0}

    def reply(body):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, json={"message": "限流"})
        return httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": 0.1}]}
        )

    calls = mock_api(reply)
    assert rerank.rerank_passages("目标价", ["甲"]) == [{"index": 0, "score": 0.1}]
    assert len(calls) == 2


def test_missing_api_key(mock_api, monkeypatch):
    mock_api(lambda body: httpx.Response(200, json={"results": []}))
    monkeypatch.setattr(rerank, "RAG_API_KEY", None)
    with pytest.raises(ValueError, match="缺少 RAG_API_KEY"):
        rerank.rerank_passages("目标价", ["甲"])


def test_cancelled_before_call_hits_nothing(mock_api):
    calls = mock_api(lambda body: httpx.Response(200, json={"results": []}))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        rerank.rerank_passages("目标价", ["甲"], cancel=cancel)
    assert calls == []


def test_cancel_during_retry_wait_stops_retries(mock_api):
    """第一次 503 后进入重试等待，等待中取消就不再发下一次请求"""
    cancel = threading.Event()

    def fail_and_schedule_cancel(body):
        threading.Timer(0.05, cancel.set).start()
        return httpx.Response(500)

    calls = mock_api(fail_and_schedule_cancel)
    started = time.monotonic()
    with pytest.raises(Cancelled):
        rerank.rerank_passages("目标价", ["甲"], cancel=cancel)
    assert len(calls) == 1
    assert time.monotonic() - started < 0.2
