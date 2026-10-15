"""对照 ingest.js 里段落已经排好之后的半段。

PDF 版面仍由 Node 的 readingOrder.js 产出。这里接收段落列表：去目录、切块、
写入 MySQL，再把向量写入 Qdrant。同一用户和文件哈希已存在时不再切一遍。
"""

import copy
import logging
import math
import re
from datetime import date, datetime

import pymysql

from app.core.config import EMBED_MODEL
from app.services import qdrant_store, store
from app.services.chunk import split_paragraphs
from app.services.embed import embed_chunks

logger = logging.getLogger(__name__)

HASH_RE = re.compile(r"^[a-f0-9]{32}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# MySQL ER_DUP_ENTRY。uniq_user_file 挡住同一用户重复插入同一份 PDF。
DUP_ENTRY = 1062


# 把标题里的空白、#、■、▌ 去掉，好让后面把 ▌目录、# 目录 认成 目录。
def toc_title(value):
    return re.sub(r"[\s#■▌]", "", str(value or ""))


# 匹配目录条目末尾那种「一串点加页码」
# 公司概况........ 3
# 盈利预测．．．．．12
# 风险提示 . . . . 8
TOC_LEADER_RE = re.compile(
    r"(?:\.{3,}|．{3,}|·{3,}|‧{3,}|…{2,}|⋯{2,}|(?:\.\s*){3,})\s*\d{1,3}"
)


def has_toc_leader(paragraph):
    title = toc_title(paragraph.get("heading"))
    if title == "目录" or (title.endswith("目录") and len(title) <= 6):
        return True
    return TOC_LEADER_RE.search(paragraph.get("text") or "") is not None


def drop_toc(paragraphs):
    """
    入参是按阅读顺序排好的段落。出参是去掉目录后的段落，以及被丢掉的短记录。
    目录可能跨页，后面几页没有「目录」标题。点线后面的页码就能认出条目；
    换行拆开的半句和它留在同一页，一并去掉。
    """
    leader = [has_toc_leader(paragraph) for paragraph in paragraphs]
    drop = copy.deepcopy(leader)
    for index, paragraph in enumerate(paragraphs):
        if drop[index]:
            continue
        text = paragraph.get("text") or ""
        fragment = len(text) <= 120 and re.search(r"[。！？]", text) is None
        if not fragment:
            continue
        beside = any(
            0 <= other < len(paragraphs)
            and leader[other]
            and paragraphs[other]["page"] == paragraph["page"]
            for other in (index - 1, index + 1)
        )
        if beside:
            drop[index] = True
    kept = []
    dropped = []
    for index, paragraph in enumerate(paragraphs):
        if not drop[index]:
            kept.append(paragraph)
            continue
        dropped.append(
            {
                "page": paragraph.get("page"),
                "reason": "目录",
                "text": paragraph["text"][:80],
            }
        )

    return {"paragraphs": kept, "dropped": dropped}


def _fail(doc_id, message):
    # 失败状态写不进去时只记日志。再把这个异常抛出去，调用方会把它当成另一次入库失败。
    try:
        store.update_doc(
            doc_id,
            {
                "status": "failed",
                "error_msg": str(message or "解析失败")[:500],
            },
        )
    except Exception:
        logger.exception("写入失败状态也失败了 doc=%s", doc_id)


def _is_duplicate(err):
    return (
        isinstance(err, pymysql.err.IntegrityError)
        and bool(err.args)
        and err.args[0] == DUP_ENTRY
    )


def _positive_int(value, message):
    # bool 是 int 的子类。True 不能当成用户 id 或页码。
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(message)
    return value


def _require_text(value, limit, message):
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError(message)
    return text


def _report_date(value):
    # 路由可能传入 date。服务内部和 MySQL 的 DATE_FORMAT 读回值都用 YYYY-MM-DD。
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not DATE_RE.fullmatch(text):
        raise ValueError("请填写有效的报告日期")
    year, month, day = (int(part) for part in text.split("-"))
    try:
        date(year, month, day)
    except ValueError:
        raise ValueError("请填写有效的报告日期") from None
    return text


def _max_chars(value):
    # 缺省和非法值都收成 800，再夹进 400 到 1200。和 Node 的 clampNumber 一样，不因为偏大就拒绝整份请求。
    if isinstance(value, bool):
        return 800
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 800
    if not math.isfinite(number):
        return 800
    return min(1200, max(400, round(number)))


def _overlap(value):
    if value is None:
        return 1
    if isinstance(value, bool) or value not in (0, 1):
        raise ValueError("overlap_paragraphs 只能是 0 或 1")
    return value


def _paragraphs(value):
    if not isinstance(value, list) or not value:
        raise ValueError("没有可用段落")
    rows = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index + 1} 段不是对象")
        text = item.get("text")
        if not isinstance(text, str) or text == "":
            raise ValueError(f"第 {index + 1} 段缺少正文")
        page = _positive_int(item.get("page"), f"第 {index + 1} 段页码无效")
        kind = item.get("kind") or "text"
        if kind not in ("text", "table", "summary"):
            raise ValueError(f"第 {index + 1} 段类型无效")
        heading = item.get("heading")
        if heading is not None and not isinstance(heading, str):
            raise ValueError(f"第 {index + 1} 段标题无效")
        rows.append(
            {
                "text": text,
                "heading": heading or None,
                "kind": kind,
                "page": page,
            }
        )
    return rows


