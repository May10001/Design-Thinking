"""Create the five business tables defined in the backend specification."""
from pathlib import Path
from alembic import op

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = (Path(__file__).resolve().parents[1] / 'schema_v1.sql').read_text(encoding='utf-8')
    for statement in sql.split(';'):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for table in ('draft_items', 'review_batches', 'imports', 'tasks', 'users'):
        op.execute(f'DROP TABLE {table}')
