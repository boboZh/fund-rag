from app.services.embed import embed_query
from app.services.rerank import rerank_passages
from app.services.qdrant_store import search_chunks
from app.services import store
import re
import math
from datetime import date
from app.utils.cancel import throw_if_cancelled

DEFAULT_TOP_K = 5
MAX_TOP_K = 20

MIN_SCORE_MIN = 0.06
MIN_SCORE_MAX = 0.7419
DEFAULT_MIN_SCORE = MIN_SCORE_MIN

RECALL_LIMIT = 20

RRF_K = 60
MODES = ("vector", "keyword", "hybrid")

# 六位数字前后不能再挨着数字，避免把 16002567 这种更长的数切成股票代码。
STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def stock_code_of(query):
    """
    入参是用户问题。出参是唯一的六位代码；没有或不止一个时返回 None。
    同一只代码出现多次仍算一家。两只不同的代码无法确定公司，不加过滤，免得把另一家的段落滤掉。
    """
    found = set(STOCK_CODE_RE.findall(str(query or "")))
    if len(found) != 1:
        return None
    return found.pop()


# 代码已经用来限制公司，再放进全文查询容易添加噪声。ngram_token_size=2 会把 600256 切成 60、00、02，这些片段在每份研报里都有，只会把无关段落顶上来。
def keyword_text_of(query, stock_code):
    original = str(query or "")
    if not stock_code:
        return original.strip()
    text = re.sub(rf"(?<!\d){stock_code}(?!\d)", " ", original)
    previous = ""
    while previous != text:
        previous = text
        text = re.sub(r"[（(【\[]\s*[）)】\]]", " ", text)

    return re.sub(r"\s+", " ", text).strip()


# 少于两个字母或数字组不出 ngram，全文检索没有结果。标准库 re 不支持 \p{L}，用 isalnum 覆盖汉字和数字。
def has_keyword_text(text):
    count = 0
    for char in str(text or ""):
        if char.isalnum():
            count += 1
            if count >= 2:
                return True
    return False


def filter_stock_code(query, stock_code):
    if stock_code is None or str(stock_code).strip() == "":
        return stock_code_of(query)
    code = str(stock_code).strip()
    if re.fullmatch(r"\d{6}", code) is None:
        raise ValueError("股票代码需要是 6 位数字")
    return code


def score_of(value):
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0
    return score if math.isfinite(score) else 0


def date_from_of(value):
    # 空字符串表示不限日期。非法日期直接拒绝，避免静默放宽成「全部研报」。
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not DATE_RE.fullmatch(text):
        raise ValueError("起始日期需要是 YYYY-MM-DD")
    year, month, day = (int(part) for part in text.split("-"))
    try:
        date(year, month, day)
    except ValueError:
        raise ValueError("起始日期需要是 YYYY-MM-DD") from None
    return text


def min_score_of(value):
    # 空字符串表示这次不设阈值。0 是合法阈值，重排分低于 0 的段落会被丢掉。
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("阈值需要是数字") from None
    if not math.isfinite(parsed):
        raise ValueError("阈值需要是数字")
    return parsed


def _round_half_away(value):
    # Math.round 把 2.5 收成 3。Python round 把 2.5 收成 2，topK 会少一条。
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def top_k_of(value):
    if value is None or value == "":
        return DEFAULT_TOP_K
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TOP_K
    if not math.isfinite(parsed):
        return DEFAULT_TOP_K
    return min(MAX_TOP_K, max(1, _round_half_away(parsed)))


def owner_id_of(value):
    # 对齐 Number.isInteger 且大于 0。1.5 不能截成 1，None 和 "abc" 都是缺少 user_id。
    if isinstance(value, bool):
        raise ValueError("缺少 user_id")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("缺少 user_id") from None
    if not math.isfinite(number) or not number.is_integer() or number <= 0:
        raise ValueError("缺少 user_id")
    return int(number)


def _kept(fields, key):
    # 0 是合法分数和名次。缺字段才变成 null，不能用 or 把 0 吃掉。
    if key not in fields or fields[key] is None:
        return None
    return fields[key]


def _col(row, snake, camel):
    # 第一次是 MySQL 行，键是 doc_id。RRF 和重排会把已经组装好的 hit 再传进来，键是 docId。
    if snake in row:
        return row[snake]
    return row[camel]


