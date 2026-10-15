from fastapi.testclient import TestClient
from unittest.mock import patch
from main import app

client = TestClient(app)


# 造一个假的异步生成器，用来替代真实的大模型
async def fake_llm_generator(question: str):
    yield f"data: 模拟回复：你问了({question})\n\n"
    yield "data: [DONE]\n\n"


# 编写测试用例，并拦截真实的方法
@patch("app.api.chat.llm_stream_generator")
def test_chat_stream_api(mock_llm_generator):
    # 把底层的真实函数，替换成上面写的假函数
    mock_llm_generator.side_effect = fake_llm_generator

    payload = {"question": "杭州天气"}
    response = client.post("/api/chat/stream", json=payload)

    assert response.status_code == 200

    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    # 校验返回的内容
    assert "data: 模拟回复：你问了(杭州天气)" in response.text
    assert "data: [DONE]" in response.text

    # 校验路由有没有正确地把参数传递给底层Service
    mock_llm_generator.assert_called_once_with("杭州天气")


def test_info_api():
    payload = {"fund_code": "000001"}
    response = client.post("/api/chat/info", json=payload)

    assert response.status_code == 200
    assert response.json()["received_code"] == "000001"
