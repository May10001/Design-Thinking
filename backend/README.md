# 后端二模块

本目录实现项目方案中后端二负责的数据库迁移、正式任务查询与编辑、匹配建议、确认保存及共用数据模型。`app.main` 是供联调的 FastAPI 入口，目前只注册后端二的路由；Agent 适配器已合并；录入、OCR 和分析调度仍需对接。

## 运行

需要 Python 3.11+ 和 **Python 实际使用的 SQLite 3.51.3+**，数据库必须放在本地磁盘。先执行以下命令确认版本；系统 `sqlite3` 命令的版本不代表 Python 的版本：

```bash
python -c 'import sqlite3; print(sqlite3.sqlite_version)'
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export DATABASE_URL=sqlite:///./data/deadline_keeper.db
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

环境变量参考 `.env.example`；应用从进程环境读取，不自动加载 `.env`。首次迁移后启动会创建配置指定的演示用户，已有用户与数据不会被清除。程序检查 SQLite 版本、WAL、迁移版本、用户 ID 和时区；低版本不会绕过校验。SQLite 每个连接启用外键、5 秒忙等待和 FULL 同步。服务仅按单进程、单演示用户设计，不提供认证。运行 `pytest -q` 使用临时 SQLite 数据库；测试显式跳过最低 SQLite 版本检查，不能据此推断运行环境满足部署要求。

已提供接口：

- `GET /api/v1/tasks`：分页、查询和日期筛选。
- `GET /api/v1/tasks/{task_id}`、`PATCH /api/v1/tasks/{task_id}`。
- `POST /api/v1/imports/{import_id}/confirm`：`Idempotency-Key`、批量确认、版本检查和响应重放。

具体请求与字段按项目的《Deadline Keeper V2 后端方案与接口文档》执行。请求体不接收用户 ID；当前用户只由服务端配置决定。任务不能通过匹配服务直接写入，需用户确认后保存。

## 给后端一和 Agent 负责人的对接约定

- 数据库迁移是表结构的唯一来源；`app.models.reflect_models(engine)` 在迁移后提供五张表的 SQLAlchemy 映射类。`app.core.database.immediate_write_transaction(engine)` 用于要求 `BEGIN IMMEDIATE` 的短事务，同一 Session 不得跨线程使用或跨模型调用持有。
- `app.schemas.task.TaskContent`、`app.schemas.review.ConfirmRequest` 和 `app.schemas.agent` 中的 `ExtractedTask`、`MatchBatchRequest`、`MatchBatchResult` 为共用数据模型。
- 分析服务为每条提取结果生成唯一草稿 ID 后，调用 `await MatchingService(engine, agent, privacy_filter).suggest(user_id, [(draft_id, extracted_task), ...])`。返回列表与输入顺序一致；每项含 `suggested_action`、`matched_task_id`、`matched_task_version`、`proposed_task`、`match_details`。分析服务负责在同一事务里写入草稿和导入状态；匹配服务本身不写数据库。
- 需要语义判断时，`agent` 必须提供 `async match_tasks(MatchBatchRequest)`，返回 `MatchBatchResult` 或同结构对象。`privacy_filter` 是同步函数，接收一份候选 `TaskView` 字典并返回已脱敏字典，须保留公开任务 ID、版本及匹配必要字段。原始候选快照只返回给本地用户；传入 Agent 的候选必须先脱敏。未配置时返回 `DEPENDENCY_NOT_READY`，不会伪造模型判断。
- 调用方负责模型及整次分析的超时、并发控制、失败状态和事务边界。匹配服务区分有效的 `UNCERTAIN`、无效输出和模型调用失败，并拒绝未知候选 ID；候选超过五条时 `DIFFERENT` 保持 `REVIEW`。
- 后端一在其主应用中挂载 `app.routers.tasks.router` 和 `app.routers.review.router`，并提供 `app.state.engine`、`app.state.user_id`，同时复用或合并 `app.main` 中的错误响应和请求 ID 处理。不要同时初始化两个独立数据库或信任客户端提交的用户 ID。

数据库模型、任务和确认模块的自动化测试不替代截图 OCR、真实 LLM 和前后端全链路联调。实际部署应使用 SQLite 备份接口备份处于 WAL 模式的数据库。

---

# deadline_keeper V2 Agent 交付说明

本模块将脱敏通知转成待检查的任务草稿，并为后端给出的候选旧任务判断关系。接口与《deadline_keeper V2 后端方案》第 9 节和《Agent 需求说明》第 5 节一致。原仓库使用 DeepSeek 兼容协议，本模块用异步 HTTPX 直接调用同一协议，以支持后端请求取消和单次调用超时。

## 本地启动

在仓库根目录执行。需要 Python 3.11 或以上，有效的模型 API 配置；Windows 下如果 `python` 指向其他版本，可替换为 `py -3.11` 创建环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-agent.txt
Copy-Item backend/.env.agent.example backend/.env
```