def chunk_hit(row, fields):
    return {
        "rank": fields["rank"],
        "score": fields["score"],
        "vectorRank": _kept(fields, "vector_rank"),
        "vectorScore": _kept(fields, "vector_score"),
        "keywordRank": _kept(fields, "keyword_rank"),
        "keywordScore": _kept(fields, "keyword_score"),
        "rrfRank": _kept(fields, "rrf_rank"),
        "rrfScore": _kept(fields, "rrf_score"),
        "rerankRank": _kept(fields, "rerank_rank"),
        "rerankScore": _kept(fields, "rerank_score"),
        "dropped": bool(fields.get("dropped")),
        "id": _col(row, "id", "id"),
        "docId": _col(row, "doc_id", "docId"),
        "chunkType": _col(row, "chunk_type", "chunkType"),
        "pageStart": _col(row, "page_start", "pageStart"),
        "pageEnd": _col(row, "page_end", "pageEnd"),
        "heading": _col(row, "heading", "heading"),
        "content": _col(row, "content", "content"),
        "stockCode": _col(row, "stock_code", "stockCode"),
        "stockName": _col(row, "stock_name", "stockName"),
        "title": _col(row, "title", "title"),
        "org": _col(row, "org", "org"),
        "reportDate": _col(row, "report_date", "reportDate"),
    }


def vector_recall(query, owner_id, stock_code, date_from, limit, cancel):
    """
    入参是问题、用户、股票代码、起始日期、条数和取消开关。出参按余弦相似度排列，原文来自 MySQL。
    Qdrant 里有、MySQL 里已经没有的点丢掉，名次在剩下的结果里重新排。
    """
    vector = embed_query(query, cancel=cancel)
    throw_if_cancelled(cancel)
    points = search_chunks(
        vector,
        user_id=owner_id,
        stock_code=stock_code,
        date_from=date_from,
        limit=limit,
    )
    rows = store.list_chunks_by_ids(owner_id, [point["id"] for point in points])

    by_id = {int(row["id"]): row for row in rows}
    hits = []
    for point in points:
        row = by_id.get(int(point["id"]))
        if row is None:
            continue
        rank = len(hits) + 1
        score = score_of(point["score"])
        hits.append(
            chunk_hit(
                row,
                {
                    "rank": rank,
                    "score": score,
                    "vector_rank": rank,
                    "vector_score": score,
                },
            )
        )
    return hits


def keyword_recall(query, owner_id, stock_code, date_from, limit):
    """
    入参是问题、用户、股票代码、起始日期和条数。出参是全文命中，以及实际送进全文索引的文本。
    过滤用的股票代码先从问题里拿掉；拿掉后组不出词，就不再查全文。
    """
    text = keyword_text_of(query, stock_code)
    if not has_keyword_text(text):
        return {"text": text, "hits": []}
    rows = store.search_chunks_by_keyword(
        owner_id,
        text=text,
        stock_code=stock_code,
        date_from=date_from,
        limit=limit,
    )
    hits = []
    for index, row in enumerate(rows):
        rank = index + 1
        score = score_of(row["score"])
        hits.append(
            chunk_hit(
                row,
                {
                    "rank": rank,
                    "score": score,
                    "keyword_rank": rank,
                    "keyword_score": score,
                },
            )
        )
    return {"text": text, "hits": hits}


def fuse_by_rank(vector_hits, keyword_hits):
    """
    入参是两路各自按名次排好的命中。出参按融合分从高到低，还没按 topK 截断。
    融合分 = 1/(60+向量名次) + 1/(60+关键词名次)。某一路没有这条，那一路不加分。
    只累加名次。余弦分和全文分不在一个量纲上。分数相同时按 chunk id 排，同一条问题重复检索名次不变。
    """
    by_id = {}

    def take(hit, source):
        chunk_id = int(hit["id"])
        item = by_id.get(chunk_id)
        if item is None:
            item = {
                "row": hit,
                "vector_rank": None,
                "vector_score": None,
                "keyword_rank": None,
                "keyword_score": None,
            }
            by_id[chunk_id] = item
        if source == "vector":
            item["vector_rank"] = hit["vectorRank"]
            item["vector_score"] = hit["vectorScore"]
        else:
            item["keyword_rank"] = hit["keywordRank"]
            item["keyword_score"] = hit["keywordScore"]

    for hit in vector_hits:
        take(hit, "vector")
    for hit in keyword_hits:
        take(hit, "keyword")

    results = []
    for item in by_id.values():
        score = 0
        if item["vector_rank"] is not None:
            score += 1 / (RRF_K + item["vector_rank"])
        if item["keyword_rank"] is not None:
            score += 1 / (RRF_K + item["keyword_rank"])
        results.append({"item": item, "score": score})
    # list.sort 返回 None。这里要的是排好的列表，调用方还要按 topK 截断。
    results.sort(key=lambda entry: (-entry["score"], entry["item"]["row"]["id"]))
    return results


