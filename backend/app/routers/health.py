from fastapi import APIRouter, Request
from sqlalchemy import text
import anyio
from app.core.errors import BusinessError

router=APIRouter(tags=['health'])

@router.get('/health/live')
def live():
    return {'status':'ok'}

@router.get('/health/ready')
async def ready(request: Request):
    def database_ok():
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text('SELECT 1')).scalar_one()
            return True
        except Exception:
            return False
    checks={'database':'ok' if await anyio.to_thread.run_sync(database_ok) else 'failed','ocr':'ok' if request.app.state.ocr is not None else 'failed','config':'ok' if request.app.state.agent is not None else 'failed'}
    if 'failed' in checks.values():
        raise BusinessError('DEPENDENCY_NOT_READY','本地依赖未就绪',503,{'checks':checks})
    return {'status':'ready'}
