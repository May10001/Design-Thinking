from uuid import UUID
from fastapi import APIRouter, Header, Request
from fastapi.concurrency import run_in_threadpool
from app.schemas.review import ConfirmRequest
from app.services.review_service import confirm_import

router = APIRouter(prefix='/api/v1/imports', tags=['review'])

@router.post('/{import_id}/confirm')
async def confirm(import_id: UUID, payload: ConfirmRequest, request: Request, idempotency_key: str = Header(alias='Idempotency-Key',min_length=1,max_length=128)):
    raw_request = await request.json()
    return await run_in_threadpool(confirm_import, request.app.state.engine, request.app.state.user_id, import_id, payload, idempotency_key, raw_request)
