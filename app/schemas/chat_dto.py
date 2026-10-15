
from app.schemas.base import CamelModel


class ChatQuery(CamelModel):
    question: str


class InfoRequest(CamelModel):
    fund_code: str
