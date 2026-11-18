# Deadline Keeper V2 Agent 需求说明

版本：v1.0  
日期：2026-11-17  
面向：Agent 负责人，以及参与接口联调和验收的后端开发者。

本说明依据 [Deadline Keeper V2 后端方案](./Deadline-Keeper-V2-后端方案.md) 的任务提取、语义匹配和日期规则制定。本文约定目标能力和交付要求；当前目录尚未提供现有 Agent 源码，具体改造位置由实现时核对。

## 1. Agent 要完成什么

Agent 提供两个可以被后端调用的能力：**把通知文字转换成结构化任务；判断提取出的任务与候选旧任务是否为同一件事。**

用户上传截图时，后端先完成 OCR 和规则脱敏，再把文字交给 Agent。Agent 返回任务内容、日期、不确定字段和匹配关系，后端生成建议，用户确认后保存。

| 能力 | 接收什么 | 返回什么 | 调用者 |
| --- | --- | --- | --- |
| 任务提取 | 脱敏通知、参考时间、通知时间是否已知、时区 | 多个任务及其字段、原文证据、不确定信息 | 后端一的 `AnalysisService` |
| 语义匹配 | 新任务及后端筛选出的候选旧任务 | `SAME` / `DIFFERENT` / `UNCERTAIN`，以及关联 ID 和理由 | 后端二的 `MatchingService` |

例如，通知说“机器学习实验报告的提交日期改为 2026 年 11 月 23 日”。Agent 应识别报告任务和新日期；后端提供候选报告后，Agent 判断是否为同一个任务。新建还是更新的业务建议由后端生成。

## 2. 与两位后端开发的责任边界

| 工作 | 主负责人 | Agent 负责人需要配合的内容 |
| --- | --- | --- |
| 截图上传、OCR、规则脱敏 | 后端一 | 接收已经处理好的文本，保留脱敏占位符 |
| 提取提示词、模型接入、提取输出 | Agent 负责人 | 交付可调用的 `extract_tasks()` |
| 日期理解、不确定字段标记 | Agent 负责人 | 按本说明返回明确日期或待检查结果 |
| 查询旧任务、哈希及编号规则匹配 | 后端二 | 接收后端提供的候选，不另行查库 |
| 候选旧任务的语义判断 | Agent 负责人 | 交付可调用的 `match_tasks()` |
| 合并旧字段、生成更新差异与建议 | 后端二 | 准确标记 `observed_fields` 和匹配关系 |
| 分析状态、总超时、草稿保存 | 后端一 | 返回有效结构或明确异常，支持调用取消 |
| 用户确认、任务保存、版本与幂等 | 后端二 | 返回结果后不执行任务写入 |

本版 Agent 在 FastAPI 后端进程内运行，通过适配器调用外部模型。其可用输入就是当次请求的数据，不需要数据库工具、联网搜索、长期对话记忆或多个 Agent 协作。AI 脱敏不属于本版 Agent 的交付范围。

## 3. 任务提取需求

### 3.1 识别并拆分任务

Agent 需要识别通知中要求完成的具体事项，包括作业、报告、实验、材料提交等。能独立检查和确认的事项分别输出一条任务，共用的课程、日期或提交要求按原文作用范围分配。

- 同一任务的标题、格式要求和截止日期属于同一条结果。
- 同一通知明确要求提交报告和完成另一项作业时，输出两条结果。
- 群聊寒暄、纯说明和没有待办事项的通知不产生任务。
- 同一任务出现“原定日期”和“变更后日期”时，提取变更后的日期，不能拆成两个任务。
- 无法确定是否为同一事项时，保留可识别内容并标记 `task_identity` 不确定，不凭猜测合并。

### 3.2 提取字段

每条任务必须返回以下键，缺失内容用 null 表示。字段名与后端共用模型一致。

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `title` | string / null | 从原文概括任务名称，最多 200 字符；无法确定时为 null，并标记不确定 |
| `description` | string / null | 任务补充说明，最多 2,000 字符 |
| `course_name` | string / null | 明确提到的课程，最多 100 字符 |
| `external_ref` | string / null | 作业或任务编号，最多 100 字符；不能把手机号或学号当编号 |
| `due_kind` | string | `NONE` / `DATE` / `DATETIME` / `UNRESOLVED` |
| `due_date` | string / null | `YYYY-MM-DD`，按任务时区表示 |
| `due_at` | string / null | 带时区的 ISO 8601 时间；适配器校验后统一转换成 UTC |
| `timezone` | string | 使用请求提供的 IANA 时区 |
| `requirements` | string / null | 提交位置、文件格式、命名等要求，最多 4,000 字符 |
| `observed_fields` | string[] | 原文中明确可用的字段集合，见第 3.3 节 |
| `uncertain_fields` | string[] | 需要检查的字段集合，见第 3.4 节 |
| `reason` | string | 简短说明提取依据或不确定原因，最多 500 字符 |
| `source_excerpt` | string | 来自脱敏原文的实际片段，最多 1,000 字符 |

