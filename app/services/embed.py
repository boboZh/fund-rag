from app.core.config import EMBED_DIM, RAG_BASE_URL, EMBED_MODEL, RAG_API_KEY
from app.utils.cancel import throw_if_cancelled, wait_or_cancel
import httpx

BATCH_SIZE = 16
RETRIES = 2

client = None


def embed_dim():
    dim = float(EMBED_DIM or 1024)
    if not dim.is_integer() or dim <= 0:
        raise ValueError(f"EMBED_DIM无效：{EMBED_DIM}")
    return int(dim)


class EmbedDimError(Exception):
    pass


class EmbedCountsError(Exception):
    pass


def get_client():
    global client
    if client is None:
        if not RAG_API_KEY:
            raise ValueError("缺少 RAG_API_KEY")
        client = httpx.Client(
            base_url=RAG_BASE_URL or "https://api.siliconflow.cn/v1",
            headers={
                "Authorization": f"Bearer {RAG_API_KEY}",
            },
            timeout=60,
        )
    return client


# 拼装送去向量化的text，拼上每页的标题，为了提高向量检索精度
def passage_text(info):
    stock_name = info.get("stock_name")
    heading = info.get("heading")
    content = info.get("content")
    title = "｜".join(
        part
        for part in (str(item or "").strip() for item in [stock_name, heading])
        if part
    )
    body = str(content or "")
    if not title:
        return body
    return f"{title}\n{body}"


def vectors_from(response, expected):
    data = response.get("data") if response else None
    rows = data if isinstance(data, list) else []

    if len(rows) != expected:
        raise EmbedCountsError(f"Embedding返回{len(rows)}条，请求了{expected}条")

    dim = embed_dim()
    vectors = []
    for row in sorted(rows, key=lambda row: row["index"]):
        vector = row.get("embedding")
        actual = len(vector) if isinstance(vector, list) else "空"
        if actual != dim:
            raise EmbedDimError(f"向量维度是 {actual}，需要 {dim}")
        vectors.append(vector)

    return vectors


def embed_batch(batch, cancel=None):
    client = get_client()
    last_error = None
    for attempt in range(RETRIES + 1):
        throw_if_cancelled(cancel)
        try:
            response = client.post(
                "/embeddings",
                json={"model": EMBED_MODEL or "BAAI/bge-m3", "input": batch},
                timeout=60,
            )
            response.raise_for_status()
            return vectors_from(response.json(), len(batch))
        except EmbedDimError:
            raise
        except EmbedCountsError as err:
            last_error = err
            if attempt == RETRIES:
                break
            wait_or_cancel(0.2 * (attempt + 1), cancel)
        except httpx.HTTPError as err:
            last_error = err
            if attempt == RETRIES:
                break
            wait_or_cancel(0.2 * (attempt + 1), cancel)
    raise last_error


# texts: 已经准备好的文本，输出等长的向量数组
def embed_texts(texts, *, cancel=None):
    inputs = texts if isinstance(texts, list) else []
    vectors = []
    for offset in range(0, len(inputs), BATCH_SIZE):
        throw_if_cancelled(cancel)
        batch = inputs[offset : offset + BATCH_SIZE]
        vectors.extend(embed_batch(batch, cancel))
    return vectors


# chunks: 切好的块
# 输出：与块一一对应的向量
def embed_chunks(chunks, *, stock_name=None, cancel=None):
    texts = []
    for chunk in chunks or []:
        texts.append(
            passage_text(
                {
                    "stock_name": stock_name,
                    "heading": chunk["heading"],
                    "content": chunk["content"],
                }
            )
        )
    return embed_texts(texts, cancel=cancel)


def embed_query(query, *, cancel=None):
    text = str(query or "")
    if not text.strip():
        raise ValueError("问题不能为空")
    result = embed_texts([text], cancel=cancel)
    return result[0]