def _normalize(doc):
    """
    入参是路由转好的普通字典，键用 user_id、file_hash 这种列名。
    出参是可插入 research_docs 的元数据，以及给切块用的段落。
    校验失败抛 ValueError，此时还没有插入文档。
    """
    if not isinstance(doc, dict):
        raise ValueError("入库参数无效")
    file_hash = str(doc.get("file_hash") or "")
    if HASH_RE.fullmatch(file_hash) is None:
        raise ValueError("file_hash 需要是 32 位小写 md5")
    stock_code = str(doc.get("stock_code") or "").strip()
    if re.fullmatch(r"\d{6}", stock_code) is None:
        raise ValueError("股票代码需要是 6 位数字")
    file_name = str(doc.get("file_name") or "").strip()
    if not file_name:
        raise ValueError("缺少文件名")
    paragraphs = _paragraphs(doc.get("paragraphs"))
    file_path = str(doc.get("file_path") or "")
    if len(file_path) > 500:
        raise ValueError("file_path 过长")
    replace = doc.get("replace", False)
    if not isinstance(replace, bool):
        raise ValueError("replace 只能是布尔值")
    meta = {
        "user_id": _positive_int(doc.get("user_id"), "user_id 需要是正整数"),
        "file_hash": file_hash,
        "file_name": file_name[:255],
        "file_path": file_path,
        "stock_code": stock_code,
        "stock_name": _require_text(doc.get("stock_name"), 50, "请填写股票名称"),
        "title": _require_text(doc.get("title"), 255, "请填写研报标题"),
        "org": _require_text(doc.get("org"), 100, "请填写机构"),
        "report_date": _report_date(doc.get("report_date")),
        "max_chars": _max_chars(doc.get("max_chars", 800)),
        "overlap_paragraphs": _overlap(doc.get("overlap_paragraphs", 1)),
        "page_count": _page_count(doc.get("page_count"), paragraphs),
        "replace": replace,
    }
    return meta, paragraphs


def _page_count(value, paragraphs):
    # 调用方带了 PDF 总页数就用它。没有时，段落上的最大页码就是这份研报写到了第几页。
    if value is None:
        return max(paragraph["page"] for paragraph in paragraphs)
    return _positive_int(value, "page_count 无效")


def _result(doc_id, user_id):
    doc = store.get_doc(doc_id, user_id)
    if not doc:
        raise RuntimeError("写入后读不到研报")
    return {"doc": doc, "duplicate": False}


def _existing(user_id, file_hash):
    found = store.find_doc_by_hash(user_id, file_hash)
    if not found:
        return None
    return {"doc": found, "duplicate": True}