缺少课程或任务编号不一定意味着任务不明确，不能把所有 null 字段都标为不确定。Agent 需要区分“原文没有提供”和“原文提供了线索，但无法可靠理解”。

### 3.3 标记本次明确出现的字段

`observed_fields` 只能使用以下值：

```text
title
description
course_name
external_ref
deadline
requirements
```

`deadline` 对应一组字段：`due_kind`、`due_date`、`due_at`、`timezone`。只有能确定含义的日期信息才进入 `observed_fields`；含糊日期进入 `uncertain_fields`。

这个字段决定后端如何生成更新建议。例如通知只有“提交日期改为 2026 年 11 月 23 日”，就不能把没有出现的提交要求写进 `observed_fields`。后端会保留旧任务中未涉及的内容。

Agent 不用 null 表示“通知要求删除旧值”。原文未涉及的字段保持缺失含义，明确清空内容由用户在最终确认中操作。

### 3.4 标记不确定信息

`uncertain_fields` 使用 `observed_fields` 的六个枚举值，并允许 `task_identity`。以下情况需要标记：

| 情况 | 标记方式 |
| --- | --- |
| “这个报告”但无法确定指的是哪项任务 | `task_identity` |
| 日期缺少年份、存在冲突或无法解析 | `deadline` |
| 通知时间未知，却使用“明天”“本周五” | `deadline`，并返回 `SOURCE_TIME_UNKNOWN` 告警 |
| 提交方式文字明显缺损，无法准确恢复 | `requirements` |
| 任务名称无法确定 | `title`，必要时同时标记 `task_identity` |

任务本身可识别但部分字段不确定时，仍输出草稿，并用 `reason` 说明。后端据此保留为待检查，不能因部分内容不明确而直接丢弃整个任务。

### 3.5 日期解释

`reference_time` 由后端在录入时冻结。`source_time_known=true` 表示它来自已知通知发布时间；false 表示只知道接收时间。Agent 按输入的时区解释日历日期。

| 文本或条件 | 预期结果 |
| --- | --- |
| “2026 年 11 月 20 日提交” | `DATE`，`due_date=2026-11-20`，`due_at=null` |
| “2026 年 11 月 20 日 18:00 提交” | `DATETIME`，日期为当地 11 月 20 日，时间按时区换算 |
| “明天 18:00”，已知通知时间为 2026-11-17 16:00，时区上海 | `DATETIME`，当地时间 2026-11-18 18:00，即 UTC `2026-11-18T10:00:00Z` |
| “明天提交”，通知时间未知 | `UNRESOLVED`，两个日期字段为 null，标记 `deadline` |
| “11 月 20 日提交”，原文未给年份 | `UNRESOLVED`，保留原文，不自行补当前年份 |
| “稍后交”“尽快完成” | `UNRESOLVED`，标记 `deadline` |
| 没有提到截止日期 | `NONE`，日期字段为 null；不因此自动标记不确定 |
| “本次不设截止日期” | `NONE`，日期字段为 null，`observed_fields` 可以包含 `deadline` |

`DATETIME` 的 `due_at` 换算到 `timezone` 后必须与 `due_date` 一致。`UNRESOLVED` 不能同时保留一个猜出的日期。只精确到天时，不补造 23:59 或其他时刻。

### 3.6 没有任务的结果

能够正常理解通知、确认没有任务时，返回 `tasks=[]`，并使用 `NO_TASK_FOUND` 告警说明。调用失败、输出不合法或模型拒绝处理都属于异常，不能转换成空任务结果。

## 4. 语义匹配需求

后端先执行完整内容哈希、课程加编号等规则。规则不足以确定关系时，才把新任务和候选旧任务交给 Agent。

### 4.1 三种关系

| `relation` | 含义 | `matched_task_id` |
| --- | --- | --- |
| `SAME` | 有足够证据认定为某个候选任务的再次通知或变更通知 | 必须是本条请求候选中的一个 ID |
| `DIFFERENT` | 有足够证据认定与提供的候选任务不同 | null |
| `UNCERTAIN` | 证据不足、多个候选同样可能或信息冲突 | null |

