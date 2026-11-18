"""Import analysis: short DB transactions around async model calls."""
import asyncio
import json
from datetime import datetime
from uuid import uuid4
import anyio
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from app.adapters.agent_adapter import AgentError
from app.core.database import immediate_write_transaction
from app.core.errors import BusinessError, not_found
from app.schemas.agent import ExtractionRequest, ExtractionResult
from app.services.common import canonical_json, utc_now
from app.services.input_service import import_created
from app.services.matching_service import MatchingService
from app.services.privacy_filter import PrivacyFilter


def _load(session, user_id, import_id):
    row = session.execute(text('SELECT * FROM imports WHERE id=:id AND user_id=:user'), {'id':str(import_id),'user':user_id}).mappings().first()
    if row is None:
        raise not_found()
    return dict(row)


def recover_interrupted(engine):
    with immediate_write_transaction(engine) as session:
        session.execute(text("UPDATE imports SET status='FAILED',version=version+1,last_error_code='PROCESS_INTERRUPTED',last_error_message='分析中断，请重试',updated_at=:now WHERE status='ANALYZING'"), {'now':utc_now()})


def _claim(engine, user_id, import_id, version):
    with immediate_write_transaction(engine) as session:
        record = _load(session, user_id, import_id)
        if record['status'] in ('REVIEW_READY','COMPLETED'):
            return None
        if record['status'] == 'ANALYZING':
            raise BusinessError('ANALYSIS_IN_PROGRESS','通知正在分析',409)
        if record['version'] != version:
            raise BusinessError('IMPORT_VERSION_CONFLICT','通知已更新，请刷新',409,{'current_version':record['version']})
        result = session.execute(text("UPDATE imports SET status='ANALYZING',version=version+1,last_error_code=NULL,last_error_message=NULL,updated_at=:now WHERE id=:id AND user_id=:u AND version=:v AND status IN ('READY','FAILED')"), {'id':str(import_id),'u':user_id,'v':version,'now':utc_now()})
        if result.rowcount != 1:
            raise BusinessError('IMPORT_VERSION_CONFLICT','通知已更新，请刷新',409)
        record['version'] += 1
        return record


def _finish(engine, user_id, record, extracted, suggestions, agent_warnings):
    now=utc_now()
    with immediate_write_transaction(engine) as session:
        current = _load(session,user_id,record['id'])
        if current['status'] != 'ANALYZING' or current['version'] != record['version']:
            raise BusinessError('IMPORT_VERSION_CONFLICT','通知已更新，请刷新',409)
        for ordinal, ((draft_id, item), suggestion) in enumerate(zip(extracted, suggestions, strict=True), 1):
            row={'id':draft_id,'import_id':record['id'],'user_id':user_id,'ordinal':ordinal,'extracted_json':item.model_dump_json(),'proposed_task_json':canonical_json(suggestion['proposed_task']),'suggested_action':suggestion['suggested_action'],'matched_task_id':suggestion['matched_task_id'],'matched_task_version':suggestion['matched_task_version'],'match_details_json':canonical_json(suggestion['match_details']),'created_at':now,'updated_at':now}
            session.execute(text(f"INSERT INTO draft_items ({','.join(row)}) VALUES ({','.join(':'+key for key in row)})"), row)
        all_warnings = json.loads(current['warnings_json'])
        known = {w['code'] for w in all_warnings}
        for warning in agent_warnings:
            item=warning.model_dump(mode='json')
            if item['code'] not in known:
                all_warnings.append(item)
                known.add(item['code'])
        result=session.execute(text("UPDATE imports SET status=:status,version=version+1,warnings_json=:warnings,analyzed_at=:now,updated_at=:now WHERE id=:id AND user_id=:u AND status='ANALYZING' AND version=:v"), {'status':'REVIEW_READY' if extracted else 'COMPLETED','warnings':canonical_json(all_warnings),'now':now,'id':record['id'],'u':user_id,'v':record['version']})
        if result.rowcount != 1:
            raise BusinessError('IMPORT_VERSION_CONFLICT','通知已更新，请刷新',409)


def _fail(engine, user_id, record, code, message):
    with immediate_write_transaction(engine) as session:
        session.execute(text("UPDATE imports SET status='FAILED',version=version+1,last_error_code=:code,last_error_message=:message,updated_at=:now WHERE id=:id AND user_id=:u AND status='ANALYZING' AND version=:v"), {'code':code,'message':message,'now':utc_now(),'id':record['id'],'u':user_id,'v':record['version']})


