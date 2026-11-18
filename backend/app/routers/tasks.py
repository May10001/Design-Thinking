from datetime import date
from uuid import UUID
from fastapi import APIRouter, Query, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session
from app.core.database import immediate_write_transaction
from app.core.errors import BusinessError
from app.schemas.task import TaskContent, TaskPatch
from app.services.task_service import FIELDS, get_task, list_tasks, task_view, update_task

router = APIRouter(prefix='/api/v1/tasks', tags=['tasks'])

@router.get('')
def tasks(request: Request, page: int = Query(default=1,ge=1), page_size: int = Query(default=20,ge=1,le=100), q: str | None = Query(default=None,max_length=200), course_name: str | None = Query(default=None,max_length=100), due_from: date | None = None, due_to: date | None = None):
    if due_from and due_to and due_from > due_to:
        raise BusinessError('VALIDATION_ERROR','日期下界不能晚于上界',422)
    with Session(request.app.state.engine) as session:
        return list_tasks(session,request.app.state.user_id,page,page_size,q,course_name,due_from,due_to)

@router.get('/{task_id}')
def task_detail(task_id: UUID, request: Request):
    with Session(request.app.state.engine) as session:
        return task_view(get_task(session,request.app.state.user_id,task_id))

@router.patch('/{task_id}')
def task_patch(task_id: UUID, payload: TaskPatch, request: Request):
    with immediate_write_transaction(request.app.state.engine) as session:
        old = get_task(session,request.app.state.user_id,task_id)
        try:
            merged = TaskContent.model_validate({**{key:old[key] for key in FIELDS}, **payload.changes})
        except ValidationError as error:
            raise BusinessError('VALIDATION_ERROR','任务字段不合法',422,{'fields':[{'field':'.'.join(map(str, e['loc'])), 'message':e['msg']} for e in error.errors()]}) from error
        return update_task(session,request.app.state.user_id,task_id,payload.expected_version,merged)