def hybrid_hits_of(fused):
    return [
        chunk_hit(
            entry["item"]["row"],
            {
                "rank": index + 1,
                "score": entry["score"],
                "vector_rank": entry["item"]["vector_rank"],
                "vector_score": entry["item"]["vector_score"],
                "keyword_rank": entry["item"]["keyword_rank"],
                "keyword_score": entry["item"]["keyword_score"],
                "rrf_rank": index + 1,
                "rrf_score": entry["score"],
            },
        )
        for index, entry in enumerate(fused)
    ]


def rerank_hits(query, hits, top_k, min_score, cancel):
    """
    入参是已经召回的候选、问题、topK、解析后的阈值和取消开关。
    出参的 ranked 按重排分排列，低于阈值的标 dropped，实验台仍显示这些行。
    hits 是去掉 dropped 之后的前 topK 条。阈值只和重排分比较。
    """
    if not len(hits):
        return {"hits": [], "ranked": []}
    throw_if_cancelled(cancel)
    scored = rerank_passages(query, [hit["content"] for hit in hits], cancel=cancel)

    score_by_index = {item["index"]: item["score"] for item in scored}
    ordered = [
        {"hit": hit, "score": score_by_index.get(index)}
        for index, hit in enumerate(hits)
    ]
    # 没有分数的排到最后。分数相同或都缺失时按 chunk id，重复检索名次不变。
    ordered.sort(
        key=lambda entry: (
            entry["score"] is None,
            -(entry["score"] if entry["score"] is not None else 0),
            entry["hit"]["id"],
        )
    )
    ranked = []
    for index, item in enumerate(ordered):
        dropped = min_score is not None and (
            item["score"] is None or item["score"] < min_score
        )
        ranked.append(
            chunk_hit(
                item["hit"],
                {
                    "rank": index + 1,
                    "score": 0 if item["score"] is None else item["score"],
                    "vector_rank": item["hit"]["vectorRank"],
                    "vector_score": item["hit"]["vectorScore"],
                    "keyword_rank": item["hit"]["keywordRank"],
                    "keyword_score": item["hit"]["keywordScore"],
                    "rrf_rank": item["hit"]["rrfRank"],
                    "rrf_score": item["hit"]["rrfScore"],
                    "rerank_rank": index + 1,
                    "rerank_score": item["score"],
                    "dropped": dropped,
                },
            )
        )
    return {
        "ranked": ranked,
        "hits": [item for item in ranked if not item["dropped"]][:top_k],
    }


def retrieve(
    query=None,
    user_id=None,
    top_k=None,
    mode="vector",
    rerank=False,
    min_score=None,
    stock_code=None,
    date_from=None,
    cancel=None,
):
    """
    入参是问题、用户、topK、mode（vector / keyword / hybrid）、是否重排、阈值、可选的股票代码、起始日期和取消开关。
    显式传入的六位代码优先于从问题里识别出来的代码。出参字段与 Node 的 retrieve() 相同，供实验台对列。
    步骤：校验模式和用户；解析股票代码、日期和阈值；按模式召回；hybrid 做 RRF；打开重排时再按重排分截断。
    """
    if mode not in MODES:
        raise ValueError(f"检索模式只能是 vector、keyword、hybrid，收到 {mode}")
    owner_id = owner_id_of(user_id)

    throw_if_cancelled(cancel)
    limit = top_k_of(top_k)
    code = filter_stock_code(query, stock_code)
    since = date_from_of(date_from)
    threshold = min_score_of(min_score)
    rerank_on = bool(rerank)
    # 重排要看到名次靠后的正确段，所以召回先取 20 条，而不是直接取 topK。
    recall_limit = RECALL_LIMIT if mode == "hybrid" or rerank_on else limit

    vector_hits = []
    keyword_hits = []
    keyword_query = None

    if mode in ("vector", "hybrid"):
        vector_hits = vector_recall(query, owner_id, code, since, recall_limit, cancel)

    if mode in ("keyword", "hybrid"):
        throw_if_cancelled(cancel)
        keyword = keyword_recall(query, owner_id, code, since, recall_limit)
        keyword_hits = keyword["hits"]
        keyword_query = keyword["text"]

    if mode == "hybrid":
        pool = hybrid_hits_of(fuse_by_rank(vector_hits, keyword_hits))[
            : RECALL_LIMIT if rerank_on else limit
        ]
    elif mode == "vector":
        pool = vector_hits
    else:
        pool = keyword_hits

    if rerank_on:
        reranked = rerank_hits(query, pool, limit, threshold, cancel)
        return {
            "mode": mode,
            "topK": limit,
            "stockCode": code,
            "keywordQuery": keyword_query,
            "rerank": True,
            "minScore": threshold,
            "hits": reranked["hits"],
            "ranked": reranked["ranked"],
        }
    return {
        "mode": mode,
        "topK": limit,
        "stockCode": code,
        "keywordQuery": keyword_query,
        "rerank": False,
        "minScore": None,
        "hits": pool,
        "ranked": None,
    }
