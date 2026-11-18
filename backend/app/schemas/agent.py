"""Shared request and result models for the in-process Agent adapter."""

from datetime import UTC, date, datetime
import re
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ObservedField = Literal["title", "description", "course_name", "external_ref", "deadline", "requirements"]
UncertainField = Literal["title", "description", "course_name", "external_ref", "deadline", "requirements", "task_identity"]
WarningCode = Literal["PRIVACY_RULE_APPLIED", "OCR_LOW_QUALITY", "SOURCE_TIME_UNKNOWN", "NO_TASK_FOUND"]
DueKind = Literal["NONE", "DATE", "DATETIME", "UNRESOLVED"]
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^\S+$")]


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Expected an IANA time zone") from None
    return value


Timezone = Annotated[str, AfterValidator(validate_timezone)]


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractionRequest(AgentModel):
    sanitized_text: Annotated[str, Field(min_length=1, max_length=20_000, pattern=r"\S")]
    reference_time: AwareDatetime
    source_time_known: Annotated[bool, Field(strict=True)]
    timezone: Timezone


class TaskFields(AgentModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)] | None
    course_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None
    external_ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None
    due_kind: DueKind
    due_date: date | None
    due_at: AwareDatetime | None
    timezone: Timezone
    requirements: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)] | None

    @field_validator("due_date", mode="before")
    @classmethod
    def validate_date_format(cls, value):
        if value is not None and not (isinstance(value, date) and not isinstance(value, datetime)) and not (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise ValueError("due_date must use YYYY-MM-DD")
        return date.fromisoformat(value) if isinstance(value, str) else value

    @field_validator("due_at", mode="before")
    @classmethod
    def validate_datetime_format(cls, value):
        if value is not None and not isinstance(value, datetime) and not (isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}[Tt ]", value)):
            raise ValueError("due_at must be an ISO 8601 datetime")
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value

    @model_validator(mode="after")
    def check_deadline(self) -> Self:
        if (self.due_date is not None) != (self.due_kind in {"DATE", "DATETIME"}):
            raise ValueError("due_date does not agree with due_kind")
        if (self.due_at is not None) != (self.due_kind == "DATETIME"):
            raise ValueError("due_at does not agree with due_kind")
        if self.due_at is not None:
            if self.due_at.astimezone(ZoneInfo(self.timezone)).date() != self.due_date:
                raise ValueError("due_at does not agree with due_date in the task time zone")
            self.due_at = self.due_at.astimezone(UTC)
        return self


class ExtractedTask(TaskFields):
    observed_fields: list[ObservedField]
    uncertain_fields: list[UncertainField]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    source_excerpt: Annotated[str, Field(min_length=1, max_length=1_000, pattern=r"\S")]

    @model_validator(mode="after")
    def check_field_flags(self) -> Self:
        observed = set(self.observed_fields)
        uncertain = set(self.uncertain_fields)
        if len(observed) != len(self.observed_fields) or len(uncertain) != len(self.uncertain_fields):
            raise ValueError("Field names must be unique")
        if observed & uncertain:
            raise ValueError("Observed and uncertain fields must be disjoint")
        if self.title is None and "title" not in uncertain:
            raise ValueError("A missing title must be marked uncertain")
        if (self.due_kind == "UNRESOLVED") != ("deadline" in uncertain):
            raise ValueError("An unresolved deadline must be marked uncertain")
        if self.due_kind in {"DATE", "DATETIME"} and "deadline" not in observed:
            raise ValueError("A resolved deadline must be marked observed")
        for field in ("title", "description", "course_name", "external_ref", "requirements"):
            value = getattr(self, field)
            if field in observed and value is None:
                raise ValueError("An observed field must contain a value")
            if value is not None and field not in observed | uncertain:
                raise ValueError("A supplied field must be marked observed or uncertain")
        return self


class AgentWarning(AgentModel):
    code: WarningCode
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ExtractionResult(AgentModel):
    tasks: Annotated[list[ExtractedTask], Field(max_length=20)]
    warnings: Annotated[list[AgentWarning], Field(max_length=4)]


class TaskView(TaskFields):
    id: Identifier
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    due_kind: Literal["NONE", "DATE", "DATETIME"]
    version: Annotated[int, Field(strict=True, ge=1)]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MatchRequestItem(AgentModel):
    draft_item_id: Identifier
    new_task: ExtractedTask
    candidate_tasks: list[TaskView]


class MatchBatchRequest(AgentModel):
    items: list[MatchRequestItem]


class MatchResultItem(AgentModel):
    draft_item_id: Identifier
    relation: Literal["SAME", "DIFFERENT", "UNCERTAIN"]
    matched_task_id: Identifier | None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    uncertain_fields: list[UncertainField]

    @model_validator(mode="after")
    def check_relation(self) -> Self:
        if (self.relation == "SAME") != (self.matched_task_id is not None):
            raise ValueError("Only SAME may specify a matched_task_id, and it must")
        if len(set(self.uncertain_fields)) != len(self.uncertain_fields):
            raise ValueError("Uncertain fields must be unique")
        return self


class MatchBatchResult(AgentModel):
    items: Annotated[list[MatchResultItem], Field(max_length=20)]
