"""Opt-in live smoke test using public notices and pre-labelled expectations."""

import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from backend.app.adapters.agent_adapter import AgentAdapter, AgentError
from backend.app.schemas.agent import ExtractionRequest, MatchBatchRequest, MatchRequestItem, TaskView

XJTU = "西安交通大学《电信T.A杯》“AI+教育”创新应用技能大赛"
USTC = "2026全国颠覆性技术创新大赛智能网联新能源汽车锦标赛"
NOTICES = [
    {
        "case": "XJTU-ORIGINAL",
        "source": "https://agent.xjtu.edu.cn/aiic/info/1023/1206.htm",
        "text": f"{XJTU}作品提交：请在2026年10月25日前，将所有参赛材料合成一个 ZIP 或 RAR 压缩包，经作品收集链接 [URL_1] 上传。压缩包名称依次包含赛道、作品、负责人和学校。",
        "time": "2026-10-12T00:00:00+08:00",
        "due": [("2026-10-25", "DATE")],
    },
    {
        "case": "XJTU-UPDATE",
        "source": "https://agent.xjtu.edu.cn/aiic/info/1023/1208.htm",
        "text": f"{XJTU}组委会将报名截止改为2026年10月16日，请各参赛队按时完成报名。作品提交截止日期仍为2026年10月25日，提交细节见 [URL_1]。",
        "time": "2026-10-13T00:00:00+08:00",
        "due": [("2026-10-16", "DATE"), ("2026-10-25", "DATE")],
    },
    {
        "case": "SCUT-SUBMISSION",
        "source": "https://www2.scut.edu.cn/sfl/2026/0917/c35377a635743/page.htm",
        "text": "第九届“外教社杯”全国高校学生跨文化能力大赛华南理工大学校园选拔赛：请在2026年10月15日23:59前将校赛作品发至指定邮箱 [EMAIL_1]。",
        "time": "2026-09-17T00:00:00+08:00",
        "due": [("2026-10-15", "DATETIME")],
    },
    {
        "case": "USTC-POSTPONEMENT",
        "source": "https://www.ustc.edu.cn/info/1362/26069.htm",
        "text": f"{USTC}公开征集，自主报名原定2026年11月15日截止，现延期至2026年12月6日。参赛者到官网 [URL_1] 注册报名，并提交参赛单位信息、项目基本信息和参赛承诺书。",
        "time": "2026-11-14T00:00:00+08:00",
        "due": [("2026-12-06", "DATE")],
    },
]
TASK_FIELDS = ("title", "description", "course_name", "external_ref", "due_kind",
               "due_date", "due_at", "timezone", "requirements")


async def verify(agent: AgentAdapter) -> bool:
    extracted = {}
    failed = False
    for notice in NOTICES:
        started = perf_counter()
        try:
            result = await agent.extract_tasks(ExtractionRequest(
                sanitized_text=notice["text"], reference_time=notice["time"],
                source_time_known=True, timezone="Asia/Shanghai",
            ))
        except AgentError as exc:
            print(f'{notice["case"]} ERROR {exc.code} {exc} ({perf_counter() - started:.2f}s)')
            failed = True
            continue

        dates = [(task.due_date.isoformat() if task.due_date else None, task.due_kind) for task in result.tasks]
        ok = dates == notice["due"] and all(
            task.course_name is None and task.external_ref is None and not task.uncertain_fields
            for task in result.tasks
        )
        if ok and notice["case"] == "XJTU-ORIGINAL":
            requirements = result.tasks[0].requirements or ""
            ok = "ZIP" in requirements.upper() and "RAR" in requirements.upper() and "[URL_1]" in requirements
        if ok and notice["case"] == "XJTU-UPDATE":
            ok = all("requirements" not in task.observed_fields for task in result.tasks)
        if ok and notice["case"] == "SCUT-SUBMISSION":
            task = result.tasks[0]
            ok = task.due_at == datetime.fromisoformat("2026-10-15T15:59:00+00:00") and "[EMAIL_1]" in (task.requirements or "")
        print(f'{notice["case"]} {"PASS" if ok else "FAIL"} ({perf_counter() - started:.2f}s)')
        print(result.model_dump_json(ensure_ascii=False))
        if ok:
            extracted[notice["case"]] = result.tasks
        failed |= not ok

    if not {"XJTU-ORIGINAL", "XJTU-UPDATE"} <= extracted.keys():
        print("XJTU-MATCH SKIPPED (extraction did not pass)")
        return False

    # The old submission candidate comes from the actual original notice.
    old = TaskView(
        **extracted["XJTU-ORIGINAL"][0].model_dump(include=set(TASK_FIELDS)),
        id="xjtu-original-entry-submission", version=1,
        created_at="2026-10-12T08:00:00Z", updated_at="2026-10-12T08:00:00Z",
    )
    # Compare both new drafts with this same candidate. Registration is a
    # distinct operation, and the unchanged submission refers to the old task.
    drafts = [
        MatchRequestItem(draft_item_id=task.due_date.isoformat(), new_task=task, candidate_tasks=[old])
        for task in extracted["XJTU-UPDATE"]
    ]
    started = perf_counter()
    try:
        matches = await agent.match_tasks(MatchBatchRequest(items=drafts))
    except AgentError as exc:
        print(f"XJTU-MATCH ERROR {exc.code} {exc} ({perf_counter() - started:.2f}s)")
        return False
    expected = [(date(2026, 10, 16).isoformat(), "DIFFERENT", None),
                (date(2026, 10, 25).isoformat(), "SAME", old.id)]
    actual = [(item.draft_item_id, item.relation, item.matched_task_id) for item in matches.items]
    ok = expected == actual
    print(f'XJTU-MATCH {"PASS" if ok else "FAIL"} ({perf_counter() - started:.2f}s)')
    print(matches.model_dump_json(ensure_ascii=False))
    failed |= not ok
    if "USTC-POSTPONEMENT" not in extracted:
        print("USTC-MATCH SKIPPED (extraction did not pass)")
        return False

    # Reconstruct the old snapshot from the original date quoted in the notice.
    old = TaskView(
        id="ustc-registration", title=f"{USTC}公开征集报名", description=None,
        course_name=None, external_ref=None, due_kind="DATE", due_date=date(2026, 11, 15),
        due_at=None, timezone="Asia/Shanghai", requirements=None, version=1,
        created_at="2026-11-01T08:00:00Z", updated_at="2026-11-01T08:00:00Z",
    )
    started = perf_counter()
    try:
        matches = await agent.match_tasks(MatchBatchRequest(items=[
            MatchRequestItem(draft_item_id="ustc-update", new_task=extracted["USTC-POSTPONEMENT"][0],
                             candidate_tasks=[old]),
        ]))
    except AgentError as exc:
        print(f"USTC-MATCH ERROR {exc.code} {exc} ({perf_counter() - started:.2f}s)")
        return False
    item = matches.items[0]
    ok = item.relation == "SAME" and item.matched_task_id == old.id
    print(f'USTC-MATCH {"PASS" if ok else "FAIL"} ({perf_counter() - started:.2f}s)')
    print(matches.model_dump_json(ensure_ascii=False))
    return ok and not failed


async def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    load_dotenv(args.env_file)
    try:
        async with AgentAdapter() as agent:
            print(json.dumps({"model": agent.settings.model}))
            return 0 if await verify(agent) else 1
    except AgentError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
