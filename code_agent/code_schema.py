from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannedFile(_Base):
    path: str  # 프로젝트 루트 기준 상대 경로
    action: Literal["create", "modify"]
    purpose: str


class Task(_Base):
    id: str  # TASK-001
    title: str
    feature_ids: list[str] = Field(default_factory=list)
    ac_ids: list[str] = Field(default_factory=list)
    files: list[PlannedFile] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class WorkPlan(_Base):
    tasks: list[Task] = Field(default_factory=list)


class TaskStatus(_Base):
    task_id: str
    done: bool
    note: str | None = None


class CodeResult(_Base):
    summary: str
    task_status: list[TaskStatus] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
