from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ScheduleType = Literal["once", "daily", "weekly", "monthly", "interval"]
ScheduledTaskStatus = Literal["active", "paused", "completed", "archived"]
ConcurrencyPolicy = Literal["forbid", "allow"]
MisfirePolicy = Literal["coalesce", "skip"]


class ScheduledTaskBase(BaseModel):
    tenant_id: str
    agent_id: str
    title: str
    prompt: str
    description: str | None = None
    schedule_type: ScheduleType = "daily"
    schedule: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "Asia/Shanghai"
    rrule: str | None = None
    status: ScheduledTaskStatus = "active"
    concurrency_policy: ConcurrencyPolicy = "forbid"
    misfire_policy: MisfirePolicy = "coalesce"
    max_runs: int | None = None
    end_at: str | None = None
    source_session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    execution_mode: str | None = "agent"
    pipeline_steps: list[dict[str, Any]] | None = None


class ScheduledTaskCreateRequest(ScheduledTaskBase):
    pass


class ScheduledTaskUpdateRequest(BaseModel):
    tenant_id: str
    agent_id: str | None = None
    title: str | None = None
    prompt: str | None = None
    description: str | None = None
    schedule_type: ScheduleType | None = None
    schedule: dict[str, Any] | None = None
    timezone: str | None = None
    rrule: str | None = None
    status: ScheduledTaskStatus | None = None
    concurrency_policy: ConcurrencyPolicy | None = None
    misfire_policy: MisfirePolicy | None = None
    max_runs: int | None = None
    end_at: str | None = None
    metadata: dict[str, Any] | None = None
    execution_mode: str | None = None
    pipeline_steps: list[dict[str, Any]] | None = None


class ScheduledTaskDraftRequest(BaseModel):
    tenant_id: str
    agent_id: str
    session_id: str | None = None
    message: str
    timezone: str | None = None


class ScheduledTaskDraftRead(BaseModel):
    should_create: bool
    tenant_id: str
    agent_id: str
    title: str = ""
    prompt: str = ""
    description: str | None = None
    schedule_type: ScheduleType = "daily"
    schedule: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "Asia/Shanghai"
    rrule: str | None = None
    confidence: float = 0.0
    reason: str | None = None
    source_session_id: str | None = None


class ScheduledTaskRead(BaseModel):
    id: str
    tenant_id: str
    agent_id: str
    created_by_user_id: str
    title: str
    prompt: str
    description: str | None = None
    schedule_type: str
    schedule: dict[str, Any] = Field(default_factory=dict)
    timezone: str
    rrule: str | None = None
    status: str
    concurrency_policy: str
    misfire_policy: str
    max_runs: int | None = None
    end_at: str | None = None
    next_run_at: str | None = None
    last_run_at: str | None = None
    last_status: str | None = None
    run_count: int
    source_session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    execution_mode: str = "agent"
    pipeline_steps: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class ScheduledTaskRunRead(BaseModel):
    id: str
    tenant_id: str
    scheduled_task_id: str
    task_title: str | None = None
    task_status: str | None = None
    agent_id: str
    user_id: str
    session_id: str | None = None
    scheduled_for: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    result_summary: str | None = None
    error: str | None = None
    trace: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)
