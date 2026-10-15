from datetime import date, datetime
from typing import Literal

from pydantic import Field
from app.schemas.base import CamelModel


class Paragraph(CamelModel):
    text: str = Field(min_length=1)
    heading: str | None = None
    kind: Literal["text", "table", "summary"] = "text"
    page: int = Field(ge=1)


class IngestRequest(CamelModel):
    user_id: int = Field(gt=0)
    file_hash: str = Field(pattern=r"^[a-f0-9]{32}$")
    file_name: str = Field(min_length=1, max_length=255)
    # Node 已经把 PDF 写到磁盘。空字符串只用于直接调这个接口、还没有路径的情况。
    file_path: str = Field(default="", max_length=500)
    stock_code: str = Field(pattern=r"^\d{6}$")
    stock_name: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=255)
    org: str = Field(min_length=1, max_length=100)
    report_date: date
    max_chars: int = Field(default=800, ge=400, le=1200)
    overlap_paragraphs: Literal[0, 1] = 1
    # 版面结果里的 PDF 总页数。不传时用段落上的最大页码。
    page_count: int | None = Field(default=None, ge=1)
    # 为 true 时，同一用户和哈希已有文档也重新切块。Node 的入库、重切、重试都要带上。
    replace: bool = False
    paragraphs: list[Paragraph] = Field(min_length=1)


class IngestDocOut(CamelModel):
    id: int
    user_id: int
    file_hash: str
    file_name: str
    file_path: str = ""
    stock_code: str
    stock_name: str
    report_date: str | date
    title: str
    org: str
    page_count: int | None = None
    max_chars: int
    overlap_paragraphs: int
    embed_model: str | None = None
    status: str
    error_msg: str | None = None
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class IngestResponse(CamelModel):
    doc: IngestDocOut
    duplicate: bool
