from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.task import TaskContent


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    draft_item_id: UUID
    action: Literal['CREATE', 'UPDATE', 'KEEP', 'SKIP']
    target_task_id: UUID | None = None
    expected_task_version: int | None = Field(default=None, ge=1, strict=True)
    final_task: TaskContent | None = None

    @model_validator(mode='after')
    def validate_action(self):
        supplied = self.model_fields_set
        required = {'CREATE': {'final_task'}, 'UPDATE': {'final_task','target_task_id','expected_task_version'}, 'KEEP': {'target_task_id','expected_task_version'}, 'SKIP': set()}[self.action]
        optional_fields = {'final_task','target_task_id','expected_task_version'}
        if (supplied & optional_fields) != required or any(getattr(self, k) is None for k in required):
            raise ValueError('操作所需字段不正确')
        return self


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_import_version: int = Field(ge=1, strict=True)
    items: list[ReviewItem] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def unique_items(self):
        drafts = [x.draft_item_id for x in self.items]
        targets = [x.target_task_id for x in self.items if x.target_task_id is not None]
        if len(set(drafts)) != len(drafts) or len(set(targets)) != len(targets):
            raise ValueError('草稿或目标任务不能重复')
        return self
