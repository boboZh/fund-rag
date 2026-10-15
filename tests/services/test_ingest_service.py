import pymysql
import pytest

from app.services import ingest


def paragraph(text="目标价上调到25元。", heading="估值", kind="text", page=7):
    return {"text": text, "heading": heading, "kind": kind, "page": page}


def payload(**overrides):
    doc = {
        "user_id": 1,
        "file_hash": "a" * 32,
        "file_name": "广汇能源（600256）.pdf",
        "stock_code": "600256",
        "stock_name": "广汇能源",
        "title": "公司深度报告",
        "org": "开源证券",
        "report_date": "2026-10-05",
        "max_chars": 800,
        "overlap_paragraphs": 1,
        "paragraphs": [paragraph()],
    }
    doc.update(overrides)
    return doc


def doc_row(doc_id, **overrides):
    row = {
        "id": doc_id,
        "user_id": 1,
        "file_hash": "a" * 32,
        "stock_code": "600256",
        "stock_name": "广汇能源",
        "title": "公司深度报告",
        "org": "开源证券",
        "report_date": "2026-10-05",
        "status": "parsing",
        "error_msg": None,
    }
    row.update(overrides)
    return row


def test_drop_toc_removes_leader_lines_and_split_fragments():
    result = ingest.drop_toc(
        [
            {
                "page": 2,
                "text": "1、国家级稀缺煤油气资源................4",
                "kind": "text",
                "heading": "目录",
            },
            {
                "page": 3,
                "text": "图30：2026H1公司天然气产量3.64亿",
                "kind": "text",
                "heading": "正文",
            },
            {
                "page": 3,
                "text": "方................................15",
                "kind": "text",
                "heading": "正文",
            },
            {
                "page": 4,
                "text": "公司深耕主业。",
                "kind": "text",
                "heading": "公司概况",
            },
        ]
    )
    assert [item["text"] for item in result["paragraphs"]] == ["公司深耕主业。"]
    assert [item["reason"] for item in result["dropped"]] == ["目录", "目录", "目录"]


