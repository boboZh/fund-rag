from app.services.chunk import split_paragraphs


def para(text, heading, kind="text", page=1):
    return {"text": text, "heading": heading, "kind": kind, "page": page}


def test_short_paragraphs_stay_one_chunk():
    """短段落合并后，切点仍落在段落之间"""
    chunks = split_paragraphs(
        [
            para("产能继续释放。", "成长"),
            para("需求保持旺盛。", "成长"),
            para("成本开始下行。", "成长"),
        ],
        {"max_chars": 800, "overlap_paragraphs": 0},
    )
    assert len(chunks) == 1
    assert chunks[0]["content"] == "产能继续释放。\n需求保持旺盛。\n成本开始下行。"
    assert chunks[0]["paragraphCount"] == 3


def test_new_heading_starts_new_chunk():
    """遇到新小节就开新块"""
    chunks = split_paragraphs(
        [para("产能继续释放。", "成长"), para("目标价上调到25元。", "估值")],
        {"max_chars": 800, "overlap_paragraphs": 1},
    )
    assert [chunk["heading"] for chunk in chunks] == ["成长", "估值"]
    assert chunks[1]["content"] == "目标价上调到25元。"


def test_long_paragraph_splits_on_period():
    """超长段落按句号拆开，不从字中间截断"""
    first = f"{'收入增长' * 8}。"
    second = f"{'利润改善' * 8}。"
    third = f"{'估值合理' * 8}。"
    chunks = split_paragraphs(
        [para(f"{first}{second}{third}", "盈利")],
        {"max_chars": len(first) + len(second), "overlap_paragraphs": 0},
    )
    assert [chunk["content"] for chunk in chunks] == [f"{first}{second}", third]
    for chunk in chunks:
        assert chunk["content"].endswith("。")


def test_overlap_is_previous_paragraph():
    """重叠是完整的上一段，而不是回退若干个字"""
    first = f"第一段{'产能' * 12}。"
    second = f"第二段{'需求' * 12}。"
    third = f"第三段{'成本' * 12}。"
    chunks = split_paragraphs(
        [para(first, "成长"), para(second, "成长"), para(third, "成长")],
        {"max_chars": len(first) + 1 + len(second), "overlap_paragraphs": 1},
    )
    assert chunks[0]["content"] == f"{first}\n{second}"
    assert chunks[1]["content"].startswith(second)
    assert third in chunks[1]["content"]


def test_table_and_summary_are_own_chunks():
    """表格和首页核心数据单独成块"""
    chunks = split_paragraphs(
        [
            para("评级 买入\n目标价 25", "核心数据", "summary"),
            para("收入 10 20 30\n利润 1 2 3", "财务", "table"),
            para("主业仍然稳定。", "概述"),
        ],
        {"max_chars": 800, "overlap_paragraphs": 1},
    )
    assert [chunk["chunkType"] for chunk in chunks] == ["summary", "table", "text"]
    assert chunks[1]["content"] == "收入 10 20 30\n利润 1 2 3"


def test_table_stays_one_chunk_over_limit():
    """一整张表即使超过字数上限也保持一块"""
    row = "主营收入（百万元） | 1,741 | 2,535 | 5,119 | 7,179"
    chunks = split_paragraphs(
        [
            para(f"{row}\n{row}\n{row}", "盈利预测", "table"),
            para("维持买入评级。", "盈利预测"),
        ],
        {"max_chars": 50, "overlap_paragraphs": 1},
    )
    assert chunks[0]["chunkType"] == "table"
    assert len(chunks[0]["content"].split("\n")) == 3
    assert chunks[1]["content"] == "维持买入评级。"
    assert not chunks[1]["content"].startswith(row)
