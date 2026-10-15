import pytest
from app.services.llm_services import llm_stream_generator


# 加上这个装饰器，告诉pytest 这是一个异步测试函数
@pytest.mark.asyncio
async def test_llm_generator():
    question = "什么是etf"
    chunks = []

    async for chunk in llm_stream_generator(question):
        chunks.append(chunk)

    assert len(chunks) > 0
    assert chunks[0].startswith("data: ")
