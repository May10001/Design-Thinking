import os
from uuid import UUID
from zoneinfo import ZoneInfo
from sqlalchemy import text
from app.services.common import utc_now


def configure_demo_user(engine):
    user_id = str(UUID(os.getenv('DEMO_USER_ID', '00000000-0000-4000-8000-000000000001')))
    timezone = os.getenv('DEFAULT_TIMEZONE', 'Asia/Shanghai')
    ZoneInfo(timezone)
    with engine.begin() as conn:
        conn.execute(text('INSERT OR IGNORE INTO users (id,display_name,timezone,created_at) VALUES (:id,:name,:timezone,:now)'), {'id':user_id, 'name':os.getenv('DEMO_USER_NAME','演示用户'), 'timezone':timezone, 'now':utc_now()})
    return user_id
