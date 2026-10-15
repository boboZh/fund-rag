from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def body(**overrides):
    payload = {
        "query": "广汇能源（600256）的目标价",
        "userId": 1,
        "topK": 5,
        "mode": "hybrid",
        "rerank": True,
        "minScore": None,
        "stockCode": "",
        "dateFrom": "",
    }
    payload.update(overrides)
    return payload


def result():
    return {
        "mode": "hybrid",
        "topK": 5,
        "stockCode": "600256",
        "keywordQuery": "广汇能源 的目标价",
        "rerank": True,
        "minScore": None,
        "hits": [],
        "ranked": [],
    }


@patch("app.api.retrieve.retrieve")
def test_route_passes_snake_case_and_returns_camel_case(mock_retrieve):
    """合法 JSON 交给服务时用列名，响应原样返回服务给出的驼峰字段"""
    mock_retrieve.return_value = result()
    response = client.post("/retrieve", json=body())
    assert response.status_code == 200
    assert response.json() == result()
    kwargs = mock_retrieve.call_args.kwargs
    assert kwargs["query"] == "广汇能源（600256）的目标价"
    assert kwargs["user_id"] == 1
    assert kwargs["top_k"] == 5
    assert kwargs["mode"] == "hybrid"
    assert kwargs["rerank"] is True
    assert kwargs["min_score"] is None
    assert kwargs["stock_code"] is None
    assert kwargs["date_from"] is None
    assert kwargs["cancel"] is not None


def test_illegal_mode_returns_400():
    response = client.post("/retrieve", json=body(mode="rerank"))
    assert response.status_code == 400
    assert response.json()["detail"] == "检索模式只能是 vector、keyword、hybrid，收到 rerank"


def test_missing_user_returns_400():
    payload = body()
    del payload["userId"]
    response = client.post("/retrieve", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "缺少 user_id"


def test_bad_stock_code_returns_service_message():
    response = client.post("/retrieve", json=body(stockCode="60025"))
    assert response.status_code == 400
    assert response.json()["detail"] == "股票代码需要是 6 位数字"


def test_zero_min_score_is_forwarded():
    """0 是合法阈值，不能被当成没传"""
    with patch("app.api.retrieve.retrieve") as mock_retrieve:
        mock_retrieve.return_value = result()
        response = client.post("/retrieve", json=body(minScore=0))
    assert response.status_code == 200
    assert mock_retrieve.call_args.kwargs["min_score"] == 0
