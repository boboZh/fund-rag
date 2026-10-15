from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.schemas.chat_dto import ChatQuery, InfoRequest
from app.services.llm_services import llm_stream_generator

router = APIRouter()


@router.post("/api/chat/stream")
async def chat(req: ChatQuery):
    return StreamingResponse(
        llm_stream_generator(req.question),
        media_type="text/event-stream",
    )


@router.post("/api/chat/info")
async def info(req: InfoRequest):
    return {"status": "success", "data": "aaa", "received_code": req.fund_code}


@router.get("/api/chat/info_simple")
async def info_simple():
    return {"data": "aaa"}
