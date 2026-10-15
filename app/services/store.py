"""对照 store.js。每次查询新建连接、用完关闭，不握着一条闲置连接。

查询结果的键保持 MySQL 列名（stock_code、doc_id）。驼峰只出现在 HTTP JSON 上，
由 CamelModel 在路由边界转换。全文分在驱动里是 Decimal，这里转成 float，后面排序才和 Node 一样。
"""

from contextlib import closing
from decimal import Decimal

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import DB_HOST, DB_Name, DB_PASS, DB_PORT, DB_USER

DOC_FIELDS = """
  id, user_id, file_hash, file_name, file_path, stock_code, stock_name,
  DATE_FORMAT(report_date, '%Y-%m-%d') AS report_date,
  title, org, page_count, max_chars, overlap_paragraphs, embed_model,
  status, error_msg, created_at, updated_at
"""

UPDATABLE = {
    "status",
    "error_msg",
    "page_count",
    "max_chars",
    "overlap_paragraphs",
    "embed_model",
}


def connect():
    # DictCursor：每一行是字典，键是列名。让 fetchall() 返回字典列表，键是列名。默认游标返回元组，只能按下标取，row["stock_code"] 会失败
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        port=int(DB_PORT or 3306),
        database=DB_Name,
        charset="utf8mb4",
        cursorclass=DictCursor,
    )


def to_pymysql(sql):
    # mysql2 的占位符是 ?。pymysql 用 Python 的 % 格式化，占位符是 %s。
    # 先把 SQL 里已有的 % 写成 %%，再把 ? 换成 %s。顺序反了，刚换上的 %s 会被再转义成 %%s。
    # DATE_FORMAT(..., '%Y-%m-%d') 里本身有 %。pymysql 把 SQL 当 Python 格式化字符串处理，未转义的 %Y 会报错。先把 % 写成 %%，再把 ? 换成 %s，格式串和占位符才不会混在一起。
    return sql.replace("%", "%%").replace("?", "%s")

# 递归list和dict，遇到 Decimal（十进制精确小数，MySQL 的 DECIMAL / 全文分数常用这个类型）时改成 float（二进制浮点数）
def _plain(value):
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return float(value)
    return value


