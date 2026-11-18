"""Generate suggestions without modifying formal tasks or holding DB connections during Agent calls."""
from difflib import SequenceMatcher
import anyio
from app.repositories.task_repository import list_user_task_snapshots
from app.core.errors import BusinessError
from app.schemas.agent import MatchBatchRequest, MatchBatchResult
from app.schemas.task import TaskContent
from app.services.common import canonical_json, normalize
from app.services.task_service import FIELDS, derived, task_view

OBSERVED = {'title','description','course_name','external_ref','deadline','requirements'}
DEADLINE = {'due_kind','due_date','due_at','timezone'}


def _new_task(task):
    return task.model_dump(mode='json', include=set(FIELDS))


def _merged(task, old):
    result = {key:old[key] for key in FIELDS}
    new = _new_task(task)
    for field in task.observed_fields:
        if field in task.uncertain_fields:
            continue
        keys = DEADLINE if field == 'deadline' else {field}
        if field == 'deadline' and new['due_kind'] == 'UNRESOLVED':
            continue
        for key in keys:
            if new[key] is not None or (field == 'deadline' and key in ('due_date','due_at')):
                result[key] = new[key]
    changes = [{'field':key,'before':old[key],'after':result[key]} for key in FIELDS if old[key] != result[key]]
    return result, changes


def _result(task, candidates, truncated, method, reason, matched=None, uncertain=None, decision=None):
    uncertainties = sorted(set(task.uncertain_fields) | set(uncertain or []))
    proposed, changes = _merged(task, matched) if matched else (_new_task(task), [])
    if uncertainties or not proposed['title'] or proposed['due_kind'] == 'UNRESOLVED':
        action = 'REVIEW'
    elif decision:
        action = decision
    elif matched:
        action = 'UPDATE' if changes else 'DUPLICATE'
    else:
        action = 'CREATE'
    return {'suggested_action':action,'matched_task_id':matched['id'] if matched else None, 'matched_task_version':matched['version'] if matched else None, 'proposed_task':proposed, 'match_details':{'method':method,'reason':reason,'uncertain_fields':uncertainties,'candidates_truncated':truncated,'candidate_tasks':candidates,'field_changes':changes}}


class MatchingService:
    def __init__(self, engine, agent=None, privacy_filter=None):
        self.engine, self.agent, self.privacy_filter = engine, agent, privacy_filter

    async def suggest(self, user_id, items):
        if len(items) > 20 or len({str(id) for id, _ in items}) != len(items):
            raise BusinessError('INPUT_TOO_COMPLEX', '草稿数量或 ID 不合法', 422)
        # Detach snapshots before invoking external services.
        rows = await anyio.to_thread.run_sync(list_user_task_snapshots, self.engine, user_id)
        outputs = [None] * len(items)
        pending = []
        for index, (draft_id, task) in enumerate(items):
            data = _new_task(task)
            full = None
            if set(task.observed_fields) >= OBSERVED and not task.uncertain_fields:
                try:
                    full = derived(TaskContent.model_validate(data))['content_hash']
                except ValueError:
                    pass
            exact = [task_view(row) for row in rows if full and row['content_hash'] == full]
            identity = derived(TaskContent.model_validate({**data,'title':data['title'] or 'x','due_kind':'NONE','due_date':None,'due_at':None}))['identity_key']
            same_identity = [task_view(row) for row in rows if identity and row['identity_key'] == identity]
            if len(exact) == 1:
                outputs[index] = _result(task, exact, False, 'RULE_CONTENT', '完整内容一致', exact[0], decision='DUPLICATE')
                continue
            if len(exact) > 1 or len(same_identity) > 1:
                many = exact if len(exact) > 1 else same_identity
                outputs[index] = _result(task, many[:5], len(many)>5, 'RULE_IDENTITY', '匹配到多个已有任务', decision='REVIEW')
                continue
            if len(same_identity) == 1:
                outputs[index] = _result(task, same_identity, False, 'RULE_IDENTITY', '课程和任务编号一致', same_identity[0])
                continue
            course, ref, title = (normalize(data[k]) for k in ('course_name','external_ref','title'))
            ranked = []
            for row in rows:
                old_course, old_ref, old_title = row['course_normalized'], row['external_ref_normalized'], row['title_normalized']
                similarity = SequenceMatcher(None,title or '',old_title or '').ratio() if title and old_title else 0
                course_hit = bool(course and old_course == course)
                ref_hit = bool(ref and old_ref and (ref in old_ref or old_ref in ref))
                if course_hit or ref_hit or similarity >= .5:
                    ranked.append((-(2*course_hit + 2*ref_hit + similarity),row['id'],task_view(row)))
            ranked.sort(key=lambda x:(x[0],x[1]))
            candidates = [x[2] for x in ranked[:5]]
            truncated = len(ranked) > 5
            if not candidates:
                outputs[index] = _result(task, [], False, 'NO_CANDIDATE', '没有匹配候选')
            else:
                pending.append((index,str(draft_id),task,candidates,truncated))
        if pending:
            if self.agent is None or self.privacy_filter is None:
                raise BusinessError('DEPENDENCY_NOT_READY','语义匹配和候选脱敏适配器未配置',503)
            filtered_items = []
            for _, draft_id, task, candidates, _ in pending:
                filtered = [self.privacy_filter(dict(candidate)) for candidate in candidates]
                filtered_items.append({'draft_item_id':draft_id,'new_task':task,'candidate_tasks':filtered})
            request = MatchBatchRequest.model_validate({'items':filtered_items})
            if len(canonical_json(request.model_dump(mode='json'))) > 60000:
                raise BusinessError('INPUT_TOO_COMPLEX','匹配输入超出长度限制',422)
            try:
                raw_response = await self.agent.match_tasks(request)
            except (BusinessError, TimeoutError):
                raise
            except Exception as exc:
                raise BusinessError('LLM_UPSTREAM_ERROR','语义匹配服务调用失败',502) from exc
            try:
                response = MatchBatchResult.model_validate(raw_response)
                by_id = {item.draft_item_id:item for item in response.items}
                if len(by_id) != len(response.items) or set(by_id) != {p[1] for p in pending}:
                    raise ValueError('匹配草稿 ID 不一致')
                for index, draft_id, task, candidates, truncated in pending:
                    item = by_id[draft_id]
                    selected = next((candidate for candidate in candidates if candidate['id'] == item.matched_task_id), None)
                    if item.relation == 'SAME' and selected is None:
                        raise ValueError('任务不在候选集合')
                    decision = 'REVIEW' if item.relation == 'UNCERTAIN' or (item.relation == 'DIFFERENT' and truncated) else ('CREATE' if item.relation == 'DIFFERENT' else None)
                    outputs[index] = _result(task,candidates,truncated,'LLM',item.reason,selected,item.uncertain_fields,decision)
            except BusinessError:
                raise
            except Exception as exc:
                raise BusinessError('LLM_INVALID_OUTPUT','语义匹配结果无效',502) from exc
        return outputs