在 `backend/.env` 填写 `LLM_API_KEY`，并按需设置 `LLM_BASE_URL`、`LLM_MODEL`。示例供应商为 DeepSeek，测试模型为 `deepseek-flash`，基础地址为 `https://api.deepseek.com`。兼容供应商须支持 `/chat/completions` 的 JSON 对象模式。在部署环境中直接注入同名环境变量即可；环境变量优先于配置文件。模型配置不使用仓库根目录旧 Demo 的变量名。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m deadline_keeper extract backend/tests/fixtures/extraction.json --env-file backend/.env
.\.venv\Scripts\python.exe -X utf8 -m deadline_keeper match backend/tests/fixtures/matching.json --env-file backend/.env
```

两个命令都把校验结果输出为 JSON。提取样例应返回延期实验报告和课堂测验，其中测验用 `UNRESOLVED` 标记“明天”，并返回 `SOURCE_TIME_UNKNOWN`。匹配样例模拟报告延期，预期 `SAME` 并返回候选 ID。

## 接入 FastAPI 后端

Agent 适配器和共用模型已在本仓库。使用后端已有的 lifespan 来创建并关闭适配器，应用实例可复用一个客户端连接池。例如，从 `backend/` 运行的后端可使用以下入口代码：

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.adapters.agent_adapter import AgentAdapter


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AgentAdapter() as agent:
        app.state.agent = agent
        yield


app = FastAPI(lifespan=lifespan)
```

- `AnalysisService` 构造 `ExtractionRequest`，传入 `sanitized_text`、冻结的 `reference_time`、`source_time_known` 和 IANA `timezone`，执行 `await agent.extract_tasks(request)`。提取结果包含标题、课程、编号、日期、提交要求、原文证据及待检查字段。按 `observed_fields` 合并本次明确提供的值。仅有日期时保留 `DATE`。明确到时刻时，`due_at` 转为 UTC，`due_date` 是当地日期。
- `MatchingService` 查询候选并先完成规则匹配，再用 `MatchBatchRequest` 批量调用 `await agent.match_tasks(request)`。模型输入使用脱敏后的候选快照，传入 `TaskView` 所要求的字段。每项带 1–5 个候选，每批最多 20 项，序列化长度上限 60,000 字符。结果按输入顺序对应每个草稿 ID，依据 `SAME`、`DIFFERENT` 或 `UNCERTAIN` 生成建议。用户复核、字段合并和事务保存由后端服务执行。
- 单次模型调用最多 40 秒，整次分析的 90 秒总预算由后端控制。父协程的取消会传递到 HTTP 请求。每个非空调用只发一个模型请求。解析失败会使本批失败，接入方应在两步均成功后再保存草稿。

