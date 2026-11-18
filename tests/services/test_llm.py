import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services import llm_services
from app.services.llm_services import llm_stream_generator


# 加上这个装饰器，告诉pytest 这是一个异步测试函数
@pytest.mark.asyncio
async def test_llm_generator(monkeypatch):
    question = "什么是etf"
    chunks = []

    async def fake_stream():
        for content in (None, "ETF", "是交易型开放式指数基金。"):
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
            )

    create = AsyncMock(return_value=fake_stream())
    monkeypatch.setattr(llm_services.client.chat.completions, "create", create)

    async for chunk in llm_stream_generator(question):
        chunks.append(chunk)

    assert len(chunks) > 0
    assert chunks[0].startswith("data: ")
    assert chunks == [
        "data: ETF\n\n",
        "data: 是交易型开放式指数基金。\n\n",
        "data: [DONE]\n\n",
    ]
    create.assert_awaited_once_with(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": llm_services.SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        stream=True,
    )