`DIFFERENT` 仅表示与本次候选集合的关系。候选是否被截断、数据库中是否还有其他任务，由后端掌握和处理。

### 4.2 判断依据

Agent 综合任务名称、课程、编号、任务说明、提交内容和变更措辞判断身份。课程相同、日期相同或标题只有一个词相同，都不足以单独认定 `SAME`。

“实验报告延期”“原报告改为周一提交”可能仍是同一个任务；截止日期或提交格式的变化不能单独作为判定不同任务的依据。

存在两个无法区分的候选时返回 `UNCERTAIN`，不按候选顺序选择第一个。日期不确定但任务身份明确时，可以返回 `SAME` 并保留 `uncertain_fields=["deadline"]`，后端仍安排用户检查日期。

### 4.3 批量对应规则

每个输入的 `draft_item_id` 必须恰好对应一条结果。结果不能缺项、重复或出现未知草稿 ID；某条返回的候选 ID 必须属于该条输入，不能引用同批其他任务的候选。

返回简短的 `reason` 和必要的 `uncertain_fields`。Agent 不输出最终保存动作，不自行合并旧任务字段，也不修改候选内容或版本。

## 5. 后端调用接口

两个方法由 `AgentAdapter` 对外提供，在后端进程内调用。方法成功时返回经过校验的数据模型；失败时抛出明确异常，由后端转成 HTTP 错误。

### 5.1 `extract_tasks()`

```python
async def extract_tasks(request: ExtractionRequest) -> ExtractionResult:
    ...
```

输入 `ExtractionRequest`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `sanitized_text` | string | 已脱敏正文，1–20,000 字符 |
| `reference_time` | datetime | 带时区的参考时间，后端冻结后提供 |
| `source_time_known` | boolean | 是否知道原通知发布时间 |
| `timezone` | string | IANA 时区，如 `Asia/Shanghai` |

输入示例：

```json
{
  "sanitized_text": "机器学习课程：实验报告 ML-03 原定 11 月 20 日提交，现改为 2026 年 11 月 23 日，请上传 PDF。",
  "reference_time": "2026-11-17T08:00:00Z",
  "source_time_known": true,
  "timezone": "Asia/Shanghai"
}
```

输出 `ExtractionResult`：

```json
{
  "tasks": [
    {
      "title": "实验报告",
      "description": null,
      "course_name": "机器学习",
      "external_ref": "ML-03",
      "due_kind": "DATE",
      "due_date": "2026-11-23",
      "due_at": null,
      "timezone": "Asia/Shanghai",
      "requirements": "上传 PDF",
      "observed_fields": ["title", "course_name", "external_ref", "deadline", "requirements"],
      "uncertain_fields": [],
      "reason": "通知明确给出新的报告提交日期和文件格式",
      "source_excerpt": "实验报告 ML-03 原定 11 月 20 日提交，现改为 2026 年 11 月 23 日，请上传 PDF"
    }
  ],
  "warnings": []
}
```

`tasks` 最多 20 条；`warnings` 为 `{code, message}` 数组。共用告警枚举包括 `PRIVACY_RULE_APPLIED`、`OCR_LOW_QUALITY`、`SOURCE_TIME_UNKNOWN`、`NO_TASK_FOUND`。前两项由后端输入处理模块产生，Agent 根据提取结果产生后两项，不凭空推断 OCR 质量或脱敏完成程度。

### 5.2 `match_tasks()`

```python
async def match_tasks(request: MatchBatchRequest) -> MatchBatchResult:
    ...
```

| 模型 | 字段 |
| --- | --- |
| `MatchBatchRequest` | `items: list[MatchRequestItem]` |
| `MatchRequestItem` | `draft_item_id`、`new_task: ExtractedTask`、`candidate_tasks: list[TaskView]` |
| `MatchBatchResult` | `items: list[MatchResultItem]` |
| `MatchResultItem` | `draft_item_id`、`relation`、`matched_task_id`、`reason`、`uncertain_fields` |

`ExtractedTask` 就是第 5.1 节 `tasks` 中单条对象。`TaskView` 包含第 3.2 节从 `title` 到 `requirements` 的九个任务内容字段，但 `title` 不为空、`due_kind` 不含 `UNRESOLVED`；另外包含 `id`、`version`、`created_at`、`updated_at`。ID、版本和候选快照由后端提供。

