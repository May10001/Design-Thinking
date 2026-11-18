"""Read-only task snapshots; all queries are scoped to the current user."""
from sqlalchemy import text


def list_user_task_snapshots(engine, user_id):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            text('SELECT * FROM tasks WHERE user_id=:user'), {'user':user_id}
        ).mappings()]
