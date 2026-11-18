from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.schemas.task import TaskContent

Observed = Literal['title', 'description', 'course_name', 'external_ref', 'deadline', 'requirements']
Uncertain = Literal['title', 'description', 'course_name', 'external_ref', 'deadline', 'requirements', 'task_identity']


class ExtractedTask(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(max_length=200)
    description: str | None = Field(max_length=2000)
    course_name: str | None = Field(max_length=100)
    external_ref: str | None = Field(max_length=100)
    due_kind: Literal['NONE', 'DATE', 'DATETIME', 'UNRESOLVED']
    due_date: date | None
    due_at: datetime | None
    timezone: str
    requirements: str | None = Field(max_length=4000)
    observed_fields: list[Observed]
    uncertain_fields: list[Uncertain]
    reason: str = Field(max_length=500)
    source_excerpt: str = Field(max_length=1000)

    @field_validator('due_date', mode='before')
    @classmethod
    def date_format(cls, value):
        return TaskContent.date_format(value)

    @field_validator('due_at', mode='before')
    @classmethod
    def datetime_format(cls, value):
        return TaskContent.datetime_format(value)

    @field_validator('title', mode='before')
    @classmethod
    def clean_title(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value

    @model_validator(mode='after')
    def dates_valid(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('无效时区')
        if self.due_kind == 'UNRESOLVED':
            if self.due_date is not None or self.due_at is not None:
                raise ValueError('未确定日期不能有日期或时间')
        else:
            data = self.model_dump(include=set(TaskContent.model_fields))
            data['title'] = self.title or 'x'
            validated = TaskContent.model_validate(data)
            self.due_at = validated.due_at
        return self


class MatchRequestItem(BaseModel):
    draft_item_id: str
    new_task: ExtractedTask
    candidate_tasks: list[dict] = Field(max_length=5)


class MatchBatchRequest(BaseModel):
    items: list[MatchRequestItem] = Field(max_length=20)


class MatchResultItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    draft_item_id: str
    relation: Literal['SAME', 'DIFFERENT', 'UNCERTAIN']
    matched_task_id: str | None
    reason: str = Field(max_length=500)
    uncertain_fields: list[Uncertain]

    @model_validator(mode='after')
    def check_relation(self):
        if (self.relation == 'SAME') != (self.matched_task_id is not None):
            raise ValueError('SAME 必须指定匹配任务')
        return self


class MatchBatchResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[MatchResultItem]
