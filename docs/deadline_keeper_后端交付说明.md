# deadline_keeper 后端接入交付说明

交付日期为 2026 年 11 月 18 日，面向负责通知分析、候选查询和任务保存的后端同学。本文依据当前交付源码整理，模型验收记录来自 2026 年 11 月 17 日。

获取本仓库后，以包含 README.md、backend/ 和 deadline_keeper/ 的目录作为仓库根目录执行下文命令。

## 1 交付内容

Agent 提供两个异步方法，供 FastAPI 后端在进程内调用。后端传入已脱敏的数据，方法成功时返回经过校验的 Pydantic 模型，失败时抛出明确异常。

| 方法 | 输入 | 输出 | 后端调用位置 |
| --- | --- | --- | --- |
| extract_tasks() | 脱敏通知、参考时间、发布时间是否已知、时区 | 多条任务、日期、提交要求、原文证据和不确定字段 | AnalysisService |
| match_tasks() | 新任务及其候选旧任务 | SAME、DIFFERENT 或 UNCERTAIN，关联 ID 和理由 | MatchingService |

提取支持同一通知中的多项任务。匹配支持重复通知、延期通知和信息不足时的待检查判断。

截图上传、OCR、规则脱敏、候选查询、草稿持久化、用户确认及正式任务保存由后端服务完成。当前可验收范围是 Agent 接口与独立 CLI，团队后端合并后还需联调完整保存流程。

## 2 需要合并的文件

| 文件或目录 | 用途 |
| --- | --- |
| backend/app/adapters/agent_adapter.py | 模型调用、校验、异常类和连接关闭 |
| backend/app/schemas/agent.py | 请求、任务草稿、候选和结果模型 |
| backend/app/agent/prompts/ | 提取与匹配提示词及包声明 |
| backend/app/agent/__init__.py 和 __main__.py | 包声明及原模块 CLI |
| backend/requirements-agent.txt | 模块依赖 |
| backend/.env.agent.example | 配置示例 |
| backend/tests/ | 固定响应测试、真实公告验收及 JSON 样例 |
| deadline_keeper/ | 仓库根目录下的统一 CLI 入口 |

将 backend/app/ 下的文件并入团队后端对应目录。若已有 schemas/agent.py，需要先合并共用字段，保证调用方统一使用同一份定义。保持 adapters/、schemas/ 和 agent/ 在同一个 app 包下，以便相对导入生效。

backend/tests/ 中使用 backend.app.* 导入，并从仓库根目录执行。合并后若调整目录，应同步调整测试导入路径。教学示例位于 deadline_keeper_chat_demo/、deadline_keeper_mcp_demo/ 和 deadline_keeper_research_demo/，后端接入使用上表中的 Agent 文件即可。

## 3 安装与首次运行

需要 Python 3.11 或以上。本次开发验收使用 Windows、Python 3.13.9，依赖版本如下。

| 依赖 | 已验证版本 |
| --- | --- |
| httpx | 0.28.1 |
| pydantic | 2.12.4 |
| python-dotenv | 1.1.0 |
| tzdata | 2025.2 |

在仓库根目录执行。

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-agent.txt
Copy-Item backend/.env.agent.example backend/.env
~~~

在 backend/.env 中填入配置，LLM_API_KEY 使用后端部署者自己的有效密钥。

~~~dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=填写有效密钥
LLM_MODEL=deepseek-flash
LLM_TIMEOUT_SECONDS=40
~~~

本次实测使用 DeepSeek 的 deepseek-flash。LLM_BASE_URL 填 API 基础地址，适配器追加 /chat/completions。更换供应商时，需确认其支持 Chat Completions JSON 对象模式和当前请求参数，并重新验收。

CLI 会按 --env-file 加载文件，已有环境变量优先。直接创建 AgentAdapter() 时只读取环境变量，FastAPI 启动时应提前加载配置文件或注入环境变量。实际密钥按团队约定配置在服务端。

先运行两个 CLI 样例。

~~~powershell
.\.venv\Scripts\python.exe -X utf8 -m deadline_keeper extract backend/tests/fixtures/extraction.json --env-file backend/.env
.\.venv\Scripts\python.exe -X utf8 -m deadline_keeper match backend/tests/fixtures/matching.json --env-file backend/.env
~~~

