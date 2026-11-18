# Deadline Keeper V2 后端

本服务提供通知录入、规则脱敏、本地 PaddleOCR、Agent 任务提取、旧任务匹配、草稿检查和确认保存。录入与分析分成两个请求：只有用户确认后才写正式任务。当前用户由服务端配置的演示用户确定；当前实现是单进程、单用户演示，不提供登录认证。

## 环境准备

- Python 3.11 或以上，且 Python 实际连接的 SQLite 为 **3.51.3 或以上**。用 `python -c 'import sqlite3; print(sqlite3.sqlite_version)'` 检查；数据库放在本机磁盘。
- 配置 OpenAI Chat Completions 兼容的模型服务：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。适配器不自动加载 `.env`，请通过进程环境设置；不要提交密钥。
- 安装 PaddleOCR 和本地中文模型。以下命令以 macOS/Linux 为例，Windows 使用对应的虚拟环境和环境变量设置方式：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[ocr,test]'
mkdir -p models/paddleocr/det models/paddleocr/rec
curl -L --fail https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_det_infer.tar | tar -x -C models/paddleocr/det --strip-components=1
curl -L --fail https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_rec_infer.tar | tar -x -C models/paddleocr/rec --strip-components=1
export DATABASE_URL=sqlite:///./data/deadline_keeper.db
export OCR_MODEL_DIR=./models/paddleocr
export LLM_BASE_URL='<模型服务基础地址>'
export LLM_MODEL='<模型名称>'
export LLM_API_KEY='<模型密钥>'
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

模型目录可放在其他本地位置，通过 `OCR_MODEL_DIR` 指定；目录下的 `det/` 和 `rec/` 分别对应 PP-OCRv5 mobile 检测和识别模型。首次启动会检查数据库迁移、配置和本地 OCR 模型，缺少依赖时启动失败。OCR 本身不需要联网，模型调用需要配置的服务可用。

## 接口与流程

1. `POST /api/v1/imports/text` 接收 JSON `text`，可选 `source_time` 和 `timezone`；或通过 `POST /api/v1/imports/image` 以 multipart 的 `file` 上传 PNG/JPEG 截图。成功返回导入 ID、脱敏正文和版本。
2. `POST /api/v1/imports/{import_id}/analyze`，提交 `{"expected_version": 1}`。服务调用 Agent 提取、查询并匹配旧任务，返回 `ImportDetail` 和待检查草稿；`GET /api/v1/imports/{import_id}` 可查询后续状态。
3. 用户检查并修改草稿后，调用 `POST /api/v1/imports/{import_id}/confirm`，提供 `Idempotency-Key` 和最终选择。该操作在单个数据库事务内保存，支持版本冲突检查和幂等重放。
4. `GET /api/v1/tasks`、`GET /api/v1/tasks/{id}`、`PATCH /api/v1/tasks/{id}` 用于查看和编辑正式任务。`GET /health/live` 与 `GET /health/ready` 分别检查进程和本地依赖。

详细请求、响应和业务规则见仓库的 `docs/Deadline-Keeper-V2-后端方案.md`。规则脱敏覆盖邮箱、手机号、身份证号和带“学号”标签的编号，但不能保证识别全部敏感信息。当前截图本地 OCR；只有脱敏后的文本及候选任务发送给模型。失败时不把失败伪装成空任务结果，分析失败后可用新版本重试。

## 验证

在仓库根目录执行：

```bash
PYTHONPATH=backend python -m pytest -q backend/tests
```

自动化测试使用临时数据库及模拟 Agent/OCR，测试代码显式跳过 SQLite 最低版本检查；它们不代替真实模型、真实截图和前后端全链路验收。备份运行中的 WAL 数据库应使用 SQLite 备份接口。

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

本交付实现后端进程内接口和独立命令行验收入口。V2 导入、分析调度、匹配和确认保存接口已实现；真实外部模型以及前后端整体流程仍需在实际部署环境中完成联调。
