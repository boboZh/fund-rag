from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, Response
from pydantic import ValidationError

from app.schemas.retrieve_dto import RetrieveRequest
from app.services.retrieve import retrieve
from app.utils.cancel import Cancelled, run_until_disconnect

router = APIRouter()

# 校验失败的句子与 retrieve() 抛出的一致，Node 把 detail 原样展示。
# loc 可能是字段名 user_id，也可能是请求里的别名 userId。
FIELD_MESSAGES = {
    "user_id": "缺少 user_id",
    "userId": "缺少 user_id",
    "query": "请输入问题",
    "min_score": "阈值需要是数字",
    "minScore": "阈值需要是数字",
    "stock_code": "股票代码需要是 6 位数字",
    "stockCode": "股票代码需要是 6 位数字",
    "date_from": "起始日期需要是 YYYY-MM-DD",
    "dateFrom": "起始日期需要是 YYYY-MM-DD",
}


def validation_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    names = [part for part in error.get("loc", ()) if isinstance(part, str)]
    field = names[-1] if names else ""
    if field == "mode":
        return f"检索模式只能是 vector、keyword、hybrid，收到 {error.get('input')}"
    if field in FIELD_MESSAGES:
        return FIELD_MESSAGES[field]
    return "参数无效"


@router.post("/retrieve")
async def retrieve_api(request: Request, payload: dict[str, Any] = Body()):
    """
    入参是 RetrieveRequest 校验后的下划线字段：query、user_id、top_k、mode、rerank、min_score、stock_code、date_from。
    请求 JSON 里的 userId、topK 由 CamelModel 的别名填进这些字段。
    出参与 Node 的 retrieve() 相同，键是驼峰。
    步骤：校验 JSON，不合法返回 400；检索放进线程池；浏览器断开则取消并返回 499。
    """
    try:
        body = RetrieveRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=validation_message(exc)) from exc
    try:
        # model_dump 用字段名。服务层不接收驼峰，也不接收 Pydantic 模型。
        data = body.model_dump()
        return await run_until_disconnect(
            request,
            retrieve,
            query=data["query"],
            user_id=data["user_id"],
            top_k=data["top_k"],
            mode=data["mode"],
            rerank=data["rerank"],
            min_score=data["min_score"],
            stock_code=data["stock_code"],
            date_from=data["date_from"],
        )
    except Cancelled:
        return Response(status_code=499)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
