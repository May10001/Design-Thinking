# 后端二模块

本目录实现项目方案中后端二负责的数据库迁移、正式任务查询与编辑、匹配建议、确认保存及共用数据模型。`app.main` 是供联调的 FastAPI 入口，目前只注册后端二的路由；录入、OCR、分析调度和真实 Agent 由对应负责人接入。

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