提取样例应返回报告和测验两项任务。报告日期为 2026-11-23，测验中的“明天”因缺少发布时间而标为 UNRESOLVED，并返回 SOURCE_TIME_UNKNOWN。匹配样例应返回 SAME 和样例中的候选 ID。

## 4 任务提取接口

~~~python
async def extract_tasks(request: ExtractionRequest) -> ExtractionResult:
    ...
~~~

ExtractionRequest 的四个字段均必填。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| sanitized_text | str | 脱敏正文，1–20,000 字符，不能全为空白 |
| reference_time | 带时区的 datetime | 后端在录入时冻结，重试时沿用 |
| source_time_known | bool | 已知原通知发布时间时为 true，仅知接收时间时为 false |
| timezone | str | IANA 时区，例如 Asia/Shanghai |

即使 reference_time 使用服务器接收时间，只要原通知发布时间未知，source_time_known 仍应为 false。

ExtractionResult 包含 tasks 和 warnings。tasks 最多 20 项，每项字段如下，所有键均存在，可空字段使用 null。

| 字段 | 含义及限制 |
| --- | --- |
| title | 标题，最多 200 字符。不明时为 null 并标记 title 不确定 |
| description | 补充说明，可为 null，最多 2,000 字符 |
| course_name | 课程，可为 null，最多 100 字符 |
| external_ref | 作业或任务编号，可为 null，最多 100 字符 |
| due_kind | NONE、DATE、DATETIME 或 UNRESOLVED |
| due_date | 当地日历日期，序列化为 YYYY-MM-DD，可为 null |
| due_at | 精确时间，返回模型中为带时区 datetime，序列化时统一为 UTC，可为 null |
| timezone | 与请求相同的 IANA 时区 |
| requirements | 提交方式、格式、命名等，可为 null，最多 4,000 字符 |
| observed_fields | 本次通知明确提供、可用于更新的字段 |
| uncertain_fields | 需要人工检查的字段 |
| reason | 提取依据或待检查原因，最多 500 字符 |
| source_excerpt | 当前脱敏通知中的连续原文片段，最多 1,000 字符 |

observed_fields 可取 title、description、course_name、external_ref、deadline、requirements。uncertain_fields 还可包含 task_identity。两组字段不重复且互斥。

| due_kind | due_date | due_at | 含义 |
| --- | --- | --- | --- |
| NONE | null | null | 原文未说明期限，或明确说明不限时 |
| DATE | 有值 | null | 只精确到某一天 |
| DATETIME | 有值 | 有值 | 日期和时刻明确，按任务时区换算后两者一致 |
| UNRESOLVED | null | null | 日期含糊或信息不足，需要检查 |

“11 月 20 日”缺少年份时需检查。“明天”只有在已知通知发布时间时才能解析。完全没有期限的任务可正常返回 NONE。

warnings 是含 code 和 message 的数组。Agent 产生 SOURCE_TIME_UNKNOWN 或 NO_TASK_FOUND。后端输入模块负责 PRIVACY_RULE_APPLIED 和 OCR_LOW_QUALITY。

tasks=[] 且带 NO_TASK_FOUND 表示正常理解后没有待办事项。模型失败通过异常传递。

## 5 任务匹配接口

~~~python
async def match_tasks(request: MatchBatchRequest) -> MatchBatchResult:
    ...
~~~

请求 items 中，每项包含以下字段。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| draft_item_id | str | 后端分配的本批草稿标识，必须唯一 |
| new_task | ExtractedTask | 提取结果中的一条完整任务 |
| candidate_tasks | list[TaskView] | 后端为本条草稿查出的候选旧任务 |

TaskView 需提供 title、description、course_name、external_ref、due_kind、due_date、due_at、timezone、requirements，以及 id、version、created_at、updated_at。title 必须非空，due_kind 为 NONE、DATE 或 DATETIME，version 为大于等于 1 的整数，时间戳必须带时区。

ID 为 1–128 字符、无空白的非空字符串，可直接使用后端 UUID 的字符串形式。按共用模型组装候选，数据库实体中的额外字段会被模型拒绝。

每批最多 20 条草稿，每条 1–5 个候选，序列化后的匹配请求最多 60,000 字符。无候选的草稿由后端处理。空批次直接返回 items=[]。

每个 draft_item_id 恰好对应一条结果，返回顺序与输入一致。

