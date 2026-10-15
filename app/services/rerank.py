from app.utils.cancel import throw_if_cancelled, wait_or_cancel
from app.core.config import RAG_API_KEY, RAG_BASE_URL, RERANK_MODEL
import httpx
import math

RETRIES = 2

client = None


def get_client():
    global client
    if client is None:
        if not RAG_API_KEY:
            raise ValueError("缺少 RAG_API_KEY")
        client = httpx.Client(
            base_url=RAG_BASE_URL or "https://api.siliconflow.cn/v1",
            headers={
                "Authorization": f"Bearer {RAG_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
    return client


def message_of(err):
    data = err.response.data if err.response else None
    if data and data.get("message"):
        return str(data["message"])
    return err.get("message") or "重排失败"


def _should_retry(err):
    status = err.response.status if err.response else None
    if status is not None and status >= 400 and status < 500 and status != 429:
        return True


def rerank_passages(query, documents, cancel=None):
    """
    入参是问题和候选正文，出参是每条正文的下标和重排分
    重排模型把问题和这一段放在同一次打分里，比事先各自编码再比余弦准，所以只拿召回留下的少量候选来跑。
    失败再试两次，取消和参数错误不重试
    """
    if not query:
        raise ValueError("没有问题，无法重排")

    docs = [str(doc or "") for doc in (documents or [])]
    if not len(docs):
        return []

    client = get_client()
    last_error = None

    for attempt in range(RETRIES + 1):
        throw_if_cancelled(cancel)
        try:
            response = client.post(
                "/rerank",
                json={
                    "model": RERANK_MODEL or "BAAI/bge-reranker-v2-m3",
                    "query": str(query),
                    "documents": docs,
                    "top_n": len(docs),
                    "return_documents": False,
                },
                timeout=60,
            )
            response.raise_for_status()
            _response = response.json()
            data = _response.get("data") if _response else None
            results = data.results if data else None
            if not isinstance(results, list) or not len(results):
                raise ValueError("重排没有返回分数")

            return [
                {
                    "index": int(row["index"]),
                    "score": float(row.get("relevance_score") or row.get("score")),
                }
                for row in results
                if row["index"].is_integer and math.isfinite(row["score"])
            ]
        except httpx.HTTPError as err:
            last_error = err
            if (not _should_retry(err)) or attempt == RETRIES:
                break
            wait_or_cancel(0.2 * (attempt + 1), cancel)

    error = ValueError(message_of(last_error))
    error["name"] = last_error["name"]
    raise error
