from fastapi import APIRouter, Request, Response

from app.services.retrieve import retrieve
from app.utils.cancel import Cancelled, run_until_disconnect

router = APIRouter()


@router.post("/retrieve")
async def retrieve_api(body: RetrieveDto, request: Request):
    try:
        return await run_until_disconnect(
            request,
            retrieve,
            query=body.query,
            userId=body.userId,
            topK=body.topK,
            mode=body.mode,
            rerank=body.rerank,
            minScore=body.minScore,
            stockCode=body.stockCode,
            dateFrom=body.dateFrom,
        )
    except Cancelled:
        return Response(status_code=499)
