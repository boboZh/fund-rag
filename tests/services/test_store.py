from decimal import Decimal

from app.services import store


def test_to_pymysql_escapes_date_format_then_placeholders():
    sql = "DATE_FORMAT(d.report_date, '%Y-%m-%d') AS report_date WHERE c.id = ?"
    assert (
        store.to_pymysql(sql)
        == "DATE_FORMAT(d.report_date, '%%Y-%%m-%%d') AS report_date WHERE c.id = %s"
    )


def test_plain_turns_fulltext_score_into_float():
    rows = store._plain([{"id": 1, "score": Decimal("12.5"), "stock_code": "600256"}])
    assert rows[0]["score"] == 12.5
    assert isinstance(rows[0]["score"], float)
    assert rows[0]["stock_code"] == "600256"


def test_empty_keyword_does_not_query():
    assert store.search_chunks_by_keyword(1, text="   ") == []


def test_invalid_chunk_ids_skip_query():
    assert store.list_chunks_by_ids(1, ["x", 0, -1, 1.5, True]) == []


def test_keyword_returns_empty_when_stock_has_no_docs(monkeypatch):
    calls = []

    def fake(sql, params=None):
        calls.append((sql, list(params or [])))
        return []

    monkeypatch.setattr(store, "exec_strict", fake)
    assert store.search_chunks_by_keyword(1, text="目标价", stock_code="600256") == []
    assert len(calls) == 1
    assert "stock_code" in calls[0][0]


def test_keyword_sql_filters_docs_and_inlines_limit(monkeypatch):
    calls = []

    def fake(sql, params=None):
        calls.append((sql, list(params or [])))
        if "FROM research_docs" in sql:
            return [{"id": 10}, {"id": 11}]
        return []

    monkeypatch.setattr(store, "exec_strict", fake)
    store.search_chunks_by_keyword(
        1,
        text="目标价",
        stock_code="600256",
        date_from="2026-01-01",
        limit=5,
    )
    sql, params = calls[1]
    assert "c.doc_id IN (?, ?)" in sql
    assert "d.report_date >= ?" in sql
    assert "LIMIT 5" in sql
    assert params == ["目标价", 1, 10, 11, "2026-01-01", "目标价"]


def test_insert_doc_returns_insert_id(monkeypatch):
    def fake(sql, params=None):
        assert params[3] == ""
        return {"insert_id": 42, "affected_rows": 1}

    monkeypatch.setattr(store, "exec_strict", fake)
    assert (
        store.insert_doc(
            {
                "user_id": 1,
                "file_hash": "a" * 32,
                "file_name": "广汇能源.pdf",
                "stock_code": "600256",
                "stock_name": "广汇能源",
                "report_date": "2026-10-05",
                "title": "公司深度报告",
                "org": "开源证券",
                "max_chars": 800,
                "overlap_paragraphs": 1,
            }
        )
        == 42
    )


def test_replace_chunks_reads_split_paragraphs_keys(monkeypatch):
    calls = []

    def fake(sql, params=None):
        calls.append((sql, list(params or [])))
        return {"insert_id": 0, "affected_rows": 1}

    monkeypatch.setattr(store, "exec_strict", fake)
    store.replace_chunks(
        9,
        1,
        [
            {
                "chunkIndex": 0,
                "chunkType": "text",
                "pageStart": 1,
                "pageEnd": 1,
                "heading": "估值",
                "paragraphCount": 1,
                "content": "目标价上调到25元。",
                "charCount": 10,
            }
        ],
    )
    assert "DELETE FROM research_chunks" in calls[0][0]
    assert calls[1][1] == [9, 1, 0, "text", 1, 1, "估值", 1, "目标价上调到25元。", 10]
