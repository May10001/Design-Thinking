from uuid import uuid4
from sqlalchemy import text
from app.core.errors import BusinessError, not_found
from app.schemas.task import TaskContent
from app.services.common import canonical_json, digest, normalize, utc_now

FIELDS = tuple(TaskContent.model_fields)
PUBLIC = FIELDS + ('id','version','created_at','updated_at')


def task_view(row):
    return {key: row[key] for key in PUBLIC}


def derived(content):
    data = content.model_dump(mode='json')
    normalized = {key: normalize(value) if isinstance(value, str) and key not in {'due_kind','due_date','due_at','timezone'} else value for key, value in data.items()}
    identity = [normalize(data['course_name']), normalize(data['external_ref'])]
    return {**data, 'title_normalized':normalize(data['title']), 'course_normalized':identity[0], 'external_ref_normalized':identity[1], 'content_hash':digest(normalized), 'identity_key':digest(identity) if all(identity) else None}


def create_task(session, user_id, content):
    data = {**derived(content), 'id':str(uuid4()), 'user_id':user_id, 'version':1, 'created_at':utc_now()}
    data['updated_at'] = data['created_at']
    columns = ','.join(data)
    session.execute(text(f"INSERT INTO tasks ({columns}) VALUES ({','.join(':'+key for key in data)})"), data)
    return task_view(data)


def get_task(session, user_id, task_id):
    row = session.execute(text('SELECT * FROM tasks WHERE id=:id AND user_id=:user'), {'id':str(task_id),'user':user_id}).mappings().first()
    if row is None:
        raise not_found()
    return dict(row)


def update_task(session, user_id, task_id, version, content):
    previous = get_task(session, user_id, task_id)
    if previous['version'] != version:
        raise BusinessError('TASK_VERSION_CONFLICT', '任务已被修改，请读取最新内容后重新确认', details={'task_id':str(task_id),'current_version':previous['version']})
    data = {**derived(content), 'updated_at':utc_now(), 'id':str(task_id), 'user_id':user_id, 'expected':version}
    columns = list(derived(content)) + ['updated_at']
    updated = session.execute(text(f"UPDATE tasks SET {','.join(key+'=:'+key for key in columns)}, version=version+1 WHERE id=:id AND user_id=:user_id AND version=:expected"),data)
    if updated.rowcount != 1:
        raise BusinessError('TASK_VERSION_CONFLICT', '任务已被修改')
    return task_view(get_task(session,user_id,task_id))


def list_tasks(session, user_id, page=1, page_size=20, q=None, course_name=None, due_from=None, due_to=None):
    conditions = ['user_id=:user']; params = {'user':user_id, 'limit':page_size, 'offset':(page-1)*page_size}
    if q:
        escaped = normalize(q).replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        conditions.append("(title_normalized LIKE :q ESCAPE '\\' OR external_ref_normalized LIKE :q ESCAPE '\\')")
        params['q'] = f'%{escaped}%'
    if course_name:
        conditions.append('course_normalized=:course'); params['course'] = normalize(course_name)
    if due_from:
        conditions.append('due_date>=:due_from'); params['due_from'] = due_from.isoformat()
    if due_to:
        conditions.append('due_date<=:due_to'); params['due_to'] = due_to.isoformat()
    where = ' AND '.join(conditions)
    total = session.execute(text(f'SELECT COUNT(*) FROM tasks WHERE {where}'),params).scalar_one()
    rows = session.execute(text(f'SELECT * FROM tasks WHERE {where} ORDER BY due_date IS NULL, due_date, created_at, id LIMIT :limit OFFSET :offset'),params).mappings()
    return {'items':[task_view(row) for row in rows], 'page':page, 'page_size':page_size, 'total':total}
