import math
import re
from app.utils.cache import writeCache
from app.mock.doc import withoutToc


# 拼接纯文本内容
def content_of(items, type):
    return "\n".join([item["text"] for item in items])


def content_length(items, type):
    return len(content_of(items, type))


# 按max_chars，以标点符号分隔，拼一起超过max_chars的小短句，当成一小块放进groups
def pack_long_text(paragraph, max_chars):
    if paragraph["kind"] != "text" or len(paragraph["text"]) <= max_chars:
        return [paragraph]

    # r"(?<=[。！？；])" 只标记位置，不吃掉标点。切开后 。！？； 仍留在前一段末尾。
    pieces = [
        piece.strip()
        for piece in re.split(r"(?<=[。！？；])", paragraph["text"])
        if piece.strip()
    ]

    if len(pieces) <= 1:
        return [paragraph]

    groups: list = []
    buffer: str = ""

    def push():
        nonlocal buffer
        if not buffer:
            return
        groups.append({**paragraph, "text": buffer})
        buffer = ""

    for piece in pieces:
        if buffer and len(buffer) + len(piece) > max_chars:
            push()
        buffer += piece

    push()

    return groups


# 整合切块信息
def to_chunk(items, type, chunkIndex):
    content = content_of(items, type)
    return {
        "chunkIndex": chunkIndex,
        "chunkType": type,
        "pageStart": items[0]["page"],
        "pageEnd": items[len(items) - 1]["page"],
        "heading": items[0]["heading"] or None,
        "paragraphCount": len(items),
        "content": content,
        "charCount": len(content),
    }


def split_paragraphs(paragraphs, config=None):
    config = config or {}
    max_chars = config.get("max_chars", 800)
    overlap_paragraphs = config.get("overlap_paragraphs", 1)

    if overlap_paragraphs not in (0, 1):
        raise ValueError("overlap_paragraphs 只能是 0 或 1")
    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, (int, float))
        or not math.isfinite(max_chars)
        or max_chars < 50
    ):
        raise ValueError("max_chars 过小")

    chunks: list = []
    buffer: list = []
    buffer_type: str = "text"

    def emit(items, type):
        if not len(items):
            return
        chunks.append(to_chunk(items, type, len(chunks)))

    def flush(carry):
        nonlocal buffer, buffer_type
        emit(buffer, buffer_type)
        buffer = [carry] if carry else []

    def enqueue(paragraph):
        nonlocal buffer, buffer_type
        type: str = paragraph["kind"] or "text"

        # 表格连续行合成一块，不受合并上限限制。
        if type == "table":
            if len(buffer) and buffer_type != "table":
                emit(buffer, buffer_type)
            if buffer_type != "table":
                buffer = []

            buffer_type = "table"
            buffer = [*buffer, paragraph]
            return

        if type == "summary":
            if len(buffer):
                emit(buffer, buffer_type)
            buffer = []
            emit([paragraph], type)
            return

        headingChanged = len(buffer) > 0 and (buffer[0]["heading"] or None) != (
            paragraph["heading"] or None
        )
        typeChanged = len(buffer) > 0 and buffer_type != type
        if headingChanged or typeChanged:
            flush(None)

        pieces = pack_long_text(paragraph, max_chars)
        for piece in pieces:
            # 第一段，直接append
            if not len(buffer):
                buffer = [piece]
                buffer_type = type
                continue
            # 拼上长度小于max_chars，直接拼
            if content_length([*buffer, piece], type) <= max_chars:
                buffer = [*buffer, piece]
                continue

            last = buffer[len(buffer) - 1]
            canOverlap = (
                overlap_paragraphs == 1
                and type == "text"
                and len(last["text"]) < max_chars
                and content_length([last, piece], type) <= max_chars
                and (last["heading"] or None) == (piece["heading"] or None)
            )
            emit(buffer, type)
            buffer = [last, piece] if canOverlap else [piece]
            buffer_type = type

    for paragraph in paragraphs:
        enqueue(paragraph)

    if len(buffer):
        emit(buffer, buffer_type)

    return [{**chunk, "chunkIndex": index} for index, chunk in enumerate(chunks)]


# result = split_paragraphs(
#     withoutToc["paragraphs"], {"max_chars": 800, "overlap_paragraphs": 1}
# )

# writeCache("chunks-result", result)
