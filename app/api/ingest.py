from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import ValidationError

from app.schemas.ingest_dto import IngestRequest, IngestResponse
from app.services.ingest import ingest

router = APIRouter()

# 校验失败时的文案与 Node 的 readMeta 一致，调用方可以直接展示。
# Pydantic 报错的 loc 用的是请求里的别名（stockCode），不是列名 stock_code。两套都收。
FIELD_MESSAGES = {
    "user_id": "userId 需要是正整数",
    "userId": "userId 需要是正整数",
    "file_hash": "请先选择 PDF",
    "fileHash": "请先选择 PDF",
    "file_name": "缺少文件名",
    "fileName": "缺少文件名",
    "file_path": "file_path 过长",
    "filePath": "file_path 过长",
    "stock_code": "股票代码需要是 6 位数字",
    "stockCode": "股票代码需要是 6 位数字",
    "stock_name": "请填写股票名称",
    "stockName": "请填写股票名称",
    "title": "请填写研报标题",
    "org": "请填写机构",
    "report_date": "请填写有效的报告日期",
    "reportDate": "请填写有效的报告日期",
    "max_chars": "maxChars 需要在 400 到 1200 之间",
    "maxChars": "maxChars 需要在 400 到 1200 之间",
    "overlap_paragraphs": "overlapParagraphs 只能是 0 或 1",
    "overlapParagraphs": "overlapParagraphs 只能是 0 或 1",
    "page_count": "pageCount 无效",
    "pageCount": "pageCount 无效",
    "paragraphs": "没有可用段落",
    "text": "段落缺少正文",
    "page": "段落页码无效",
    "kind": "段落类型无效",
    "heading": "段落标题无效",
}


def validation_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    names = [part for part in error.get("loc", ()) if isinstance(part, str)]
    for name in reversed(names):
        if name in FIELD_MESSAGES:
            return FIELD_MESSAGES[name]
    return "参数无效"


@router.post("/ingest", response_model=IngestResponse)
def ingest_api(payload: dict[str, Any] = Body()):
    """
    入参是驼峰 JSON：元数据和已经按阅读顺序排好的段落。出参是文档和 duplicate，键也是驼峰。
    用 def 而不是 async def，FastAPI 会放进线程池。切块和向量化会打外部接口，不能占着事件循环。
    校验失败返回 400。入库过程中的失败写在文档 status 上，仍然返回 200，调用方看 status。
    """
    try:
        body = IngestRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=validation_message(exc)) from exc
    try:
        # model_dump 用列名。服务层不接收驼峰，也不接收 Pydantic 模型。
        return ingest(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
