from alembic import context
from sqlalchemy import engine_from_config, pool
import os

config = context.config

def run_migrations_online():
    engine = config.attributes.get('connection_engine')
    owned = engine is None
    if owned:
        from app.core.database import create_database
        engine = create_database(os.getenv('DATABASE_URL', config.get_main_option('sqlalchemy.url')))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    if owned:
        engine.dispose()

if context.is_offline_mode():
    raise RuntimeError('Offline migration is not supported')
run_migrations_online()
