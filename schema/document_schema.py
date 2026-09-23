# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: 2026-09-23

from pydantic import BaseModel, Field


class DocumentUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(max_length=100)
    enabled: bool


class ACLUpdate(BaseModel):
    permissions: list[dict[str, str]] = Field(default_factory=list)
