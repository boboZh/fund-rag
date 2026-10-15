from types import SimpleNamespace

import pytest

from app.services import qdrant_store
from qdrant_client.models import FieldCondition, MatchValue, Range


DOC = {
    "id": 3,
    "user_id": 1,
    "stock_code": "600256",
    "stock_name": "广汇能源",
    "title": "深度报告",
    "org": "开源证券",
    "report_date": "2026-10-05",
}

CHUNK = {
    "id": 9,
    "page_start": 7,
    "heading": "盈利预测与估值",
    "chunk_type": "text",
    "content": "目标价 12.5 元。",
}


def test_payload_only_keeps_filter_and_display_fields():
    assert qdrant_store.payload_of(DOC, CHUNK) == {
        "user_id": 1,
        "doc_id": 3,
        "stock_code": "600256",
        "stock_name": "广汇能源",
        "title": "深度报告",
        "org": "开源证券",
        "report_date": 20261005,
        "page_start": 7,
        "heading": "盈利预测与估值",
        "chunk_type": "text",
    }
    assert "content" not in qdrant_store.payload_of(DOC, CHUNK)


def test_report_date_rejects_non_yyyymmdd():
    with pytest.raises(ValueError, match="报告日期无法写入向量库"):
        qdrant_store.report_date_number("2026/10/05")


def test_upsert_skips_qdrant_when_dim_wrong(monkeypatch):
    monkeypatch.setattr(qdrant_store, "EMBED_DIM", "4")
    monkeypatch.setattr(qdrant_store, "client", object())

    def boom():
        raise AssertionError("维度不对时不应调用 Qdrant")

    monkeypatch.setattr(qdrant_store, "get_client", boom)
    with pytest.raises(ValueError, match="向量维度是 3，需要 4"):
        qdrant_store.upsert_chunks(DOC, [CHUNK], [[0.1, 0.2, 0.3]])


def test_upsert_skips_qdrant_when_counts_differ(monkeypatch):
    def boom():
        raise AssertionError("条数不对时不应调用 Qdrant")

    monkeypatch.setattr(qdrant_store, "get_client", boom)
    with pytest.raises(ValueError, match="向量条数 0 和切块 1 不一致"):
        qdrant_store.upsert_chunks(DOC, [CHUNK], [])


def test_search_always_filters_user_and_optional_stock_and_date(monkeypatch):
    calls = []

    class FakeClient:
        def query_points(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(points=[SimpleNamespace(id=9, score=0.91)])

    monkeypatch.setattr(qdrant_store, "get_client", lambda: FakeClient())
    hits = qdrant_store.search_chunks(
        [0.1, 0.2],
        user_id=8,
        stock_code="600256",
        date_from="2026-01-01",
        limit=3,
    )
    assert hits == [{"id": 9, "score": 0.91}]
    kwargs = calls[0]
    assert kwargs["collection_name"] == "research_chunks"
    assert kwargs["query"] == [0.1, 0.2]
    assert kwargs["limit"] == 3
    assert kwargs["with_payload"] is False
    must = kwargs["query_filter"].must
    assert must[0] == FieldCondition(key="user_id", match=MatchValue(value=8))
    assert must[1] == FieldCondition(
        key="stock_code", match=MatchValue(value="600256")
    )
    assert must[2] == FieldCondition(
        key="report_date", range=Range(gte=20260101)
    )


def test_search_without_stock_only_filters_user(monkeypatch):
    calls = []

    class FakeClient:
        def query_points(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(points=[])

    monkeypatch.setattr(qdrant_store, "get_client", lambda: FakeClient())
    qdrant_store.search_chunks([0.1, 0.2], user_id=8, limit=5)
    must = calls[0]["query_filter"].must
    assert must == [FieldCondition(key="user_id", match=MatchValue(value=8))]


def test_delete_by_doc_includes_user_when_given(monkeypatch):
    calls = []

    class FakeClient:
        def delete(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(qdrant_store, "get_client", lambda: FakeClient())
    qdrant_store.delete_by_doc(3, 1)
    kwargs = calls[0]
    assert kwargs["collection_name"] == "research_chunks"
    assert kwargs["wait"] is True
    assert kwargs["points_selector"].must == [
        FieldCondition(key="doc_id", match=MatchValue(value=3)),
        FieldCondition(key="user_id", match=MatchValue(value=1)),
    ]
