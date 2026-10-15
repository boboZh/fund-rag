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
    # 硅基流动把原因放在 body.message。超时和连接失败没有 response，只能用异常文本。
    response = getattr(err, "response", None)
    data = None
    if response is not None:
        try:
            data = response.json()
        except ValueError:
            data = None
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    text = str(err).strip()
    return text or "重排失败"


def _should_retry(err):
    # 4xx 是请求本身不对，原样再发还是失败。429 是限流，和 5xx、断连一样可以再试。
    response = getattr(err, "response", None)
    status = response.status_code if response is not None else None
    if status is not None and 400 <= status < 500 and status != 429:
        return False
    return True


def _index_of(value):
    # 下标必须是整数。1.5 截成 1 会对到另一段正文，这种直接丢掉。
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value)
    return value


def _score_of(row):
    # 0 是合法重排分。relevance_score 为 0 时不能改读 score，只有缺字段或 null 才改读。
    if row.get("relevance_score") is not None:
        raw = row["relevance_score"]
    else:
        raw = row.get("score")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    score = float(raw)
    if not math.isfinite(score):
        return None
    return score


def _scored_rows(results):
    rows = []
    for row in results:
        if not isinstance(row, dict):
            continue
        index = _index_of(row.get("index"))
        score = _score_of(row)
        if index is None or score is None:
            continue
        rows.append({"index": index, "score": score})
    return rows


def rerank_passages(query, documents, cancel=None):
    """
    入参是问题和候选正文，出参是每条正文的下标和重排分。
    重排模型把问题和这一段放在同一次打分里，比事先各自编码再比余弦准，所以只拿召回留下的少量候选来跑。
    失败再试两次。取消和参数错误不重试。
    步骤：空候选直接返回；请求 /rerank；从 results 映射下标和分数；4xx（除 429）立即失败，其余失败等待后重试。
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
            body = response.json()
            results = body.get("results") if isinstance(body, dict) else None
            if not isinstance(results, list) or not len(results):
                raise ValueError("重排没有返回分数")
            return _scored_rows(results)
        except (httpx.HTTPError, ValueError) as err:
            last_error = err
            if not _should_retry(err) or attempt == RETRIES:
                break
            wait_or_cancel(0.2 * (attempt + 1), cancel)

    raise ValueError(message_of(last_error)) from last_error
