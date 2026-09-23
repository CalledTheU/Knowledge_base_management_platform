# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: 2026-09-23

from pydantic import BaseModel, Field


class DepartmentInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = None


class RoleInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    permissions: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    department_id: str | None = None
    roles: list[str] = Field(min_length=1)


class UserStatus(BaseModel):
    active: bool


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=200)
    department_id: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["user"])