| relation | 含义 | matched_task_id |
| --- | --- | --- |
| SAME | 有足够证据认定与某个候选是同一任务，可包括重复、延期或格式变更 | 必须是本条输入的候选 ID |
| DIFFERENT | 与本次提供的候选均不同 | null |
| UNCERTAIN | 线索不足、冲突，或多个候选均可能 | null |

结果还包含 reason 和 uncertain_fields。日期不明确但任务身份明确时，可返回 SAME，并提示 deadline 待检查。

SAME 表达任务身份，后端仍需比较字段以决定 UPDATE 或 DUPLICATE。DIFFERENT 只针对本次候选集合，后端根据查询范围和是否截断决定最终建议。

## 6 可运行的调用示例

下面的代码从仓库根目录执行。它读取配置，提取一条延期通知，再与给定旧任务匹配，打印两个结果。

~~~python
import asyncio
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from backend.app.adapters.agent_adapter import AgentAdapter
from backend.app.schemas.agent import (
    ExtractionRequest,
    MatchBatchRequest,
    MatchRequestItem,
    TaskView,
)


async def main():
    load_dotenv(Path("backend/.env"), override=False)
    request = ExtractionRequest(
        sanitized_text="机器学习实验报告延期至 2026 年 11 月 23 日提交。",
        reference_time="2026-11-17T08:00:00Z",
        source_time_known=True,
        timezone="Asia/Shanghai",
    )
    candidate = TaskView(
        id="report-001",
        version=2,
        title="机器学习实验报告",
        description=None,
        course_name="机器学习",
        external_ref=None,
        due_kind="DATE",
        due_date=date(2026, 11, 20),
        due_at=None,
        timezone="Asia/Shanghai",
        requirements="上传 PDF",
        created_at="2026-11-16T09:00:00Z",
        updated_at="2026-11-16T09:00:00Z",
    )

    async with AgentAdapter() as agent:
        extraction = await agent.extract_tasks(request)
        print(extraction.model_dump_json(indent=2, ensure_ascii=False))
        if not extraction.tasks:
            return

        matching = await agent.match_tasks(
            MatchBatchRequest(
                items=[
                    MatchRequestItem(
                        draft_item_id="draft-001",
                        new_task=extraction.tasks[0],
                        candidate_tasks=[candidate],
                    )
                ]
            )
        )
        print(matching.model_dump_json(indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
~~~

持久化时用 model_dump(mode="json") 获取可序列化字典，或 model_dump_json() 获取 JSON 文本。model_dump() 默认保留 Python 的 date 和 datetime 对象。

## 7 接入 FastAPI 与业务流程

下例放在团队后端的 backend/app/main.py 中，从 backend/ 启动时使用 app.* 导入。根目录执行方式则对应 backend.app.*。将客户端初始化和关闭逻辑并入后端已有 lifespan。

~~~python
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from app.adapters.agent_adapter import AgentAdapter


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(config_path, override=False)
    async with AgentAdapter() as agent:
        app.state.agent = agent
        yield


app = FastAPI(lifespan=lifespan)
~~~

配置路径指向 backend/.env。FastAPI 和 Uvicorn 由团队后端依赖提供。请求中通过 request.app.state.agent 获取客户端，不同通知复用连接池，每次入参独立。

建议按以下顺序联调。

1. 后端完成文本输入或 OCR、规则脱敏，并冻结 reference_time。
2. AnalysisService 调用 extract_tasks()，为各项分配 draft_item_id。
3. MatchingService 查询当前用户的候选旧任务，先执行内容哈希、课程与编号等规则匹配。
4. 将仍需语义判断的任务及脱敏候选送入 match_tasks()。
5. 根据关系、不确定字段和新旧差异生成 CREATE、DUPLICATE、UPDATE 或 REVIEW 建议。
6. 两步均成功后，原子保存本批草稿与分析状态，交给用户逐项确认。
7. 用户确认后，以事务保存正式任务，并检查任务版本和确认请求幂等性。

排队、提取、匹配和保存共用 90 秒预算，由后端控制。可用 asyncio.timeout(remaining_seconds) 包裹调用，remaining_seconds 按整个分析截止时刻计算。适配器每个非空调用发一次模型请求，单次最多 40 秒，外层取消会向下传递，内部没有自动重试。

更新时按 observed_fields 选择本次提供的值。deadline 对应 due_kind、due_date、due_at、timezone 整组字段。通知未涉及的要求或描述应保留旧值。仅引用“仍按原要求”或“提交细节见链接”时，适配器将这类引用处理为 requirements=null，并移出 observed_fields。

不确定字段和 UNCERTAIN 进入 REVIEW。缺课程或缺编号可按实际含义保留。UNRESOLVED 用于待检查草稿，用户确认正式任务前需完成日期选择。候选外发前也需脱敏，后端保留本地候选快照供用户核对。

## 8 异常与失败处理

以下异常从 app.adapters.agent_adapter 导入，根目录运行时对应 backend.app.adapters.agent_adapter。

| 异常 | code | 后端处理 |
| --- | --- | --- |
| AgentConfigurationError | AGENT_CONFIGURATION_ERROR | 必要配置缺失或非法，启动阶段处理 |
| AnalysisTimeout | ANALYSIS_TIMEOUT | 分析失败，允许用户重试 |
| LLMUpstreamError | LLM_UPSTREAM_ERROR | 网络、鉴权、上游 HTTP 失败或模型拒绝 |
| LLMInvalidOutput | LLM_INVALID_OUTPUT | JSON、字段、证据或 ID 校验失败，整批失败 |
| InputTooComplex | INPUT_TOO_COMPLEX | 超出任务、候选或匹配正文限制，提示拆分或调整候选 |
| Pydantic ValidationError | 由后端映射 | 输入字段或模型构造无效 |
| ValueError | 由后端映射 | 草稿 ID 重复、候选为空或同项候选 ID 重复 |
| asyncio.CancelledError | 继续向上传递 | 保持取消语义，由分析流程收尾 |

AnalysisTimeout 同时继承 TimeoutError 和 AgentError。上游异常和无效输出不能转换为空任务结果。分析失败时整批失败，避免保存部分成功草稿。按团队共用规范映射 HTTP 状态和错误响应体。

日志可记录错误代码、耗时和请求标识。Pydantic 原始错误可能包含输入内容，后端记录时应剔除正文、候选内容和密钥。

## 9 已验证结果与验收命令

在仓库根目录执行。

~~~powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s backend/tests -v
.\.venv\Scripts\python.exe -X utf8 -m backend.tests.live_notices --env-file backend/.env
~~~

第一条使用固定响应验证接口边界，第二条发送真实模型请求并消耗对应 API 额度。

| 已执行验证 | 结果 |
| --- | --- |
| 13 项固定响应测试 | 通过，覆盖字段、日期、证据、候选 ID、超限、超时、取消及并发隔离 |
| 4 条公开高校公告提取 | 通过，覆盖多事项、日期精度、邮箱和链接占位符及延期 |
| 2 次真实匹配调用 | 通过，报名与作品提交为 DIFFERENT，原任务重复和延期可判 SAME |
| 改名后的 extract、match CLI | 两个入口已通过真实模型验证 |

公开公告样例经过节选转述并替换链接或邮箱，输入及断言见 backend/tests/live_notices.py。中科大延期案例的旧候选按公告记载的原截止日期构造。构造的课程样例见 backend/tests/fixtures/，便于复现课程、编号和相对日期待检查。

来源包括[西交大作品提交通知](https://agent.xjtu.edu.cn/aiic/info/1023/1206.htm)、[西交大报名顺延通知](https://agent.xjtu.edu.cn/aiic/info/1023/1208.htm)、[华工校赛通知](https://www2.scut.edu.cn/sfl/2026/0917/c35377a635743/page.htm)和[中科大赛事通知](https://www.ustc.edu.cn/info/1362/26069.htm)。

前期模型调用出现过字段组合校验失败，以及把“提交细节见链接”当作新要求的情况。提示词和引用处理调整后，记录中的重跑通过。UNCERTAIN 和异常路径通过固定响应测试验证，真实通知样本仍需扩充。现有样本量不足以评估开题方案预设的字段准确率指标。

团队后端合并后，应共同核对以下链路。

- 新通知生成草稿，用户确认后创建正式任务。
- 多项通知分别生成可确认的草稿。
- 报告延期时更新日期，并保留未提及的旧提交要求。
- 同课程不同编号的作业分别处理。
- 发布时间未知的“明天提交”显示待检查。
- 模型失败时标记分析失败，重试沿用冻结的参考时间。
- 正式任务在用户确认后按事务保存。

通知上传、数据库查询、草稿状态和确认保存的完整端到端验收，需要在团队后端合并后执行。
