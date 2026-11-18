"""Backend-two integration app; import/OCR/Agent routes are provided by teammates."""
import os
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from app.core.current_user import configure_demo_user
from app.core.database import create_database
from app.core.errors import BusinessError
from app.routers import review, tasks


def create_app(engine=None, user_id=None):
    @asynccontextmanager
    async def lifespan(app):
        owned = engine is None
        if owned:
            app.state.engine = create_database(os.getenv('DATABASE_URL','sqlite:///./data/deadline_keeper.db'))
            with app.state.engine.connect() as connection:
                try:
                    revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
                except Exception as error:
                    raise RuntimeError('Database migration required: run alembic upgrade head') from error
                if revision != '0001_initial':
                    raise RuntimeError('Database migration is not at the expected revision')
            app.state.user_id = configure_demo_user(app.state.engine)
        try:
            yield
        finally:
            if owned:
                app.state.engine.dispose()
    app = FastAPI(title='Deadline Keeper — Backend 2', lifespan=lifespan)
    if engine is not None:
        app.state.engine, app.state.user_id = engine, user_id

    @app.middleware('http')
    async def request_identifier(request, call_next):
        request.state.request_id = f'req_{uuid4().hex}'
        response = await call_next(request)
        response.headers['X-Request-Id'] = request.state.request_id
        return response

    def error_response(request, code, message, status, details=None):
        return JSONResponse(status_code=status, headers={'X-Request-Id':request.state.request_id}, content={'error':{'code':code,'message':message,'request_id':request.state.request_id,'details':details or {}}})

    @app.exception_handler(BusinessError)
    async def business_error(request, error):
        return error_response(request,error.code,error.message,error.status,error.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        fields = [{'field':'.'.join(map(str,e['loc'])), 'message':e['msg']} for e in error.errors()]
        return error_response(request,'VALIDATION_ERROR','请求字段不合法',422,{'fields':fields})

    @app.exception_handler(OperationalError)
    async def database_error(request, error):
        if 'locked' in str(error.orig).lower() or 'busy' in str(error.orig).lower():
            return error_response(request,'DATABASE_BUSY','数据库繁忙，请稍后重试',503)
        return error_response(request,'DEPENDENCY_NOT_READY','数据库不可用',503)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        return error_response(request,'INTERNAL_ERROR','服务内部错误',500)

    app.include_router(tasks.router)
    app.include_router(review.router)
    return app

app = create_app()
