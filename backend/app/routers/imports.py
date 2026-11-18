import asyncio
from uuid import UUID
import anyio
from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import ValidationError
from app.core.errors import BusinessError
from app.schemas.import_record import AnalyzeRequest, TextImportRequest
from app.services.input_service import MAX_IMAGE_BYTES, create_import, validate_image

router = APIRouter(prefix='/api/v1/imports', tags=['imports'])


@router.post('/text', status_code=201)
def import_text(payload: TextImportRequest, request: Request):
    return create_import(request.app.state.engine, request.app.state.user_id, payload.text, 'TEXT', payload.source_time, payload.timezone)


def _run_ocr(ocr, data):
    validate_image(data)
    if ocr is None:
        raise BusinessError('DEPENDENCY_NOT_READY','OCR 未配置',503)
    try:
        value, warnings = ocr.recognize(data)
    except BusinessError:
        raise
    except Exception as exc:
        raise BusinessError('OCR_FAILED','图片文字识别失败',502) from exc
    if not value or not value.strip():
        raise BusinessError('OCR_NO_TEXT','图片没有识别到文字',422)
    if any(w.get('code') != 'OCR_LOW_QUALITY' for w in warnings):
        raise BusinessError('OCR_FAILED','OCR 结果无效',502)
    return value, warnings


async def _bounded_ocr(app, data, state):
    async with app.state.ocr_slot:
        state['started'] = True
        return await anyio.to_thread.run_sync(_run_ocr,app.state.ocr,data)


def _consume_task(task):
    if not task.cancelled():
        task.exception()


@router.post('/image', status_code=201)
async def import_image(request: Request, file: UploadFile = File(...), source_time: str | None = Form(None), timezone: str | None = Form(None)):
    try:
        if file.content_type and file.content_type not in ('image/png','image/jpeg','application/octet-stream'):
            raise BusinessError('UNSUPPORTED_IMAGE_TYPE','仅支持 PNG 或 JPEG 图片',415)
        try:
            metadata=TextImportRequest(text='x',source_time=source_time,timezone=timezone)
        except ValidationError as exc:
            raise BusinessError('VALIDATION_ERROR','发布时间或时区不合法',422) from exc
        data=await file.read(MAX_IMAGE_BYTES+1)
        if len(data)>MAX_IMAGE_BYTES:
            raise BusinessError('INPUT_TOO_LARGE','图片大小超过限制',413)
    finally:
        await file.close()
    state={'started':False}
    task=asyncio.create_task(_bounded_ocr(request.app,data,state))
    task.add_done_callback(_consume_task)
    try:
        raw, warnings = await asyncio.wait_for(asyncio.shield(task), timeout=request.app.state.ocr_timeout)
    except TimeoutError as exc:
        if not state['started']:
            task.cancel()
        raise BusinessError('OCR_FAILED','图片识别超时，请重试',502) from exc
    except asyncio.CancelledError:
        if not state['started']:
            task.cancel()
        raise
    return await anyio.to_thread.run_sync(create_import,request.app.state.engine,request.app.state.user_id,raw,'IMAGE',metadata.source_time,metadata.timezone,warnings)


@router.post('/{import_id}/analyze')
async def analyze(import_id: UUID, payload: AnalyzeRequest, request: Request):
    return await request.app.state.analysis.analyze(request.app.state.user_id,import_id,payload.expected_version)


@router.get('/{import_id}')
async def detail(import_id: UUID, request: Request):
    return await request.app.state.analysis.detail(request.app.state.user_id,import_id)
