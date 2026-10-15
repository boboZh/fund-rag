from typing import Literal

from pydantic import Field, field_validator

from app.schemas.base import CamelModel


class RetrieveRequest(CamelModel):
    # 空字符串和 null 都表示「这次不带这个条件」，服务里再决定默认值。
    query: str
    user_id: int = Field(gt=0)
    top_k: float | None = None
    mode: Literal["vector", "keyword", "hybrid"] = "vector"
    rerank: bool = False
    min_score: float | None = None
    stock_code: str | None = None
    date_from: str | None = None

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("请输入问题")
        return text

    # mode="before" 表示在类型转换之前执行：原始值先经过这个函数，再交给 float | None、str | None 做类型校验。
    @field_validator("top_k", "min_score", "stock_code", "date_from", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        if value is None or value == "":
            return None
        return value