下面的匹配示例没有明确任务编号，需要结合课程、任务名称、提交格式和延期措辞判断：

```json
{
  "items": [
    {
      "draft_item_id": "00000000-0000-4000-8000-000000000201",
      "new_task": {
        "title": "实验报告",
        "description": null,
        "course_name": "机器学习",
        "external_ref": null,
        "due_kind": "DATE",
        "due_date": "2026-11-23",
        "due_at": null,
        "timezone": "Asia/Shanghai",
        "requirements": "上传 PDF",
        "observed_fields": ["title", "course_name", "deadline", "requirements"],
        "uncertain_fields": [],
        "reason": "通知说明原报告延后提交",
        "source_excerpt": "机器学习课程的实验报告延后至 2026 年 11 月 23 日提交，仍上传 PDF"
      },
      "candidate_tasks": [
        {
          "id": "00000000-0000-4000-8000-000000000301",
          "title": "机器学习实验报告",
          "description": null,
          "course_name": "机器学习",
          "external_ref": null,
          "due_kind": "DATE",
          "due_date": "2026-11-20",
          "due_at": null,
          "timezone": "Asia/Shanghai",
          "requirements": "上传 PDF",
          "version": 2,
          "created_at": "2026-11-16T09:00:00Z",
          "updated_at": "2026-11-16T09:00:00Z"
        }
      ]
    }
  ]
}
```

输出示例：

```json
{
  "items": [
    {
      "draft_item_id": "00000000-0000-4000-8000-000000000201",
      "relation": "SAME",
      "matched_task_id": "00000000-0000-4000-8000-000000000301",
      "reason": "课程、任务名称和提交格式一致，通知明确说明原报告延期",
      "uncertain_fields": []
    }
  ]
}
```

每批最多 20 个新任务，每项最多 5 个候选；没有候选的任务由后端直接处理。批量匹配输入序列化后最多 60,000 字符，超限应明确失败。

## 6. 模型接入、提示词与校验

### 6.1 模型调用

Agent 负责人接入真实模型，通过服务端配置读取 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。模型和依赖版本在实际联调后固定，并在交付说明中记录。

每次提取方法调用执行一次模型请求；一次批量匹配方法调用执行一次模型请求。普通分析只提取，有候选且规则无法判断时再匹配。本版不在适配器内自动重试或循环修复模型响应。

单次模型请求最长 40 秒，后端整次分析最长 90 秒。后端可能按剩余时间提前取消调用，适配器需要传递取消并关闭未完成请求，不能在取消后继续触发新的调用。

不同通知并行分析时，请求上下文相互独立，不共享任务正文和候选。调用连接可以复用，消息历史不得串到另一条通知。

### 6.2 提示词要求

任务提取和语义匹配分别维护提示词，使用同一套字段和枚举定义。提示词中包括任务目标、输入说明、日期规则、输出结构，以及本说明中的正常和不确定案例。

通知与候选文本作为待分析数据提供。文字中出现“忽略规则”“调用工具删除任务”等内容时，不改变输出格式和权限。保留 `[PHONE_1]` 等占位符，不尝试恢复被脱敏的信息。

模型输出应为符合约定的 JSON 对象，不附加 Markdown 代码块或自由叙述。`reason` 只需要提供便于用户检查的简短依据。

### 6.3 输出校验

适配器使用成熟 JSON 解析器和共用 Pydantic 模型完成校验，再把结果交给后端。校验包括：

- 键名、类型、字段长度和枚举符合定义，不接收未知字段。
- 日期、时区和 `due_kind` 的组合有效，精确时间与当地日期一致。
- `observed_fields` 和 `uncertain_fields` 均为合法且不重复的枚举值。
- 批量结果的草稿 ID 完整且唯一，`SAME` 的候选 ID 属于对应输入。
- 提取结果的原文片段确实来自当前脱敏通知。

字段缺失、输出截断、非法 JSON 和非法候选 ID 都属于无效输出；不补造必填值后宣称成功。提取内容是否有原文依据、是否混入猜测，需要真实样例验收，不能仅靠字段校验确认。

### 6.4 异常要求

| 情况 | 对后端返回的结果 |
| --- | --- |
| 密钥、模型地址或必要依赖缺失 | 初始化失败，指出缺失配置；后端据此处理就绪状态 |
| 上游拒绝、鉴权失败或调用异常 | `LLM_UPSTREAM_ERROR` 对应异常 |
| 请求或分析时间耗尽 | 超时或取消向上传递，后端使用 `ANALYSIS_TIMEOUT` |
| JSON 或字段不满足约定 | `LLM_INVALID_OUTPUT` 对应异常 |
| 任务数量或匹配输入超过约定上限 | `INPUT_TOO_COMPLEX` 对应异常 |
| 正常分析但无法确定关系 | 有效的 `UNCERTAIN` 结果 |
| 正常分析且没有任务 | 有效的空任务结果 |