def _detail(engine, user_id, import_id, repair=False):
    if repair:
        with engine.connect() as connection:
            status = _load(connection,user_id,import_id)['status']
        if status == 'ANALYZING':
            with immediate_write_transaction(engine) as session:
                session.execute(text("UPDATE imports SET status='FAILED',version=version+1,last_error_code='PROCESS_INTERRUPTED',last_error_message='分析中断，请重试',updated_at=:now WHERE id=:id AND user_id=:u AND status='ANALYZING'"), {'id':str(import_id),'u':user_id,'now':utc_now()})
    with engine.connect() as session:
        session.exec_driver_sql('BEGIN')
        record = _load(session,user_id,import_id)
        rows = session.execute(text('SELECT * FROM draft_items WHERE import_id=:id AND user_id=:u ORDER BY ordinal'), {'id':str(import_id),'u':user_id}).mappings().all()
        items=[]
        for row in rows:
            extracted = json.loads(row['extracted_json'])
            items.append({'id':row['id'],'ordinal':row['ordinal'],'status':row['status'],'suggested_action':row['suggested_action'],'matched_task_id':row['matched_task_id'],'matched_task_version':row['matched_task_version'],'proposed_task':json.loads(row['proposed_task_json']),'match_details':json.loads(row['match_details_json']),'source_excerpt':extracted.get('source_excerpt',''),'final_action':row['final_action'],'final_task':json.loads(row['finalized_task_json']) if row['finalized_task_json'] else None,'confirmed_at':row['confirmed_at']})
        return {**import_created(record),'analyzed_at':record['analyzed_at'],'last_error':{'code':record['last_error_code'],'message':record['last_error_message']} if record['last_error_code'] else None,'pending_count':sum(item['status']=='PENDING' for item in items),'items':items}


class AnalysisService:
    def __init__(self, engine, agent, timeout=90):
        self.engine, self.agent, self.timeout = engine, agent, timeout
        self.active=set()
        self.lock=asyncio.Lock()
        self.slots=asyncio.Semaphore(2)

    async def detail(self, user_id, import_id):
        async with self.lock:
            return await anyio.to_thread.run_sync(_detail,self.engine,user_id,str(import_id),str(import_id) not in self.active)

    async def analyze(self, user_id, import_id, version):
        import_id=str(import_id)
        record=None
        try:
            async with asyncio.timeout(self.timeout):
                async with self.lock:
                    if import_id in self.active:
                        raise BusinessError('ANALYSIS_IN_PROGRESS','通知正在分析',409)
                    # Repair stranded ANALYZING records before an attempted claim.
                    await anyio.to_thread.run_sync(_detail,self.engine,user_id,import_id,True)
                    record=await anyio.to_thread.run_sync(_claim,self.engine,user_id,import_id,version)
                    if record is not None:
                        self.active.add(import_id)
                if record is None:
                    return await self.detail(user_id,import_id)
                if self.agent is None:
                    raise BusinessError('DEPENDENCY_NOT_READY','模型服务未配置',503)
                async with self.slots:
                    request=ExtractionRequest(sanitized_text=record['sanitized_text'], reference_time=datetime.fromisoformat(record['reference_time'].replace('Z','+00:00')),source_time_known=record['source_time'] is not None,timezone=record['timezone'])
                    try:
                        raw = await self.agent.extract_tasks(request)
                    except (AgentError, TimeoutError, BusinessError):
                        raise
                    except Exception as exc:
                        raise BusinessError('LLM_UPSTREAM_ERROR','模型分析失败，请重试',502) from exc
                    try:
                        result = ExtractionResult.model_validate(raw)
                    except (ValidationError, ValueError) as exc:
                        raise BusinessError('LLM_INVALID_OUTPUT','模型提取结果无效',502) from exc
                    extracted=[(str(uuid4()),item) for item in result.tasks]
                    suggestions=await MatchingService(self.engine,self.agent,PrivacyFilter().task).suggest(user_id,extracted)
                    await anyio.to_thread.run_sync(_finish,self.engine,user_id,record,extracted,suggestions,result.warnings)
                return await self.detail(user_id,import_id)
        except BaseException as exc:
            if record is not None:
                if isinstance(exc, TimeoutError):
                    error=BusinessError('ANALYSIS_TIMEOUT','分析超时，请重试',504)
                elif isinstance(exc, BusinessError):
                    error=exc
                elif isinstance(exc, OperationalError):
                    busy = 'locked' in str(exc.orig).lower() or 'busy' in str(exc.orig).lower()
                    error=BusinessError('DATABASE_BUSY' if busy else 'DEPENDENCY_NOT_READY','数据库不可用，请重试',503)
                elif isinstance(exc, SQLAlchemyError):
                    error=BusinessError('DEPENDENCY_NOT_READY','数据库不可用，请重试',503)
                elif isinstance(exc, AgentError):
                    code=exc.code
                    mapped=code if code in ('INPUT_TOO_COMPLEX','LLM_INVALID_OUTPUT','LLM_UPSTREAM_ERROR') else 'DEPENDENCY_NOT_READY'
                    error=BusinessError(mapped,'模型分析失败，请重试',422 if mapped=='INPUT_TOO_COMPLEX' else (503 if mapped=='DEPENDENCY_NOT_READY' else 502))
                elif isinstance(exc, asyncio.CancelledError):
                    error=BusinessError('PROCESS_INTERRUPTED','分析中断，请重试',500)
                else:
                    error=BusinessError('INTERNAL_ERROR','服务内部错误',500)
                await anyio.to_thread.run_sync(_fail,self.engine,user_id,record,error.code,error.message)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise error from exc
            if isinstance(exc, TimeoutError):
                raise BusinessError('ANALYSIS_TIMEOUT','分析超时，请重试',504) from exc
            raise
        finally:
            if record is not None:
                async with self.lock:
                    self.active.discard(import_id)
