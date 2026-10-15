import pytest

from app.services import retrieve as retrieve_mod
from app.utils.cancel import Cancelled


def row(chunk_id, content):
    return {
        "id": chunk_id,
        "doc_id": 3,
        "chunk_type": "text",
        "page_start": 7,
        "page_end": 7,
        "heading": "盈利预测与估值",
        "content": content,
        "stock_code": "600256",
        "stock_name": "广汇能源",
        "title": "深度报告",
        "org": "开源证券",
        "report_date": "2026-10-05",
    }


@pytest.fixture
def calls(monkeypatch):
    """换成假的向量、全文和重排，记录每次调用的参数。"""
    state = {"embed": [], "search": [], "keyword": [], "rerank": [], "points": [], "rows": [], "keyword_rows": []}

    def embed_query(query, *, cancel=None):
        state["embed"].append(query)
        return [0.1, 0.2]

    def search_chunks(vector, *, user_id, stock_code=None, date_from=None, limit=20):
        state["search"].append(
            {
                "vector": vector,
                "user_id": user_id,
                "stock_code": stock_code,
                "date_from": date_from,
                "limit": limit,
            }
        )
        return state["points"]

    def list_chunks_by_ids(_user_id, ids):
        state["listed_ids"] = ids
        return state["rows"]

    def search_chunks_by_keyword(user_id, *, text, stock_code=None, date_from=None, limit=20):
        state["keyword"].append(
            {
                "user_id": user_id,
                "text": text,
                "stock_code": stock_code,
                "date_from": date_from,
                "limit": limit,
            }
        )
        return state["keyword_rows"]

    def rerank_passages(query, documents, cancel=None):
        state["rerank"].append({"query": query, "documents": documents})
        return state["rerank_scores"]

    state["rerank_scores"] = []
    monkeypatch.setattr(retrieve_mod, "embed_query", embed_query)
    monkeypatch.setattr(retrieve_mod, "search_chunks", search_chunks)
    monkeypatch.setattr(retrieve_mod.store, "list_chunks_by_ids", list_chunks_by_ids)
    monkeypatch.setattr(retrieve_mod.store, "search_chunks_by_keyword", search_chunks_by_keyword)
    monkeypatch.setattr(retrieve_mod, "rerank_passages", rerank_passages)
    return state


def test_stock_code_of_unique_and_repeated():
    """括号里的六位代码可以取出；同一只写两次仍算一家"""
    assert retrieve_mod.stock_code_of("广汇能源（600256）的产能怎么看") == "600256"
    assert retrieve_mod.stock_code_of("600256 目标价 600256") == "600256"
    assert retrieve_mod.stock_code_of("往后产量靠什么上去") is None
    assert retrieve_mod.stock_code_of("比较 600256 和 000001") is None
    assert retrieve_mod.stock_code_of("编号 16002567") is None


def test_keyword_text_strips_code_and_empty_brackets():
    assert retrieve_mod.keyword_text_of("广汇能源（600256）的产能怎么看", "600256") == "广汇能源 的产能怎么看"
    assert retrieve_mod.keyword_text_of("广汇能源(600256)产能", "600256") == "广汇能源 产能"
    assert retrieve_mod.keyword_text_of("比较 600256 和 000001", None) == "比较 600256 和 000001"
    assert retrieve_mod.keyword_text_of("（600256）", "600256") == ""


def test_vector_filters_by_user_and_stock(calls):
    result = retrieve_mod.retrieve(query="600256 的目标价", user_id=8, top_k=3)

    assert calls["embed"] == ["600256 的目标价"]
    assert calls["search"][0]["limit"] == 3
    assert calls["search"][0]["user_id"] == 8
    assert calls["search"][0]["stock_code"] == "600256"
    assert result["stockCode"] == "600256"
    assert result["keywordQuery"] is None
    assert calls["keyword"] == []