def test_invalid_stock_code_raises_before_insert(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("校验失败不应访问数据库")

    monkeypatch.setattr(ingest.store, "find_doc_by_hash", fail)
    with pytest.raises(ValueError, match="股票代码需要是 6 位数字"):
        ingest.ingest(payload(stock_code="60025"))


def test_duplicate_hash_returns_existing_doc(monkeypatch):
    existing = doc_row(3, status="embedded")

    def find_doc_by_hash(_user_id, _file_hash):
        return existing

    def insert_doc(_doc):
        raise AssertionError("重复文件不应再插入")

    monkeypatch.setattr(ingest.store, "find_doc_by_hash", find_doc_by_hash)
    monkeypatch.setattr(ingest.store, "insert_doc", insert_doc)
    assert ingest.ingest(payload()) == {"doc": existing, "duplicate": True}


def test_unique_conflict_returns_the_row_inserted_by_the_other_request(monkeypatch):
    existing = doc_row(9, status="embedded")
    calls = {"find": 0}

    def find_doc_by_hash(_user_id, _file_hash):
        calls["find"] += 1
        return None if calls["find"] == 1 else existing

    def insert_doc(_doc):
        raise pymysql.err.IntegrityError(1062, "Duplicate entry")

    monkeypatch.setattr(ingest.store, "find_doc_by_hash", find_doc_by_hash)
    monkeypatch.setattr(ingest.store, "insert_doc", insert_doc)
    assert ingest.ingest(payload()) == {"doc": existing, "duplicate": True}


def test_ingest_deletes_old_points_before_replacing_chunks(monkeypatch):
    calls = []
    state = {"status": "parsing", "error_msg": None, "page_count": None, "embed_model": None}

    def find_doc_by_hash(_user_id, _file_hash):
        calls.append("find")
        return None

    def insert_doc(doc):
        calls.append("insert")
        assert doc["max_chars"] == 400
        assert doc["report_date"] == "2026-10-05"
        assert doc["file_path"] == "public/uploads/research/" + "a" * 32 + ".pdf"
        assert doc["page_count"] == 12
        return 7

    def get_doc(doc_id, user_id):
        calls.append("get")
        return doc_row(doc_id, user_id=user_id, **state)

    def delete_by_doc(doc_id, user_id=None):
        calls.append("delete_qdrant")
        assert doc_id == 7
        assert user_id == 1

    def replace_chunks(doc_id, user_id, chunks):
        calls.append("replace")
        assert doc_id == 7 and user_id == 1
        assert chunks[0]["content"] == "目标价上调到25元。"

    def update_doc(_doc_id, fields):
        calls.append(("update", fields["status"]))
        state.update(fields)

    def list_chunks(_doc_id, _user_id):
        calls.append("list")
        return [
            {
                "id": 100,
                "heading": "估值",
                "content": "目标价上调到25元。",
                "page_start": 7,
                "chunk_type": "text",
            }
        ]

    def embed_chunks(chunks, *, stock_name=None, cancel=None):
        calls.append("embed")
        assert stock_name == "广汇能源"
        assert chunks[0]["content"] == "目标价上调到25元。"
        return [[0.1, 0.2]]

    def upsert_chunks(doc, chunks, vectors):
        calls.append("upsert")
        assert doc["id"] == 7
        assert chunks[0]["id"] == 100
        assert vectors == [[0.1, 0.2]]

    monkeypatch.setattr(ingest.store, "find_doc_by_hash", find_doc_by_hash)
    monkeypatch.setattr(ingest.store, "insert_doc", insert_doc)
    monkeypatch.setattr(ingest.store, "get_doc", get_doc)
    monkeypatch.setattr(ingest.store, "replace_chunks", replace_chunks)
    monkeypatch.setattr(ingest.store, "update_doc", update_doc)
    monkeypatch.setattr(ingest.store, "list_chunks", list_chunks)
    monkeypatch.setattr(ingest.qdrant_store, "delete_by_doc", delete_by_doc)
    monkeypatch.setattr(ingest.qdrant_store, "upsert_chunks", upsert_chunks)
    monkeypatch.setattr(ingest, "embed_chunks", embed_chunks)
    monkeypatch.setattr(ingest, "EMBED_MODEL", None)

    result = ingest.ingest(
        payload(
            max_chars=50,
            page_count=12,
            file_path="public/uploads/research/" + "a" * 32 + ".pdf",
        )
    )
    assert calls.index("delete_qdrant") < calls.index("replace")
    assert calls.index("replace") < calls.index("embed")
    assert calls.index("embed") < calls.index("upsert")
    assert ("update", "embedding") in calls
    assert ("update", "embedded") in calls
    assert state["page_count"] == 12
    assert state["embed_model"] == "BAAI/bge-m3"
    assert result["duplicate"] is False
    assert result["doc"]["status"] == "embedded"


def test_toc_only_marks_failed_without_touching_qdrant(monkeypatch):
    calls = []

    monkeypatch.setattr(ingest.store, "find_doc_by_hash", lambda *_args: None)
    monkeypatch.setattr(ingest.store, "insert_doc", lambda _doc: 4)

    def update_doc(_doc_id, fields):
        calls.append(fields)

    monkeypatch.setattr(ingest.store, "update_doc", update_doc)
    monkeypatch.setattr(
        ingest.store,
        "get_doc",
        lambda doc_id, _user_id: doc_row(doc_id, **calls[-1]),
    )
    monkeypatch.setattr(
        ingest.qdrant_store,
        "delete_by_doc",
        lambda *_args, **_kwargs: calls.append("delete_qdrant"),
    )

    result = ingest.ingest(
        payload(
            paragraphs=[
                {
                    "text": "公司概况........ 3",
                    "heading": "目录",
                    "kind": "text",
                    "page": 2,
                }
            ]
        )
    )
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_msg"] == "没有切出可用段落"
    assert "delete_qdrant" not in calls
    assert result["doc"]["status"] == "failed"


def test_embed_dimension_error_does_not_upsert(monkeypatch):
    calls = []

    monkeypatch.setattr(ingest.store, "find_doc_by_hash", lambda *_args: None)
    monkeypatch.setattr(ingest.store, "insert_doc", lambda _doc: 8)
    monkeypatch.setattr(
        ingest.store,
        "get_doc",
        lambda doc_id, _user_id: doc_row(
            doc_id, **(calls[-1] if calls and isinstance(calls[-1], dict) else {})
        ),
    )
    monkeypatch.setattr(ingest.qdrant_store, "delete_by_doc", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest.store, "replace_chunks", lambda *_args: None)

    def update_doc(_doc_id, fields):
        calls.append(fields)

    monkeypatch.setattr(ingest.store, "update_doc", update_doc)
    monkeypatch.setattr(
        ingest.store,
        "list_chunks",
        lambda *_args: [
            {
                "id": 1,
                "heading": "估值",
                "content": "目标价上调到25元。",
                "page_start": 7,
                "chunk_type": "text",
            }
        ],
    )

    def embed_chunks(_chunks, *, stock_name=None, cancel=None):
        raise ingest_dim_error()

    def upsert_chunks(*_args):
        raise AssertionError("维度不对不应 upsert")

    monkeypatch.setattr(ingest, "embed_chunks", embed_chunks)
    monkeypatch.setattr(ingest.qdrant_store, "upsert_chunks", upsert_chunks)

    result = ingest.ingest(payload())
    failed = calls[-1]
    assert failed["status"] == "failed"
    assert "向量维度" in failed["error_msg"]
    assert result["doc"]["status"] == "failed"


def ingest_dim_error():
    from app.services.embed import EmbedDimError

    return EmbedDimError("向量维度是 3，需要 1024")


def test_failed_status_update_is_swallowed(monkeypatch):
    monkeypatch.setattr(ingest.store, "find_doc_by_hash", lambda *_args: None)
    monkeypatch.setattr(ingest.store, "insert_doc", lambda _doc: 5)

    def update_doc(_doc_id, fields):
        if fields.get("status") == "failed":
            raise RuntimeError("db down")

    monkeypatch.setattr(ingest.store, "update_doc", update_doc)
    monkeypatch.setattr(
        ingest.store,
        "get_doc",
        lambda doc_id, _user_id: doc_row(doc_id, status="parsing"),
    )

    def embed_chunks(_chunks, *, stock_name=None, cancel=None):
        raise RuntimeError("embed down")

    monkeypatch.setattr(ingest, "embed_chunks", embed_chunks)
    monkeypatch.setattr(ingest.qdrant_store, "delete_by_doc", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest.store, "replace_chunks", lambda *_args: None)
    monkeypatch.setattr(
        ingest.store,
        "list_chunks",
        lambda *_args: [
            {
                "id": 1,
                "heading": None,
                "content": "目标价上调到25元。",
                "page_start": 7,
                "chunk_type": "text",
            }
        ],
    )

    result = ingest.ingest(payload())
    assert result["doc"]["id"] == 5


def test_replace_rechunks_the_existing_doc(monkeypatch):
    existing = doc_row(3, status="embedded")
    updates = []

    monkeypatch.setattr(ingest.store, "find_doc_by_hash", lambda *_args: existing)

    def insert_doc(_doc):
        raise AssertionError("已有文档不应再插入")

    monkeypatch.setattr(ingest.store, "insert_doc", insert_doc)
    monkeypatch.setattr(
        ingest.store,
        "update_doc",
        lambda doc_id, fields: updates.append((doc_id, fields)),
    )
    monkeypatch.setattr(
        ingest.store,
        "get_doc",
        lambda doc_id, _user_id: doc_row(doc_id, status=updates[-1][1]["status"]),
    )
    monkeypatch.setattr(ingest.qdrant_store, "delete_by_doc", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest.store, "replace_chunks", lambda *_args: None)
    monkeypatch.setattr(
        ingest.store,
        "list_chunks",
        lambda *_args: [
            {
                "id": 11,
                "heading": "估值",
                "content": "目标价上调到25元。",
                "page_start": 7,
                "chunk_type": "text",
            }
        ],
    )
    monkeypatch.setattr(ingest, "embed_chunks", lambda *_args, **_kwargs: [[0.1]])
    monkeypatch.setattr(ingest.qdrant_store, "upsert_chunks", lambda *_args: None)

    result = ingest.ingest(payload(replace=True, max_chars=500))
    assert updates[0] == (
        3,
        {"status": "parsing", "error_msg": None, "max_chars": 500, "overlap_paragraphs": 1},
    )
    assert updates[-1][1]["status"] == "embedded"
    assert result["duplicate"] is False
    assert result["doc"]["id"] == 3
