from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator


class TextImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=20000)
    source_time: datetime | None = None
    timezone: str | None = None

    @field_validator('text', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('source_time', mode='before')
    @classmethod
    def timestamp_is_string(cls, value):
        if value is not None and not isinstance(value, datetime) and not (isinstance(value, str) and re.match(r'^\d{4}-\d{2}-\d{2}[Tt ]', value)):
            raise ValueError('发布时间必须是带时区的 ISO 8601 时间')
        return value

    @field_validator('source_time')
    @classmethod
    def aware_time(cls, value):
        if value is not None:
            if value.utcoffset() is None:
                raise ValueError('发布时间必须包含时区')
            return value.astimezone(timezone.utc)
        return value

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError('无效时区') from None
        return value


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1, strict=True)