def exec_strict(sql, params=None):
    """
    入参：sql 仍用 Node 的 ? 占位符；params 按出现顺序对应。
    出参：SELECT 返回字典列表；INSERT / UPDATE / DELETE 返回 insert_id、affected_rows。
    步骤：打开连接，执行，取出结果，提交，关闭。失败则异常继续向外抛。
    """
    params = params or []
    # closing：离开 with 时调用 close()。pymysql 连接自己的 with 只负责提交或回滚，不会关闭连接。
    with closing(connect()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(to_pymysql(sql), params)
            # description 为 None 表示没有结果集，是写入而不是查询。
            if cursor.description is None:
                result = {
                    "insert_id": cursor.lastrowid,
                    "affected_rows": cursor.rowcount,
                }
            else:
                result = _plain(cursor.fetchall())
        conn.commit()
        return result


def _positive_int(value):
    # 对齐 Number.isInteger(Number(id)) && id > 0。1.5、"abc"、0 都丢掉。
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number.is_integer() or number <= 0:
        return None
    return int(number)


def _limit_size(limit):
    # 条数来自检索参数，已经收成 1–20。部分 MySQL 版本的预处理语句不接受 LIMIT 占位符，所以拼进 SQL。
    try:
        number = float(limit)
    except (TypeError, ValueError):
        number = 0.0
    if number != number or number == 0:
        number = 20.0
    return min(20, max(1, round(number)))


def find_doc_by_hash(user_id, file_hash):
    rows = exec_strict(
        f"SELECT {DOC_FIELDS} FROM research_docs WHERE user_id = ? AND file_hash = ? LIMIT 1",
        [user_id, file_hash],
    )
    return rows[0] if rows else None


def get_doc(doc_id, user_id):
    rows = exec_strict(
        f"SELECT {DOC_FIELDS} FROM research_docs WHERE id = ? AND user_id = ? LIMIT 1",
        [doc_id, user_id],
    )
    return rows[0] if rows else None


def get_doc_by_id(doc_id):
    rows = exec_strict(
        f"SELECT {DOC_FIELDS} FROM research_docs WHERE id = ? LIMIT 1",
        [doc_id],
    )
    return rows[0] if rows else None


def list_docs(user_id):
    return exec_strict(
        f"SELECT {DOC_FIELDS} FROM research_docs WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
        [user_id],
    )


def insert_doc(doc):
    # file_path 列是 NOT NULL。Node 会带上已经保存的 PDF 路径；直接调接口、没有路径时写空字符串。
    result = exec_strict(
        """INSERT INTO research_docs
          (user_id, file_hash, file_name, file_path, stock_code, stock_name, report_date, title, org, max_chars, overlap_paragraphs, status)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'parsing')""",
        [
            doc["user_id"],
            doc["file_hash"],
            doc["file_name"],
            doc.get("file_path") or "",
            doc["stock_code"],
            doc["stock_name"],
            doc["report_date"],
            doc["title"],
            doc["org"],
            doc["max_chars"],
            doc["overlap_paragraphs"],
        ],
    )
    return result["insert_id"]


def update_doc(doc_id, fields):
    # 字段名必须落在白名单里，调用方传入的键会直接拼进 SET，不能当作用户输入。
    sets = []
    params = []
    for key, value in fields.items():
        if key not in UPDATABLE:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(doc_id)
    exec_strict(f"UPDATE research_docs SET {', '.join(sets)} WHERE id = ?", params)

# 一次 INSERT 写入多行，不是循环执行多次单行插入。VALUES (?, ...) , (?, ...) , ... 拼成一条 SQL，参数按行依次排进同一个 params
def replace_chunks(doc_id, user_id, chunks):
    exec_strict("DELETE FROM research_chunks WHERE doc_id = ?", [doc_id])
    if not chunks:
        return
    placeholders = ", ".join(["(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"] * len(chunks))
    params = []
    for chunk in chunks:
        heading = chunk["heading"]
        params.extend(
            [
                doc_id,
                user_id,
                chunk["chunkIndex"],
                chunk["chunkType"],
                chunk["pageStart"],
                chunk["pageEnd"],
                heading[:255] if heading else None,
                chunk["paragraphCount"],
                chunk["content"],
                chunk["charCount"],
            ]
        )
    exec_strict(
        f"""INSERT INTO research_chunks
          (doc_id, user_id, chunk_index, chunk_type, page_start, page_end, heading, paragraph_count, content, char_count)
         VALUES {placeholders}""",
        params,
    )


def list_chunks(doc_id, user_id):
    return exec_strict(
        """SELECT id, doc_id, chunk_index, chunk_type, page_start, page_end, heading, paragraph_count, content, char_count
         FROM research_chunks WHERE doc_id = ? AND user_id = ? ORDER BY chunk_index ASC""",
        [doc_id, user_id],
    )


def list_chunks_by_ids(user_id, ids):
    # 按 Qdrant 返回的 point id 取原文。user_id 一起限定，避免把别的用户的块读出来。
    chunk_ids = []
    seen = set()
    for value in ids:
        chunk_id = _positive_int(value)
        if chunk_id is None or chunk_id in seen:
            continue
        seen.add(chunk_id)
        chunk_ids.append(chunk_id)
    if not chunk_ids:
        return []
    placeholders = ", ".join(["?"] * len(chunk_ids))
    return exec_strict(
        f"""SELECT c.id, c.doc_id, c.chunk_index, c.chunk_type, c.page_start, c.page_end,
                c.heading, c.content,
                d.stock_code, d.stock_name, d.title, d.org,
                DATE_FORMAT(d.report_date, '%Y-%m-%d') AS report_date
         FROM research_chunks c
         JOIN research_docs d ON d.id = c.doc_id
         WHERE c.user_id = ? AND c.id IN ({placeholders})""",
        [user_id, *chunk_ids],
    )


def delete_doc(doc_id, user_id):
    return exec_strict(
        "DELETE FROM research_docs WHERE id = ? AND user_id = ?",
        [doc_id, user_id],
    )


def search_chunks_by_keyword(
    user_id, *, text, stock_code=None, date_from=None, limit=20
):
    """
    入参是用户、去掉股票代码后的检索词、可选股票代码、条数。出参按全文分从高到低，最多 limit 条。
    这一路的分是自然语言模式的 TF-IDF，和余弦分不在一个量纲上，调用方只用名次。
    能确定股票时先收成这家公司的 doc_id，再取全文前 N 条。过滤和召回同时做，正确公司的段落才会进候选。
    """
    query_text = str(text or "").strip()
    if not query_text:
        return []
    owner_id = _positive_int(user_id)
    if owner_id is None:
        return []
    size = _limit_size(limit)

    params = [query_text, owner_id]
    doc_clause = ""
    if stock_code:
        docs = exec_strict(
            "SELECT id FROM research_docs WHERE user_id = ? AND stock_code = ?",
            [owner_id, stock_code],
        )
        doc_ids = [
            doc_id
            for doc_id in (_positive_int(doc["id"]) for doc in docs)
            if doc_id is not None
        ]
        if not doc_ids:
            return []
        doc_clause = f" AND c.doc_id IN ({', '.join(['?'] * len(doc_ids))})"
        params.extend(doc_ids)
    if date_from:
        doc_clause += " AND d.report_date >= ?"
        params.append(date_from)
    params.append(query_text)

    return exec_strict(
        f"""SELECT c.id, c.doc_id, c.chunk_type, c.page_start, c.page_end, c.heading, c.content,
                d.stock_code, d.stock_name, d.title, d.org,
                DATE_FORMAT(d.report_date, '%Y-%m-%d') AS report_date,
                MATCH(c.content) AGAINST (? IN NATURAL LANGUAGE MODE) AS score
         FROM research_chunks c
         JOIN research_docs d ON d.id = c.doc_id
         WHERE c.user_id = ?{doc_clause}
           AND MATCH(c.content) AGAINST (? IN NATURAL LANGUAGE MODE)
         ORDER BY score DESC
         LIMIT {size}""",
        params,
    )


def list_chunk_bodies(user_id):
    return exec_strict(
        """SELECT c.id, c.content, d.stock_code
         FROM research_chunks c
         JOIN research_docs d ON d.id = c.doc_id
         WHERE c.user_id = ?""",
        [user_id],
    )


def recover_stuck_docs():
    return exec_strict(
        "UPDATE research_docs SET status = 'failed', error_msg = '服务重启中断，请重试' WHERE status IN ('parsing', 'embedding')",
    )