| 情况 | 异常及后端处理 |
| --- | --- |
| 必填模型配置缺失或配置非法 | `AgentConfigurationError`，启动失败 |
| 模型请求超时 | `AnalysisTimeout`（也是 `TimeoutError`），`ANALYSIS_TIMEOUT` |
| 模型拒绝或网络、鉴权失败 | `LLMUpstreamError`，`LLM_UPSTREAM_ERROR` |
| 畸形 JSON、字段不一致、非法证据或候选 ID | `LLMInvalidOutput`，`LLM_INVALID_OUTPUT` |
| 超出任务数、候选数或匹配正文上限 | `InputTooComplex`，`INPUT_TOO_COMPLEX` |
| 请求字段无效或后端组装的候选不符合约定 | `ValidationError` 或 `ValueError` |

适配器抛出的模型异常只含可记录的简短信息。后端处理输入校验错误时，应避免把 Pydantic 错误中的完整输入写入日志。

## 验收复现

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest backend.tests.test_agent -v
.\.venv\Scripts\python.exe -X utf8 -m backend.tests.live_notices --env-file backend/.env
```

固定响应测试覆盖结果格式、日期组合、原文片段、批量关联、越界 ID、超时、取消和并发隔离。真实模型测试使用高校公开公告的节选转述，替换了提交链接和邮箱，逐项核对日期、提交要求、任务数和候选 ID。两个 CLI JSON 样例为构造的课程通知，便于复现课程名、编号和缺发布时间的日期场景。

## 本次验证结果

验证于 2026 年 11 月 17 日运行，使用 Windows、Python 3.13.9、`requirements-agent.txt` 所列依赖和 DeepSeek `deepseek-flash`。13 项固定响应测试通过，4 条公开公告提取和 2 次真实批量匹配通过。

| 测试 | 预期 | 实际 |
| --- | --- | --- |
| 西交大作品提交 | 1 项，2026-10-25，仅到日，ZIP 或 RAR，并保留链接占位符 | 通过，1.79 秒 |
| 西交大报名顺延 | 2 项，报名 2026-10-16，作品提交 2026-10-25。引用提交细节的链接应保留旧要求 | 通过，1.33 秒 |
| 华工校赛提交 | 1 项，当地 2026-10-15 23:59，UTC 2026-10-15 15:59，保留邮箱占位符 | 通过，1.12 秒 |
| 中科大赛事报名延期 | 1 项，使用新日期 2026-12-06 | 通过，1.84 秒 |
| 西交大批量匹配 | 报名与旧作品提交为 `DIFFERENT`，新旧作品提交为 `SAME` | 通过，1.15 秒 |
| 中科大延期匹配 | 与原截止 2026-11-15 的报名候选为 `SAME` | 通过，0.66 秒 |
| 提取 CLI 构造样例，真实模型 | 两项，正确保留课程和编号，未知发布时间时“明天”待检查 | 通过，测验 `UNRESOLVED`，告警 `SOURCE_TIME_UNKNOWN` |
| 匹配 CLI 构造样例，真实模型 | 机器学习实验报告延期，返回对应候选 | 通过，`SAME` |

公开来源见[西安交大作品提交公告](https://agent.xjtu.edu.cn/aiic/info/1023/1206.htm)、[西安交大报名顺延公告](https://agent.xjtu.edu.cn/aiic/info/1023/1208.htm)、[华南理工校赛通知](https://www2.scut.edu.cn/sfl/2026/0917/c35377a635743/page.htm)及[中国科大赛事通知](https://www.ustc.edu.cn/info/1362/26069.htm)。中科大旧候选按公告中记载的原截止日期构造。

早期调用出现过字段组合校验失败，以及模型把“提交细节见链接”当作新要求。调整提示词并补充引用校验后，上表所列重跑全部通过。语义正确性仍取决于通知内容和模型表现，`UNCERTAIN` 和异常路径目前通过固定响应用例验证。公开样例属于联调验收，样本量不足以估计开题方案的字段准确率。

本交付实现后端进程内接口和独立命令行验收入口。任务候选查询与确认保存已提供；V2 导入、分析调度和草稿保存仍需团队完成联调。
