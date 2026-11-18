# deadline_keeper_research_demo 核心技术深度解析

本文聚焦**可迁移的 Agent 设计模式**，不逐行解释语法，而是提炼你用得到的架构思想和工程技巧。每条技术点都标注了精确的代码位置。

---

## 目录

1. [StateGraph：多节点工作流的骨架](#1-stategraph多节点工作流的骨架)
2. [结构化输出：从 LLM 文本到确定性的控制流](#2-结构化输出从-llm-文本到确定性的控制流)
3. [Agent + Tools 循环：ReAct 模式在 LangGraph 中的实现](#3-agent--tools-循环react-模式在-langgraph-中的实现)
4. [自检循环：让 LLM 评判自己的输出](#4-自检循环让-llm-评判自己的输出)
5. [工具设计：docstring 就是 prompt](#5-工具设计docstring-就是-prompt)
6. [API 封装：Pydantic BaseModel + 指数退避重试](#6-api-封装pydantic-basemodel--指数退避重试)
7. [多 LLM 实例策略：同一模型，不同配置](#7-多-llm-实例策略同一模型不同配置)
8. [流式执行 + 进度指示](#8-流式执行--进度指示)
9. [跨架构可复用的设计决策清单](#9-跨架构可复用的设计决策清单)

---

## 1. StateGraph：多节点工作流的骨架

**核心文件：** `state.py:9-14` (状态定义), `graph.py:165-198` (图构建)

### 1.1 状态设计的关键：`add_messages` reducer

```python
# state.py:9-14
class AgentState(TypedDict):
    requires_research: bool
    num_feedback_requests: int
    is_good_answer: bool
    messages: Annotated[Sequence[BaseMessage], add_messages]  # ← 关键
```

`Annotated[Sequence[BaseMessage], add_messages]` 不是普通的类型标注。`add_messages` 是一个 **reducer 函数**：当多个节点都返回 `{"messages": [...]}` 时，它不是覆盖，而是**追加**。

这意味着每个节点函数只需要关心自己产生的消息：

```python
# graph.py:65-68 — 节点只返回自己新增的消息
def decision_making_node(state: AgentState) -> dict:
    ...
    return {"requires_research": True, "messages": [AIMessage(...)]}
    #       ↑ 覆盖普通字段                      ↑ 追加到消息历史
```

**可迁移到任何 LangGraph 项目：**
- 需要累积的列表型数据（消息、文档、步骤记录）→ 写一个 reducer
- 需要覆盖的标量型数据（状态标记、计数器）→ 普通 TypedDict 字段
- 不要手动拼接历史，让 reducer 帮你做

### 1.2 条件路由：用函数返回值决定路径

```python
# graph.py:71-73 — 路由函数返回字符串，作为边的 key
def router(state: AgentState) -> str:
    return "planning" if state["requires_research"] else "end"

# graph.py:180-184 — 注册条件边
workflow.add_conditional_edges(
    "decision_making",             # 源节点
    router,                        # 路由函数 (state → "branch_name")
    {"planning": "planning",       # router返回值 → 目标节点
     "end": END},                  # "end" → 特殊节点 END
)
```

**路由函数的设计原则：**
- 入参是完整 state，不是某个字段——你可以根据任意 state 信息做判断
- 返回字符串，必须是 `add_conditional_edges` 第三个参数 dict 的 key
- `END` 是 LangGraph 内置的终止节点

### 1.3 图的结构：先注册节点，再连线

```python
# graph.py:165-198 — 完整的构建模式
workflow = StateGraph(AgentState)  # 泛型绑定 state 类型

workflow.add_node("decision_making", decision_making_node)  # 注册
workflow.add_node("planning", planning_node)
# ...

workflow.set_entry_point("decision_making")  # 入口（只能有一个）

workflow.add_conditional_edges("decision_making", router, {...})  # 条件边
workflow.add_edge("planning", "agent")                            # 固定边
# ...

return workflow.compile()  # 编译成可执行的 CompiledStateGraph
```

**两种边的区别：**

| | `add_edge` | `add_conditional_edges` |
|---|---|---|
| 何时用 | A 节点之后**总是**去 B | A 节点之后根据 state **动态选择** |
| 签名 | `(from, to)` | `(from, router_fn, {return_value: to_node})` |

---

## 2. 结构化输出：从 LLM 文本到确定性的控制流

**核心文件：** `state.py:17-36` (输出模型), `graph.py:44-50` (LLM 配置)

### 2.1 问题：为什么不能直接让 LLM return "yes" 或 "no"？

你需要一个布尔值来决定 `router()` 走哪条边。LLM 返回的文本是不可靠的——它可能返回 `"Yes."`、`"yes"`、`"YES"`、`"需要"`……任何字符串解析都有漏判风险。

### 2.2 方案：`with_structured_output` + Pydantic

```python
# state.py:17-25 — 定义期望的输出结构
class DecisionMakingOutput(BaseModel):
    requires_research: bool = Field(
        description="Whether the user query requires paper research"
    )
    answer: Optional[str] = Field(
        default=None,
        description="Direct answer, or None if research needed",
    )

# graph.py:44-46 — 创建专用 LLM 实例
decision_making_llm = base_llm.with_structured_output(
    DecisionMakingOutput, method="function_calling"
)
#  ↑ Pydantic 模型             ↑ 用 function calling 实现（DeepSeek 兼容）
```

**`with_structured_output` 做了什么：**

1. 把 `DecisionMakingOutput` 的 JSON Schema 注入 LLM 调用参数
2. LLM 返回的 JSON 自动被 Pydantic 校验——`response.requires_research` 一定是 `bool`
3. 如果 LLM 返回的 JSON 不符合 Schema，LangChain / Pydantic 会抛异常（而不是静默出错）

**使用时：**

```python
# graph.py:62-64
response: DecisionMakingOutput = decision_making_llm.invoke(...)
#        ↑ 类型注解获得 IDE 补全      ↑ 返回的是 Pydantic 对象，不是 dict

output["requires_research"] = response.requires_research  # 确定是 bool
if response.answer:                                        # 确定是 str 或 None
    output["messages"] = [AIMessage(content=response.answer)]
```

### 2.3 `method="function_calling"` 的含义

```python
base_llm.with_structured_output(Model, method="function_calling")
```

LangChain 有三种实现结构化输出的方法：

| method | 原理 | DeepSeek 兼容性 |
|---|---|---|
| `"function_calling"` | 假装 Model 是一个 tool，让 LLM 以 function call 形式返回 JSON | ✅ 兼容 |
| `"json_mode"` | 用 `response_format={"type": "json_object"}` | ⚠️ 可能不稳定 |
| `"json_schema"` | 用 `response_format={"type": "json_schema", "schema": ...}` | ❌ DeepSeek 不支持 |

**选 `"function_calling"` 的原因：** DeepSeek 的 function calling 实现成熟稳定，且相对于 OpenAI 官方的 `json_schema` 模式，function calling 是所有 OpenAI 兼容 API 的"最大公约数"——换任何国产模型都能用。

### 2.4 可迁移结论

任何需要"LLM 输出决定程序走向"的场景，都用这个模式：

```
Pydantic Model (输出结构)
    +
with_structured_output(method="function_calling")
    =
类型安全的、可路由的 LLM 输出
```

---

## 3. Agent + Tools 循环：ReAct 模式在 LangGraph 中的实现

**核心文件：** `graph.py:28-29` (工具注册), `47` (bind_tools), `94-129` (tools/agent 节点)

### 3.1 两个关键 LLM 配置

```python
# graph.py:47 — 关键：把工具列表"绑"到 LLM 上
agent_llm = base_llm.bind_tools(tools)
```

`bind_tools` 做了什么：把每个 Tool 的 name、description、args_schema 注入到 LLM 的 API 调用参数中。DeepSeek 收到这些信息后，会在需要时回复 `tool_calls` 而不是纯文本。

### 3.2 循环机制

```
agent_node (graph.py:116-123)
    │
    ├─ LLM 返回 tool_calls → should_continue 返回 "continue"
    │       │
    │       ▼
    │   tools_node (graph.py:94-110) 执行工具，返回 ToolMessage
    │       │
    │       └─→ agent_node (LLM 看到工具结果，决定是否继续调工具)
    │
    └─ LLM 返回纯文本 → should_continue 返回 "end" → 进入 judge
```

**`should_continue` 函数（`graph.py:126-129`）：**

```python
def should_continue(state: AgentState) -> str:
    last_message = state["messages"][-1]
    return "continue" if last_message.tool_calls else "end"
```

`AIMessage.tool_calls` 是一个列表，包含 LLM 请求调用的工具。核心约定：
- `tool_calls` 非空 → LLM 需要更多信息，继续调工具
- `tool_calls` 为空 → LLM 认为任务完成，可以输出最终答案

### 3.3 工具执行节点的关键细节

```python
# graph.py:94-110
def tools_node(state: AgentState) -> dict:
    last_message = state["messages"][-1]  # 取最后一条 AIMessage
    outputs = []
    for tool_call in last_message.tool_calls:
        tool_result = tools_dict[tool_call["name"]].invoke(tool_call["args"])
        #                         ↑ 按名字查找      ↑ 执行工具
        outputs.append(
            ToolMessage(
                content=json.dumps(tool_result),  # 工具返回值必须序列化为字符串
                name=tool_call["name"],           # 匹配 tool_call 的 name
                tool_call_id=tool_call["id"],     # 匹配 tool_call 的 id — 必须！
            )
        )
    return {"messages": outputs}
```

**三个必须匹配的字段：** `name`、`tool_call_id` 必须与 LLM 请求的 tool_call 一一对应，否则 LangChain 消息解析层会出错。这是最容易踩坑的地方。

### 3.4 可迁移结论

Agent 循环的本质是 `LLM思考 → 执行工具 → LLM再思考 → ...` 的交替。在 LangGraph 中实现就是两个节点 `agent` 和 `tools`，通过条件边连成回路。适用于任何"LLM 需要多步调用外部工具"的场景。

---

## 4. 自检循环：让 LLM 评判自己的输出

**核心文件：** `graph.py:135-154` (judge_node), `48-50` (judge_llm)

### 4.1 为什么需要自检

单次 LLM 调用可能：
- 漏掉了用户问题中的某个维度
- 引用了不存在的论文
- 忽略了之前的反馈

自检循环让 LLM 以"审稿人"视角再审视一遍自己的答案。

### 4.2 实现

```python
# graph.py:135-154
def judge_node(state: AgentState) -> dict:
    # ① 安全阀：最多重试 2 次
    num_feedback = state.get("num_feedback_requests", 0)
    if num_feedback >= 2:
        return {"is_good_answer": True}  # 强制通过

    # ② LLM 自我评判
    system = SystemMessage(content=JUDGE_PROMPT)
    response: JudgeOutput = judge_llm.invoke([system] + state["messages"])

    # ③ 返回评判结果
    output = {
        "is_good_answer": response.is_good_answer,
        "num_feedback_requests": num_feedback + 1,  # 计数器 +1
    }
    if response.feedback:
        output["messages"] = [AIMessage(content=response.feedback)]
        # 反馈作为新消息追加到 history → planning 节点能读到它
    return output
```

### 4.3 反馈如何驱动重新规划

```
judge 返回 feedback → final_answer_router 返回 "planning"
    → planning 节点被重新调用
    → planning 读取 state["messages"]，其中包含 feedback
    → PLANNING_PROMPT 中明确写了 "incorporate feedback in your new planning"
    → 新计划针对 feedback 调整
```

**`prompts.py:22` 这一行很关键：**

```
If feedback about a previous answer is provided, incorporate it in your new planning.
```

没有这句话，LLM 可能忽略 feedback。

### 4.4 评判标准的设计

```python
# prompts.py:80-84
A good final answer should:
- Directly answer the user query
- Answer extensively
- Take into account any feedback
- Provide inline sources
```

评判标准要**具体**、**可验证**。不要写"好的答案应该质量高"这种废话——要写 LLM 能逐条检查的具体条件。

### 4.5 可迁移结论

自检循环 = `judge_llm(Pydantic输出) + 计数器上限(防无限) + feedback → 重新规划`。适用于任何需要"质量把关"的 Agent 场景（报告生成、代码审查、翻译校对）。

---

## 5. 工具设计：docstring 就是 prompt

**核心文件：** `tools.py:88-104` (search_papers), `107-159` (download_paper)

### 5.1 工具的三要素

```python
# tools.py:88-104 — 一个完整的 LangChain Tool
@tool("search-papers", args_schema=SearchPapersInput)  # ① name + 参数Schema
def search_papers(query: str, max_papers: int = 1) -> str:
    """Search for scientific papers using the CORE API.          # ② docstring = 工具描述

    Supports complex queries with field filters (yearPublished,
    authors, title, etc.) and boolean operators (AND, OR).

    Example:                                                      # ③ 调用示例
    {"query": "attention mechanism AND yearPublished:2023", "max_papers": 5}

    Returns:
        Formatted list of papers with ID, title, authors, abstract, and URLs.
    """
```

| 要素 | 谁在用 | 作用 |
|---|---|---|
| `@tool("name")` | LangChain 框架 | 注册工具名称 |
| `args_schema` | LLM | 知道该传什么参数、参数类型 |
| **docstring** | **LLM** | **决定何时调用此工具的唯一依据** |
| Example | LLM | Few-shot 示范正确的调用格式 |

### 5.2 docstring 是 LLM 的"使用说明书"

LLM 看到的工具描述就是这个 docstring。它是 LLM 决定"要不要调这个工具"的唯一信息源。所以：

- **要写清楚这个工具是干什么的** — 别只写"Download paper"，要写"Download a paper PDF, save locally, and extract text"
- **要写参数的含义和约束** — `max_papers` 的范围是 1-10（在 `SearchPapersInput` 的 `Field` 中定义）
- **要写调用示例** — LLM 的 few-shot 能力很强，给一个 JSON 示例能显著提高调用准确率

### 5.3 args_schema 的约束能力

```python
# state.py:39-47
class SearchPapersInput(BaseModel):
    query: str = Field(description="CORE API 搜索查询语句")
    max_papers: int = Field(
        default=1,
        ge=1,     # ≥ 1
        le=10,    # ≤ 10  — LLM 看到这个约束就不会要 100 篇
    )
```

Pydantic 的 `Field` 约束（`ge`, `le`, `description`）会自动转化为 LLM 的 function calling schema，LLM 会遵守这些约束。

### 5.4 错误处理：永远不要让工具抛出未捕获异常

```python
# tools.py:85-88
try:
    return CoreAPIWrapper(...).search(query)
except Exception as e:
    return f"Error searching papers: {e}"  # ← 返回错误字符串，不抛异常
```

因为工具抛出的异常会中断整个 LangGraph 工作流。把错误转成字符串返回，LLM 能读到错误信息并调整策略（比如换关键词重试）。

### 5.5 可迁移结论

写好一个 Tool = **清晰的 docstring + Pydantic args_schema + 调用示例 + 不抛异常**。docstring 的质量直接决定 LLM 调用工具的准确率——它比模型选择和 prompt engineering 对工具调用成功率的影响更大。

---

## 6. API 封装：Pydantic BaseModel + 指数退避重试

**核心文件：** `tools.py:33-83`

### 6.1 为什么用 Pydantic BaseModel 封装 API

```python
# tools.py:33-41
class CoreAPIWrapper(BaseModel):
    base_url: ClassVar[str] = "https://api.core.ac.uk/v3"      # 类常量
    api_key: ClassVar[str] = os.environ.get("CORE_API_KEY", "") # 从环境变量读
    top_k_results: int = Field(default=1)                        # 实例变量

    def _get_search_response(self, query: str) -> dict:
        ...
```

用 Pydantic BaseModel 而非普通 class，是因为：
1. `Field(default=1)` 提供默认值和验证
2. `ClassVar` 明确标注类级别常量（不会被视为实例字段）
3. 可以轻松序列化/反序列化（如果需要的话）

### 6.2 指数退避重试

```python
# tools.py:43-61 — 生产级的重试逻辑
max_retries = 5
for attempt in range(max_retries):
    response = http.request(...)
    if 200 <= response.status < 300:
        return json.loads(response.data.decode("utf-8"))
    elif attempt < max_retries - 1:
        time.sleep(2 ** (attempt + 2))   # 2^2=4s, 2^3=8s, 2^4=16s, 2^5=32s, 2^6=64s
    else:
        raise Exception(f"CORE API returned {response.status}")
```

**指数退避的公式 `2 ** (attempt + 2)`：**

| attempt | 延迟 | 累计 |
|---|---|---|
| 0 (第一次重试) | 2² = 4s | 4s |
| 1 (第二次重试) | 2³ = 8s | 12s |
| 2 (第三次重试) | 2⁴ = 16s | 28s |
| 3 (第四次重试) | 2⁵ = 32s | 60s |
| 4 (最后一次) | 抛异常 | — |

不是等间隔重试，而是越来越长——给服务端恢复时间。这是网络编程的通用做法，不只是 AI 领域。

### 6.3 把 API 响应格式化为 LLM 可读的文本

```python
# tools.py:63-83
def search(self, query: str) -> str:
    response = self._get_search_response(query)
    results = response.get("results", [])
    if not results:
        return "No relevant results were found"

    docs = []
    for r in results:
        # 把 JSON 字段拼接成有结构的文本
        docs.append(
            f"* ID: {r.get('id', '')}\n"
            f"* Title: {r.get('title', '')}\n"
            f"* Published: {published}\n"
            f"* Authors: {authors}\n"
            f"* Abstract: {r.get('abstract', '')}\n"
            f"* URLs: ..."
        )
    return "\n-----\n".join(docs)  # 分隔符让 LLM 区分不同论文
```

**关键点：** API 返回的原始 JSON 直接给 LLM 是低效的——太多冗余字段。格式化后的字符串：
- 减少了 token 消耗
- 结构化分隔符（`-----`）让 LLM 更容易区分不同论文
- 字段用 `*` 标记，模拟 Markdown 列表，LLM 对这种格式理解最好

### 6.4 可迁移结论

封装外部 API 的标准模式：`Pydantic BaseModel + 指数退避 + JSON→文本格式化`。无论接什么外部服务（搜索 API、数据库、RSS feed），这三板斧都适用。

---

## 7. 多 LLM 实例策略：同一模型，不同配置

**核心文件：** `graph.py:31-50`

### 7.1 三个 LLM 实例，同一个 base

```python
# graph.py:34-39 — 基础实例
base_llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    temperature=0.0,  # 所有任务都要确定性输出
)

# graph.py:44-50 — 三个派生实例
decision_making_llm = base_llm.with_structured_output(DecisionMakingOutput, ...)
agent_llm = base_llm.bind_tools(tools)
judge_llm = base_llm.with_structured_output(JudgeOutput, ...)
```

**为什么不用三个独立的 ChatOpenAI 实例？** 因为三个实例共享同一个连接配置（API key、base_url、temperature）。用 `with_structured_output()` 和 `bind_tools()` 从 base 派生，保证配置一致性——改一行 base 配置，所有实例同步更新。

### 7.2 三种配置的用途

| 实例 | 配置 | 用途 | 输出类型 |
|---|---|---|---|
| `base_llm` | 无修饰 | planning 节点（自由文本） | 自然语言计划 |
| `decision_making_llm` | `with_structured_output(DecisionMakingOutput)` | 决策节点 | Pydantic 对象（bool + str） |
| `agent_llm` | `bind_tools(tools)` | 代理执行节点 | tool_calls 或自然语言 |
| `judge_llm` | `with_structured_output(JudgeOutput)` | 评判节点 | Pydantic 对象（bool + str） |

### 7.3 temperature=0.0 的意义

所有 LLM 实例都用 `temperature=0.0`。在 Agent 场景中，你**不想要**"创意"——你需要：
- 决策节点每次给出相同的判断
- 工具调用参数精确可预测
- 评判节点一致的质量标准

`temperature > 0` 适合创意写作，`temperature = 0` 适合 Agent 控制流。

### 7.4 可迁移结论

**用一个 base LLM + 多种 `with_*()` 派生**，而不是创建多个独立实例。这保证了：
- 配置一致性（改一处全改）
- 代码简洁（不用重复 model/api_key/base_url）
- 语义清晰（一看配置就知道这个 LLM 在这个节点干什么）

---

## 8. 流式执行 + 进度指示

**核心文件：** `main.py:38-109`

### 8.1 流式执行 vs 阻塞执行

```python
# 阻塞方式
result = app.invoke({"messages": [user_input]})
# 用户等 30 秒，屏幕一片空白 ← 糟糕的体验

# 流式方式
async for chunk in app.astream(..., stream_mode="updates"):
    # 每个节点完成后立即获得输出 ← 渐进式展示
```

`stream_mode="updates"` 的含义：每次 yield 一个 `{node_name: node_output}` 的 dict。节点一完成，调用方就能拿到输出，不需要等整个图执行完。

### 8.2 流的数据结构

```python
# 每次迭代得到:
# {"decision_making": {"requires_research": True, "messages": [...]}}
# {"planning": {"messages": [AIMessage(...)]}}
# {"agent": {"messages": [AIMessage(tool_calls=[...])]}}
# {"tools": {"messages": [ToolMessage(...)]}}
# {"agent": {"messages": [AIMessage(content="最终答案")]}}
# {"judge": {"is_good_answer": True}}
```

### 8.3 thinking 指示器的实现

```python
# main.py:53-57 — 两个辅助闭包
def _show_thinking():
    print("⏳ thinking...", end="", flush=True)
    #                        ↑ 不换行        ↑ 立即刷新到终端

def _clear_thinking():
    print("\r" + " " * 30 + "\r", end="")
    #       ↑ 回车(回到行首)  ↑ 用空格覆盖  ↑ 再回到行首
```

`\r`（回车）是关键——它不是换行，而是把光标移回行首。用 30 个空格覆盖旧文本，再回到行首，等价于"清除这一行"。

**`flush=True` 也很关键：** Python 的 `print` 默认有缓冲。不加 `flush=True`，输出可能被缓存几秒才显示，thinking 提示就失去了意义。

### 8.4 在每个 chunk 之间的循环

```python
# main.py:59-87
_show_thinking()                    # ① 初始 thinking

async for chunk in app.astream(...):
    _clear_thinking()               # ② 清除：上一轮的 thinking 被覆盖
    for node_name, updates in chunk.items():
        print(f"── {label} ──")     # ③ 打印：节点名称
        # ... 打印消息内容 ...
    print()
    _show_thinking()                # ④ 重现：为下一轮 LLM 等待做准备

_clear_thinking()                   # ⑤ 末尾：清除最后一个 thinking
```

视觉效果：

```
⏳ thinking...                      ← 等待中

── 🔍 分析问题 ──                    ← 节点输出
  [决策] 需要检索论文

⏳ thinking...                      ← 又进入等待

── 📋 制定计划 ──                    ← 下一个节点输出
  [ai] Step 1: Search for...

⏳ thinking...                      ← 又进入等待
...
```

### 8.5 最终回复的提取逻辑

```python
# main.py:90-107 — 找到真正的"最终答案"
for msg in reversed(all_messages):
    if msg.type == "ai" and hasattr(msg, "content") and msg.content:
        if not (hasattr(msg, "tool_calls")
                and msg.tool_calls
                and not msg.content):  # ← 排除纯 tool_calls 消息
            final = msg
            break
```

**为什么要排除纯 tool_calls？** 当 agent 决定调用工具时，返回的 AIMessage 只有 `tool_calls` 没有 `content`（或 content 为空）。这不是"答案"，只是中间步骤。真正有价值的最后消息是包含 `content` 且没有 `tool_calls` 的那条。

### 8.6 可迁移结论

流式输出 + thinking 指示器 = 任何异步 Agent 的标配。核心技巧：
- `astream(stream_mode="updates")` 获取逐节点输出
- `\r` + 空格覆盖实现行内清除
- `flush=True` 禁用缓冲
- 最终答案需要倒序遍历过滤纯 tool_calls 消息

---

## 9. 跨架构可复用的设计决策清单

| 决策 | 本项目选择 | 适用场景 |
|---|---|---|
| **LLM 接入层** | `ChatOpenAI` + `base_url` 指向 DeepSeek | 任何 OpenAI 兼容 API（Qwen, GLM, Moonshot...） |
| **状态管理** | `TypedDict` + `add_messages` reducer | 多节点累积消息的 Agent 工作流 |
| **控制流路由** | Pydantic 结构化输出 → 条件边 | 需要 LLM 决策走哪条分支 |
| **工具集成风格** | `bind_tools` + LangGraph 手动 tools_node | 想完全控制工具调用的时机和逻辑 |
| **工具集成风格 (备选)** | MCP 协议 + `load_mcp_tools` | 工具跨项目共享，或工具与服务端分离 |
| **质量保障** | judge 自检 + 计数器上限 | 需要答案质量把关的严肃场景 |
| **外部 API 调用** | Pydantic BaseModel + 指数退避 | 所有依赖外部网络服务的场景 |
| **LLM 配置管理** | 一个 base + 多个 `with_*()` 派生 | 同一模型用于不同任务的场景 |
| **用户体验** | `astream` 流式 + `\r` thinking 指示器 | 每次 LLM 调用 > 2 秒的交互式应用 |
| **安全性** | `.env` 管理 Key，不提交敏感信息 | 所有项目 |
| **错误处理** | 工具函数永远 return 字符串，不抛异常 | Agent 框架中的外部调用 |
