"""对照 qdrant.js。文件不能叫 qdrant.py，会和安装的 qdrant-client 抢名字。

过滤在 Qdrant 内部和向量搜索一起做：必须有 user_id；有股票代码再加 stock_code；
有 date_from 再加 report_date 的范围。先全局取 20 条再在 Python 里过滤是错的，
正确公司的段落可能根本没进候选。正文留在 MySQL，这里只存过滤和展示用的短字段。
"""

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Range,
    VectorParams,
)

from app.core.config import EMBED_DIM, QDRANT_URL

COLLECTION = "research_chunks"

# report_date 用整数（20261005）而不是日期字符串，范围过滤才能直接比较。
PAYLOAD_INDEXES = [
    ("user_id", PayloadSchemaType.INTEGER),
    ("doc_id", PayloadSchemaType.INTEGER),
    ("stock_code", PayloadSchemaType.KEYWORD),
    ("report_date", PayloadSchemaType.INTEGER),
]

client = None


def embed_dim():
    dim = float(EMBED_DIM or 1024)
    if not dim.is_integer() or dim <= 0:
        raise ValueError(f"EMBED_DIM无效：{EMBED_DIM}")
    return int(dim)


def get_client():
    global client
    if client is None:
        client = QdrantClient(url=QDRANT_URL or "http://127.0.0.1:6333")
    return client


def _distance_name(distance):
    value = getattr(distance, "value", distance)
    return str(value)


def vector_params_of(info):
    config = getattr(info, "config", None)
    params = getattr(config, "params", None)
    vectors = getattr(params, "vectors", None)
    size = getattr(vectors, "size", None)
    distance = getattr(vectors, "distance", None)
    if not isinstance(size, int):
        return None
    return {"size": size, "distance": distance}


def _schema_type(value):
    raw = getattr(value, "value", value)
    return str(raw)


def ensure_payload_indexes(qdrant):
    info = qdrant.get_collection(COLLECTION)
    schema = info.payload_schema or {}
    for field_name, field_schema in PAYLOAD_INDEXES:
        current = schema.get(field_name)
        if current and _schema_type(current.data_type) == _schema_type(field_schema):
            continue
        if current:
            raise ValueError(
                f"Qdrant 字段 {field_name} 的索引类型是 {current.data_type}，需要 {field_schema}"
            )
        qdrant.create_payload_index(
            collection_name=COLLECTION,
            field_name=field_name,
            field_schema=field_schema,
            wait=True,
        )


def ensure_collection():
    """
    Qdrant 不可达或向量配置对不上时抛错。
    1. collection 不存在则按 EMBED_DIM、Cosine 创建。
    2. 已存在则核对维度和距离，不一致直接失败，避免把向量写进错误的空间。
    3. 补齐还没有的 payload 索引。
    """
    qdrant = get_client()
    dim = embed_dim()
    if not qdrant.collection_exists(COLLECTION):
        qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
    else:
        info = qdrant.get_collection(COLLECTION)
        vectors = vector_params_of(info)
        actual = (
            f"{vectors['size']}/{_distance_name(vectors['distance'])}"
            if vectors
            else "未知"
        )
        if (
            not vectors
            or vectors["size"] != dim
            or _distance_name(vectors["distance"]) != "Cosine"
        ):
            raise ValueError(
                f"collection {COLLECTION} 的向量配置是 {actual}，需要 {dim}/Cosine"
            )
    ensure_payload_indexes(qdrant)
    print(f"Qdrant collection {COLLECTION} 已就绪")


def report_date_number(value):
    # 范围过滤用整数比较。2026-10-05 写成 20261005，才能表达「这天之后」。
    digits = str(value or "").replace("-", "")
    if not digits.isdigit() or len(digits) != 8:
        raise ValueError(f"报告日期无法写入向量库: {value}")
    return int(digits)


def payload_of(doc, chunk):
    """
    入参是研报和一块已经有 MySQL id 的切块。出参是 Qdrant payload。
    只放过滤和展示用的短字段。正文留在 MySQL，避免同一段话存两份。
    文档、切块的键与 store.py 读回的列名一致：user_id、page_start、chunk_type。
    """
    return {
        "user_id": int(doc["user_id"]),
        "doc_id": int(doc["id"]),
        "stock_code": doc["stock_code"],
        "stock_name": doc["stock_name"],
        "title": doc["title"],
        "org": doc["org"],
        "report_date": report_date_number(doc["report_date"]),
        "page_start": chunk.get("page_start"),
        "heading": chunk.get("heading") or None,
        "chunk_type": chunk["chunk_type"],
    }


def _point_id(chunk):
    value = chunk.get("id")
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number.is_integer() or number <= 0:
        return None
    return int(number)


def upsert_chunks(doc, chunks, vectors):
    """
    入参是研报、带自增 id 的切块，以及等长的向量。成功时无返回值。
    维度或条数对不上就抛错，不会 upsert。point id 用 research_chunks.id。
    """
    if len(chunks) != len(vectors):
        raise ValueError(f"向量条数 {len(vectors)} 和切块 {len(chunks)} 不一致")
    dim = embed_dim()
    points = []
    for index, chunk in enumerate(chunks):
        vector = vectors[index]
        actual = len(vector) if isinstance(vector, list) else "空"
        if actual != dim:
            raise ValueError(f"向量维度是 {actual}，需要 {dim}")
        chunk_id = _point_id(chunk)
        if chunk_id is None:
            raise ValueError("切块缺少 MySQL 自增 id，不能写入 Qdrant")
        points.append(
            PointStruct(
                id=chunk_id,
                vector=vector,
                payload=payload_of(doc, chunk),
            )
        )
    if not points:
        return
    get_client().upsert(collection_name=COLLECTION, points=points, wait=True)


def delete_by_doc(doc_id, user_id=None):
    # 按文档删点。重切会换成新的自增 id，不先删掉的话，旧 point 会留在库里。
    must = [
        FieldCondition(key="doc_id", match=MatchValue(value=int(doc_id))),
    ]
    if user_id is not None:
        must.append(FieldCondition(key="user_id", match=MatchValue(value=int(user_id))))
    get_client().delete(
        collection_name=COLLECTION,
        points_selector=Filter(must=must),
        wait=True,
    )


def search_filter(user_id, stock_code=None, date_from=None):
    must = [
        FieldCondition(key="user_id", match=MatchValue(value=int(user_id))),
    ]
    if stock_code:
        must.append(
            FieldCondition(
                key="stock_code",
                match=MatchValue(value=str(stock_code)),
            )
        )
    if date_from:
        must.append(
            FieldCondition(
                key="report_date",
                range=Range(gte=int(str(date_from).replace("-", ""))),
            )
        )
    return Filter(must=must)


def search_chunks(vector, *, user_id, stock_code=None, date_from=None, limit=20):
    """
    入参是问题向量、用户、可选股票代码、可选起始日期、条数。
    出参只含 point id 和余弦分。正文由调用方拿这些 id 回 MySQL 查。
    """
    result = get_client().query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=limit,
        query_filter=search_filter(user_id, stock_code, date_from),
        with_payload=False,
    )
    points = result.points if result is not None else []
    hits = []
    for point in points:
        score = point.score
        hits.append(
            {
                "id": point.id,
                "score": float(score) if score is not None else 0.0,
            }
        )
    return hits