def _embed_saved(doc_id, meta, paragraphs):
    """
    入参是已经在 research_docs 里的文档 id、元数据和段落。出参是最终文档行。
    没有切出块，或向量条数、维度不对时标 failed，不 upsert。
    """
    try:
        # layout 已经去过目录时，这里几乎是空操作。调用方把目录页放进 paragraphs 时仍要丢掉。
        without_toc = drop_toc(paragraphs)
        chunks = split_paragraphs(
            without_toc["paragraphs"],
            {
                "max_chars": meta["max_chars"],
                "overlap_paragraphs": meta["overlap_paragraphs"],
            },
        )
        if not chunks:
            _fail(doc_id, "没有切出可用段落")
            return _result(doc_id, meta["user_id"])

        saved = store.get_doc(doc_id, meta["user_id"])
        if not saved:
            raise RuntimeError("写入后读不到研报")
        # 重切会插入新的自增 id。先删旧 point，再换 MySQL 里的块，否则旧向量还指着已经删掉的 id。
        qdrant_store.delete_by_doc(saved["id"], saved["user_id"])
        store.replace_chunks(doc_id, meta["user_id"], chunks)
        store.update_doc(
            doc_id,
            {
                "status": "embedding",
                "error_msg": None,
                "page_count": meta["page_count"],
            },
        )
        stored = store.list_chunks(doc_id, meta["user_id"])
        # 为了确认马上要向量化的行就是刚写入的那一批，防：
        # 1.同一次重切还没读回来，另一个请求又对同一个 doc_id 做了 DELETE 再 INSERT。
        # 2.写入和读回的 user_id 不一致
        if len(stored) != len(chunks):
            raise RuntimeError("写入的切块数量和读回的不一致")
        # content 保持原文。股票名称和小节标题只加在 embed_chunks 送去向量化的字符串上。
        vectors = embed_chunks(stored, stock_name=saved["stock_name"])
        qdrant_store.upsert_chunks(saved, stored, vectors)
        store.update_doc(
            doc_id,
            {
                "status": "embedded",
                "error_msg": None,
                "embed_model": EMBED_MODEL or "BAAI/bge-m3",
            },
        )
    except Exception as err:
        logger.exception("研报入库失败 doc=%s", doc_id)
        _fail(doc_id, str(err).strip() or "解析失败")
    return _result(doc_id, meta["user_id"])


def ingest(doc):
    """
    入参是研报元数据和已经按阅读顺序排好的段落。出参是文档行和 duplicate。
    同一用户、同一文件哈希已存在、且 replace 不为真时，直接返回已有文档，不再切块。
    replace 为真时在已有行上重切。Node 的入库、重切、重试都会这样调用。
    步骤：
    1. 校验。代码 6 位，日期 YYYY-MM-DD，max_chars 收在 400 到 1200，overlap 只能是 0 或 1。
    2. 没有现成文档就插入 research_docs，状态是 parsing。replace 时改已有行的切块参数。
    3. 去掉目录再切块。没有块就标 failed。
    4. 先按 doc_id 删掉 Qdrant 旧 point，再换 MySQL 里的块。
    5. 状态改为 embedding，向量化后 upsert。条数或维度不对则整份 failed，不写入向量。
    6. 成功后状态为 embedded，并写下 embed_model。
    """
    meta, paragraphs = _normalize(doc)
    existing = _existing(meta["user_id"], meta["file_hash"])
    if existing and not meta["replace"]:
        return existing
    if existing:
        doc_id = existing["doc"]["id"]
        store.update_doc(
            doc_id,
            {
                "status": "parsing",
                "error_msg": None,
                "max_chars": meta["max_chars"],
                "overlap_paragraphs": meta["overlap_paragraphs"],
            },
        )
        return _embed_saved(doc_id, meta, paragraphs)

    try:
        doc_id = store.insert_doc(meta)
    except pymysql.err.IntegrityError as err:
        # 查重和插入之间另一条请求已经写入同一哈希。唯一索引挡住后，返回那一条。
        if _is_duplicate(err):
            existing = _existing(meta["user_id"], meta["file_hash"])
            if existing and not meta["replace"]:
                return existing
            if existing:
                doc_id = existing["doc"]["id"]
                store.update_doc(
                    doc_id,
                    {
                        "status": "parsing",
                        "error_msg": None,
                        "max_chars": meta["max_chars"],
                        "overlap_paragraphs": meta["overlap_paragraphs"],
                    },
                )
                return _embed_saved(doc_id, meta, paragraphs)
        raise

    return _embed_saved(doc_id, meta, paragraphs)
