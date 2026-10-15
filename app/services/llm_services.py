from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

# 进程内复用同一个客户端，避免每个提问都重新握手建连。
client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

SYSTEM_PROMPT = "你是一个专业的基金分析助手。"


async def llm_stream_generator(question: str) -> AsyncIterator[str]:
    """把用户问题发给 DeepSeek，并按 SSE 帧逐段吐出回答。

    入参:
        question: 用户问题原文。
    出参:
        异步生成器。有文本的 delta 产出 ``data: {content}\\n\\n``，
        流结束后产出 ``data: [DONE]\\n\\n``。
    步骤:
        1. 带上基金分析助手的系统提示，发起流式补全。
        2. 跳过没有文本的 chunk，只把可见内容交给路由层。
        3. 补全结束后发 [DONE]，前端据此关闭 EventSource。
    """
    stream_response = await client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        stream=True,
    )
    async for chunk in stream_response:
        content = chunk.choices[0].delta.content
        if content:
            yield f"data: {content}\n\n"

    yield "data: [DONE]\n\n"