def test_vector_keeps_qdrant_order_and_skips_missing_rows(calls):
    """MySQL 里已经没有的点丢掉，名次在剩下的结果里重排"""
    calls["points"] = [
        {"id": 9, "score": 0.91},
        {"id": 4, "score": 0},
        {"id": 15, "score": 0.4},
    ]
    calls["rows"] = [row(4, "第二段"), row(9, "第一段")]

    result = retrieve_mod.retrieve(query="目标价", user_id=8, top_k=5)

    assert calls["listed_ids"] == [9, 4, 15]
    assert [
        [hit["rank"], hit["id"], hit["score"], hit["vectorScore"], hit["content"]]
        for hit in result["hits"]
    ] == [
        [1, 9, 0.91, 0.91, "第一段"],
        [2, 4, 0, 0, "第二段"],
    ]


def test_date_from_is_passed_to_vector_search(calls):
    retrieve_mod.retrieve(query="最新的研报怎么说", user_id=8, date_from="2026-01-01")
    assert calls["search"][0]["date_from"] == "2026-01-01"


def test_invalid_date_and_mode_and_user_are_rejected(calls):
    with pytest.raises(ValueError, match="起始日期需要是 YYYY-MM-DD"):
        retrieve_mod.retrieve(query="目标价", user_id=8, date_from="2026/01/01")
    with pytest.raises(ValueError, match="检索模式只能是 vector、keyword、hybrid，收到 rerank"):
        retrieve_mod.retrieve(query="目标价", user_id=8, mode="rerank")
    with pytest.raises(ValueError, match="缺少 user_id"):
        retrieve_mod.retrieve(query="目标价", user_id=1.5)
    with pytest.raises(ValueError, match="缺少 user_id"):
        retrieve_mod.retrieve(query="目标价", user_id=None)
    assert calls["embed"] == []


def test_keyword_ranks_and_strips_filter_code(calls):
    calls["keyword_rows"] = [
        {**row(9, "第一段"), "score": "1.50"},
        {**row(4, "第二段"), "score": 0.2},
    ]
    result = retrieve_mod.retrieve(query="增持", user_id=8, mode="keyword", top_k=4)

    assert calls["embed"] == []
    assert calls["keyword"] == [
        {"user_id": 8, "text": "增持", "stock_code": None, "date_from": None, "limit": 4}
    ]
    assert [
        [hit["rank"], hit["id"], hit["score"], hit["keywordRank"], hit["vectorRank"]]
        for hit in result["hits"]
    ] == [
        [1, 9, 1.5, 1, None],
        [2, 4, 0.2, 2, None],
    ]


def test_keyword_drops_code_used_as_filter(calls):
    result = retrieve_mod.retrieve(
        query="广汇能源（600256）的产能怎么看", user_id=8, mode="keyword"
    )
    assert calls["keyword"][0]["text"] == "广汇能源 的产能怎么看"
    assert calls["keyword"][0]["stock_code"] == "600256"
    assert result["keywordQuery"] == "广汇能源 的产能怎么看"
    assert result["stockCode"] == "600256"


def test_two_codes_stay_in_keyword_query(calls):
    retrieve_mod.retrieve(query="比较 600256 和 000001 的目标价", user_id=8, mode="keyword")
    assert calls["keyword"][0]["text"] == "比较 600256 和 000001 的目标价"
    assert calls["keyword"][0]["stock_code"] is None


def test_keyword_skips_search_when_text_is_too_short(calls):
    result = retrieve_mod.retrieve(query="600256", user_id=8, mode="keyword")
    assert calls["keyword"] == []
    assert result["keywordQuery"] == ""
    assert result["hits"] == []


def test_explicit_stock_code_wins(calls):
    retrieve_mod.retrieve(
        query="目标价是多少", user_id=8, mode="keyword", stock_code="600256"
    )
    assert calls["keyword"][0] == {
        "user_id": 8,
        "text": "目标价是多少",
        "stock_code": "600256",
        "date_from": None,
        "limit": 5,
    }


