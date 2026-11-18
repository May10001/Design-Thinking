import io
import json
import warnings as pywarnings
from uuid import uuid4
from PIL import Image, UnidentifiedImageError
from sqlalchemy import text
from app.core.database import immediate_write_transaction
from app.core.errors import BusinessError, not_found
from app.services.common import canonical_json, utc_now
from app.services.privacy_filter import PrivacyFilter

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_TEXT_CHARS = 20_000


def validate_image(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise BusinessError('INPUT_TOO_LARGE', '图片大小超过限制', 413)
    try:
        with pywarnings.catch_warnings():
            pywarnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ('PNG', 'JPEG'):
                    raise BusinessError('UNSUPPORTED_IMAGE_TYPE', '仅支持 PNG 或 JPEG 图片', 415)
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise BusinessError('INPUT_TOO_LARGE', '图片像素超过限制', 413)
                if getattr(image, 'n_frames', 1) != 1:
                    raise BusinessError('INVALID_IMAGE', '不支持动态图片', 422)
                image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except BusinessError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise BusinessError('INPUT_TOO_LARGE', '图片像素超过限制', 413) from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise BusinessError('INVALID_IMAGE', '图片无法解码', 422) from exc


def create_import(engine, user_id, raw_text, input_type, source_time=None, timezone=None, warnings=None):
    if len(raw_text.strip()) > MAX_TEXT_CHARS:
        raise BusinessError('INPUT_TOO_LARGE', '文字超过长度限制', 413)
    sanitized, changed = PrivacyFilter().apply(raw_text.strip())
    if not sanitized:
        raise BusinessError('OCR_NO_TEXT' if input_type == 'IMAGE' else 'VALIDATION_ERROR', '没有可用文字', 422)
    if len(sanitized) > MAX_TEXT_CHARS:
        raise BusinessError('INPUT_TOO_LARGE', '文字超过长度限制', 413)
    warnings = list(warnings or [])
    if changed:
        warnings.append({'code':'PRIVACY_RULE_APPLIED','message':'已对常见敏感信息进行规则脱敏'})
    now = utc_now()
    source = source_time.isoformat().replace('+00:00', 'Z') if source_time else None
    record = {'id':str(uuid4()),'user_id':user_id,'input_type':input_type,'sanitized_text':sanitized,'source_time':source,'reference_time':source or now,'status':'READY','version':1,'created_at':now,'updated_at':now,'warnings_json':canonical_json(warnings)}
    with immediate_write_transaction(engine) as session:
        current = session.execute(text('SELECT timezone FROM users WHERE id=:id'), {'id':user_id}).mappings().first()
        if not current:
            raise not_found()
        record['timezone'] = timezone or current['timezone']
        columns = ','.join(record)
        session.execute(text(f"INSERT INTO imports ({columns}) VALUES ({','.join(':'+key for key in record)})"), record)
    return import_created(record)


def import_created(record):
    return {**{key:record[key] for key in ('id','input_type','status','version','sanitized_text','source_time','reference_time','timezone','created_at','updated_at')},'warnings':json.loads(record['warnings_json'])}
