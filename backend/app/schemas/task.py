from datetime import date, datetime, timezone
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskContent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(max_length=2000)
    course_name: str | None = Field(max_length=100)
    external_ref: str | None = Field(max_length=100)
    due_kind: Literal['NONE', 'DATE', 'DATETIME']
    due_date: date | None
    due_at: datetime | None
    timezone: str
    requirements: str | None = Field(max_length=4000)

    @field_validator('title', mode='before')
    @classmethod
    def trim_title(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('description', 'course_name', 'external_ref', 'requirements', mode='before')
    @classmethod
    def empty_to_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator('due_date', mode='before')
    @classmethod
    def date_format(cls, value):
        if value is not None and not (isinstance(value, date) and not isinstance(value, datetime)) and not (isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)):
            raise ValueError('日期必须使用 YYYY-MM-DD')
        return value

    @field_validator('due_at', mode='before')
    @classmethod
    def datetime_format(cls, value):
        if value is not None and not isinstance(value, datetime) and not (isinstance(value, str) and re.match(r'^\d{4}-\d{2}-\d{2}[Tt ]', value)):
            raise ValueError('截止时间必须为 ISO 8601 字符串')
        return value

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('无效时区')
        return value

    @model_validator(mode='after')
    def dates_consistent(self):
        if self.due_kind == 'NONE' and (self.due_date is not None or self.due_at is not None):
            raise ValueError('NONE 不允许日期或时间')
        if self.due_kind == 'DATE' and (self.due_date is None or self.due_at is not None):
            raise ValueError('DATE 需要日期且不允许时间')
        if self.due_kind == 'DATETIME':
            if self.due_date is None or self.due_at is None or self.due_at.utcoffset() is None:
                raise ValueError('DATETIME 需要日期和带时区的时间')
            if self.due_at.astimezone(ZoneInfo(self.timezone)).date() != self.due_date:
                raise ValueError('截止日期与时区换算后的时间不一致')
            self.due_at = self.due_at.astimezone(timezone.utc)
        return self


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1, strict=True)
    changes: dict

    @field_validator('changes')
    @classmethod
    def check_changes(cls, value):
        allowed = set(TaskContent.model_fields)
        if not value or set(value) - allowed:
            raise ValueError('changes 必须包含有效任务字段')
        deadline = {'due_kind', 'due_date', 'due_at', 'timezone'}
        if set(value) & deadline and not deadline <= set(value):
            raise ValueError('修改截止日期须提交完整日期组')
        return value