def test_hybrid_fuses_by_rank_then_truncates(calls):
    calls["points"] = [
        {"id": 1, "score": 0.9},
        {"id": 2, "score": 0.8},
        {"id": 3, "score": 0.7},
    ]
    calls["rows"] = [row(3, "丙"), row(1, "甲"), row(2, "乙")]
    calls["keyword_rows"] = [
        {**row(2, "乙"), "score": 3.2},
        {**row(4, "丁"), "score": 2.1},
        {**row(1, "甲"), "score": 1.4},
    ]

    result = retrieve_mod.retrieve(query="600256 的目标价", user_id=8, top_k=4, mode="hybrid")

    assert calls["search"][0]["limit"] == 20
    assert calls["keyword"][0]["text"] == "的目标价"
    assert calls["keyword"][0]["limit"] == 20
    assert [
        [hit["id"], hit["rank"], hit["vectorRank"], hit["keywordRank"]]
        for hit in result["hits"]
    ] == [
        [2, 1, 2, 1],
        [1, 2, 1, 3],
        [4, 3, None, 2],
        [3, 4, 3, None],
    ]
    assert result["hits"][0]["score"] == pytest.approx(1 / 62 + 1 / 61)
    assert result["hits"][0]["vectorScore"] == 0.8
    assert result["hits"][0]["keywordScore"] == 3.2
    assert result["hits"][2]["vectorScore"] is None
    assert result["keywordQuery"] == "的目标价"
    assert calls["rerank"] == []


def test_rerank_drops_below_threshold_but_keeps_ranked(calls):
    calls["points"] = [
        {"id": 1, "score": 0.9},
        {"id": 2, "score": 0.8},
        {"id": 3, "score": 0.7},
    ]
    calls["rows"] = [row(3, "丙"), row(1, "甲"), row(2, "乙")]
    calls["keyword_rows"] = [
        {**row(2, "乙"), "score": 3.2},
        {**row(4, "丁"), "score": 2.1},
        {**row(1, "甲"), "score": 1.4},
    ]
    calls["rerank_scores"] = [
        {"index": 3, "score": 0.91},
        {"index": 0, "score": 0.2},
        {"index": 1, "score": 0.05},
        {"index": 2, "score": 0.04},
    ]

    result = retrieve_mod.retrieve(
        query="600256 的目标价",
        user_id=8,
        top_k=2,
        mode="hybrid",
        rerank=True,
        min_score=0.5,
    )

    assert calls["search"][0]["limit"] == 20
    assert calls["rerank"] == [{"query": "600256 的目标价", "documents": ["乙", "甲", "丁", "丙"]}]
    assert [[hit["id"], hit["rerankRank"], hit["rrfRank"]] for hit in result["hits"]] == [[3, 1, 4]]
    assert [[hit["id"], hit["rerankRank"], hit["dropped"]] for hit in result["ranked"]] == [
        [3, 1, False],
        [2, 2, True],
        [1, 3, True],
        [4, 4, True],
    ]
    assert result["ranked"][0]["rerankScore"] == 0.91
    assert result["minScore"] == 0.5


def test_zero_rerank_score_stays_when_threshold_is_zero(calls):
    """重排分 0 不低于阈值 0，要留下；空字符串表示不设阈值"""
    calls["points"] = [{"id": 1, "score": 0.4}]
    calls["rows"] = [row(1, "甲")]
    calls["rerank_scores"] = [{"index": 0, "score": 0}]

    kept = retrieve_mod.retrieve(
        query="目标价", user_id=8, mode="vector", rerank=True, min_score=0
    )
    assert kept["hits"][0]["rerankScore"] == 0
    assert kept["hits"][0]["dropped"] is False

    calls["rerank"].clear()
    calls["rerank_scores"] = [{"index": 0, "score": -0.1}]
    dropped = retrieve_mod.retrieve(
        query="目标价", user_id=8, mode="vector", rerank=True, min_score=0
    )
    assert dropped["hits"] == []
    assert dropped["ranked"][0]["dropped"] is True

    unset = retrieve_mod.retrieve(
        query="目标价", user_id=8, mode="vector", rerank=True, min_score=""
    )
    assert unset["minScore"] is None
    assert unset["ranked"][0]["dropped"] is False


def test_vector_rerank_recalls_twenty_and_skips_empty(calls):
    result = retrieve_mod.retrieve(query="目标价", user_id=8, top_k=3, mode="vector", rerank=True)
    assert calls["search"][0]["limit"] == 20
    assert calls["rerank"] == []
    assert result["hits"] == []
    assert result["ranked"] == []


def test_cancelled_before_retrieve_skips_embed(calls):
    cancel = __import__("threading").Event()
    cancel.set()
    with pytest.raises(Cancelled):
        retrieve_mod.retrieve(query="目标价", user_id=8, cancel=cancel)
    assert calls["embed"] == []