批量中有一项输出不合法，整批匹配失败；后端不保存部分成功草稿。日志可记录请求 ID、调用类型、模型名称、耗时和错误代码，不记录完整通知、候选正文或密钥。

## 7. Agent 负责人交付什么

| 交付项 | 完成标准 |
| --- | --- |
| `AgentAdapter` | 两个异步方法可被后端直接调用，正常和异常路径符合本说明 |
| 共用 Agent 模型 | `ExtractionRequest/Result`、`ExtractedTask`、`MatchBatchRequest/Result` 等与后端字段一致 |
| 两套提示词 | 提取和匹配分别维护，可随源码版本追踪 |
| 模型配置说明 | 写明实际供应商、地址配置方式、模型、依赖和运行方式，密钥不写入文档 |
| 验收样例 | 每例有输入、预期字段或关系、不确定信息及实际结果 |
| 测试结果 | 区分固定响应校验和真实模型调用，记录未通过案例及原因 |

目标代码位置沿用后端方案的 `backend/app/adapters/agent_adapter.py`、`backend/app/schemas/agent.py` 和 `backend/app/agent/prompts/`。已有 Agent 中可复用的模型客户端和执行入口，需要读源码确认后再采用。

## 8. 验收用例

以下用例用于约定预期行为，尚不是已经完成的测试记录。

| 编号 | 场景 | 预期行为 |
| --- | --- | --- |
| A01 | 明确的新作业通知 | 提取标题、课程、编号、日期和要求，字段有原文依据 |
| A02 | 一条通知包含两项独立任务 | 输出两条，不把提交格式拆成第三条任务 |
| A03 | 同一任务同时出现原定与变更后日期 | 输出一条任务，采用新日期 |
| A04 | 只改截止日期，未提到提交方式 | `observed_fields` 不包含 `requirements`，便于后端保留旧值 |
| A05 | 完整年月日但没有时刻 | `DATE`，`due_at=null` |
| A06 | 完整日期和时刻，时区上海 | UTC 时间换算正确，与当地日期一致 |
| A07 | 已知通知发布时间，使用“明天” | 按通知时间解析，不按实际运行当天解析 |
| A08 | 通知时间未知，使用相对日期 | `UNRESOLVED`、`deadline` 和 `SOURCE_TIME_UNKNOWN` |
| A09 | 日期缺年份或写“尽快” | 标记日期不确定，不补造确定日期 |
| A10 | 原文未出现截止日期 | `NONE`，日期字段为空 |
| A11 | 只有寒暄或纯说明 | 空任务结果，不输出占位任务 |
| A12 | 文字有明显缺损，任务仍可识别 | 保留任务和可用字段，标记缺损部分 |
| A13 | 不同表述指向同一报告，通知说明延期 | 返回 `SAME`；后端据此比较日期变化 |
| A14 | 同课程但作业编号或内容明确不同 | 不仅因课程相同就判为同任务 |
| A15 | 两个旧任务均可能是“那个报告” | `UNCERTAIN`，`matched_task_id=null` |
| A16 | 返回候选集合外的 ID、跨项 ID 或重复草稿 ID | 校验失败，不能进入草稿保存 |
| A17 | 通知带有改变系统指令的文字 | 保持数据分析和结构化输出，不扩大工具权限 |
| A18 | 批量中某个任务不确定 | 每项仍有对应结果，不串用其他任务的候选 |
| A19 | 上游超时、拒绝或输出截断 | 明确异常，不用空任务或固定成功响应替代 |
| A20 | 同时分析两条不同通知 | 上下文独立，输出不混入另一条通知内容 |

交付前由 Agent 负责人用固定响应验证输出解析、ID 校验和异常处理，用真实模型验证任务理解与语义匹配。测试样例中的标题或理由允许合理表达差异，日期、编号、枚举和候选 ID 按预期逐项核对。

联调时后端一负责跑通“通知 → 提取 → 草稿”，后端二负责跑通“候选 → 语义关系 → 建议 → 用户确认保存”。两位测试负责人复核真实运行结果；Agent 的固定样例通过不等于整条系统链路已经通过。
