from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def body(**overrides):
    payload = {
        "userId": 1,
        "fileHash": "a" * 32,
        "fileName": "广汇能源（600256）.pdf",
        "stockCode": "600256",
        "stockName": "广汇能源",
        "title": "公司深度报告",
        "org": "开源证券",
        "reportDate": "2026-10-05",
        "maxChars": 800,
        "overlapParagraphs": 1,
        "paragraphs": [
            {"text": "目标价上调到25元。", "heading": "估值", "kind": "text", "page": 7}
        ],
    }
    payload.update(overrides)
    return payload


def saved_doc():
    return {
        "id": 7,
        "user_id": 1,
        "file_hash": "a" * 32,
        "file_name": "广汇能源（600256）.pdf",
        "file_path": "",
        "stock_code": "600256",
        "stock_name": "广汇能源",
        "report_date": "2026-10-05",
        "title": "公司深度报告",
        "org": "开源证券",
        "page_count": 7,
        "max_chars": 800,
        "overlap_paragraphs": 1,
        "embed_model": "BAAI/bge-m3",
        "status": "embedded",
        "error_msg": None,
    }


def test_missing_title_returns_400():
    payload = body()
    del payload["title"]
    response = client.post("/ingest", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "请填写研报标题"


def test_stock_code_must_be_six_digits():
    response = client.post("/ingest", json=body(stockCode="60025"))
    assert response.status_code == 400
    assert response.json()["detail"] == "股票代码需要是 6 位数字"


@patch("app.api.ingest.ingest")
def test_route_passes_snake_case_and_returns_camel_case(mock_ingest):
    mock_ingest.return_value = {"doc": saved_doc(), "duplicate": False}
    response = client.post("/ingest", json=body())
    assert response.status_code == 200
    assert response.json()["duplicate"] is False
    assert response.json()["doc"]["stockCode"] == "600256"
    assert response.json()["doc"]["errorMsg"] is None
    passed = mock_ingest.call_args.args[0]
    assert passed["user_id"] == 1
    assert passed["file_hash"] == "a" * 32
    assert passed["stock_code"] == "600256"
    assert passed["replace"] is False
    assert passed["paragraphs"] == [
        {"text": "目标价上调到25元。", "heading": "估值", "kind": "text", "page": 7}
    ]
