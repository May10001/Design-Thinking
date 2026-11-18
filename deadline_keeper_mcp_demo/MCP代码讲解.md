# MCP 工具调用 — 核心代码讲解

## MCP 是什么

MCP（Model Context Protocol）定义了 AI 模型与外部工具的通信协议。你可以把它理解为 **"工具调用的 HTTP"**——它规定了工具的发现（`tools/list`）、调用（`tools/call`）、结果返回的格式，而与具体传输方式无关（可以用 stdio、SSE、WebSocket 等）。

这个项目里 MCP 的角色：

```
用户 ─→ AI大模型 ─→ MCP客户端 ─→ MCP服务端 ─→ 数据库
                                    │
                        封装了"怎么查、怎么校验、怎么格式化"
```

---

## 一、三条核心流程

### ❶ 接收客户端指令

客户端通过 MCP 协议发送工具调用请求。对服务端来说，就是 `@mcp.tool()` 装饰的函数被触发：

```python
@mcp.tool()
def query_student(name: str) -> dict:
    # 当客户端发来 tools/call {name: "query_student", arguments: {name: "张三"}}
    # FastMCP 自动解析参数，调用这个函数
    ...
```

你不需要手写 RPC 通信代码，FastMCP 自动处理了参数序列化/反序列化。

### ❷ 封装数据源访问逻辑

这是 MCP 服务端最重要的职责——把底层数据操作隐藏在工具函数内部：

```python
# 底层数据源 — 客户端永远看不到这层
_students_db: list[dict] = [
    {"id": 1, "name": "张三", "score": 92, "grade": "A"},
    ...
]

@mcp.tool()
def query_student(name: str) -> dict:
    # ▼ 封装了查询逻辑：遍历、过滤、空结果处理 ▼
    matches = [s for s in _students_db if name in s["name"]]
    if not matches:
        return _fail(f"未找到名字包含「{name}」的学生")
    return _ok(matches, f"找到 {len(matches)} 条记录")


@mcp.tool()
def add_student(name: str, score: float) -> dict:
    # ▼ 封装了校验逻辑：空值检查、范围检查 ▼
    if not name.strip():
        return _fail("学生姓名不能为空")
    if not (0 <= score <= 100):
        return _fail("分数必须在 0~100 之间")
    # ▼ 封装了写入逻辑：生成 ID、计算评级、写入数据库 ▼
    new_id = max(s["id"] for s in _students_db) + 1
    grade = _compute_grade(score)
    _students_db.append({...})
    return _ok(new_student)
```

**为什么要封装？** 因为大模型不应该知道数据库表结构、字段名、查询语法。它只需要知道"我想查张三"→ 调用 `query_student("张三")` → 拿到结果。数据怎么存的、怎么查的，全部是服务端的内部实现。

### ❸ 返回标准化结果

所有工具返回统一的格式，客户端不需要每个工具单独解析：

```python
# 成功:  {"status": "success", "message": "...", "data": {...}}
# 失败:  {"status": "error",   "message": "...", "data": null}
```

在代码里通过两个工厂函数保证格式一致：

```python
def _ok(data=None, message="操作成功"):
    return {"status": "success", "message": message, "data": data}

def _fail(message="操作失败"):
    return {"status": "error", "message": message, "data": None}
```

每个工具函数末尾只需 `return _ok(...)` 或 `return _fail(...)`，格式不会跑偏。

---

## 二、服务端代码拆解

### FastMCP — 一行启动服务端

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("deadline_keeper_mcp_demo")
```

`FastMCP` 是 MCP SDK 提供的高层封装，它自动处理：
- 协议握手（客户端 `initialize` → 服务端返回能力列表）
- 工具发现（客户端 `tools/list` → 自动列出所有 `@mcp.tool()` 函数签名）
- 工具调用（客户端 `tools/call` → 自动匹配函数、传入参数、返回结果）

### @mcp.tool() — 声明一个可被 AI 调用的工具

```python
@mcp.tool()
def query_student(name: str) -> dict:
```

`@mcp.tool()` 做了两件事：
1. **从函数签名自动生成 tool schema**：参数名 `name`、类型 `str` 自动变成 JSON Schema
2. **从 docstring 自动生成工具描述**：AI 模型根据描述判断什么时候该调用这个工具

所以写清楚 docstring 很重要——它决定了 AI 会不会"选对工具"。

### 内部辅助函数不对外暴露

```python
def _compute_grade(score: float) -> str:
    # 这个函数没有 @mcp.tool() 装饰器
    # 所以客户端看不到它，AI 也调用不了它
```

以 `_` 开头且没有装饰器的函数就是纯内部实现，不会暴露给客户端。

---

## 三、客户端代码拆解

### 建立连接：StdioServerParameters + stdio_client

```python
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(Path(__file__).with_name("math_server.py"))],
)

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()    # MCP 握手
```

`stdio` 是最简单的传输方式——客户端启动服务端作为子进程，通过标准输入/输出通信。不需要网络端口，不需要 HTTP 服务器。

### 加载工具：load_mcp_tools

```python
from langchain_mcp_adapters.tools import load_mcp_tools

tools = await load_mcp_tools(session)
```

这一步发生了什么：

```
客户端发送:  tools/list
服务端返回:  [
              {name: "query_student", parameters: {name: string}, description: "..."},
              {name: "add_student",   parameters: {name, score},  description: "..."},
              {name: "get_top_students", parameters: {n: int},    description: "..."},
            ]

load_mcp_tools 把上面的 MCP Tool 列表
  → 转换为 LangChain 的 BaseTool 对象
  → 可以被 langchain agent 直接使用
```

### Agent 决策 + 调用 — 核心循环

```python
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个学生管理系统助手..."),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
    #             ↑
    #  存放 "思考→调用→结果" 的中间步骤
])

agent = create_tool_calling_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
```

这里是 `agent_scratchpad` 的关键作用。当用户问"张三多少分？"时：

```
第 1 步: LLM 返回 tool_call {name: "query_student", args: {name: "张三"}}
         ↓ 存入 agent_scratchpad
第 2 步: AgentExecutor 执行 query_student("张三")
         → MCP 客户端发送 tools/call 到服务端
         → 服务端返回 {"status": "success", "data": [...], "message": "..."}
         → 结果以 ToolMessage 形式存入 agent_scratchpad
第 3 步: LLM 看到 scratchpad 中有调用记录和结果
         → 决定不需要再调工具，生成最终回复
         → "张三的分数是92分，评级为A"
```

`verbose=True` 会打印每一步，类似：

```
> Entering new AgentExecutor chain...
Invoking: query_student with {'name': '张三'}
Got output: {"status": "success", "message": "找到 1 条记录", "data": [...]}
张三的分数是92分，评级为A。
> Finished chain.
```

---

## 四、一条完整请求的时序

```
🧑 用户: "帮我查一下张三的成绩，再告诉我排名前三的学生"
    │
    ▼
┌─ AgentExecutor ─────────────────────────────────────────────────┐
│                                                                 │
│  ① LLM 思考:                                                    │
│     "需要先查张三，再查排名。先调 query_student('张三')"          │
│                                                                 │
│  ② MCP 客户端 ──tools/call──→ MCP 服务端                        │
│     {name: "query_student", arguments: {name: "张三"}}          │
│                              │                                  │
│                              ❸ 服务端执行:                       │
│                                 _students_db 中搜索 "张三"       │
│                                 命中 → _ok(matches)              │
│                                                                 │
│  ④ MCP 客户端 ←──返回结果── MCP 服务端                           │
│     {"status":"success","data":[{"name":"张三","score":92}]}    │
│                                                                 │
│  ⑤ LLM 思考: "张三92分，现在查排名"                              │
│                                                                 │
│  ⑥ MCP 客户端 ──tools/call──→ MCP 服务端                        │
│     {name: "get_top_students", arguments: {n: 3}}               │
│                              │                                  │
│                              ❸ 服务端执行:                       │
│                                 sorted(..., key=score, rev)     │
│                                 取前3 → _ok(top3)                │
│                                                                 │
│  ⑦ MCP 客户端 ←──返回结果── MCP 服务端                           │
│     {"status":"success","data":[{孙七96},{张三92},{王五85}]}    │
│                                                                 │
│  ⑧ LLM 总结: "张三92分排第2，前三名是孙七96分、张三92分、王五85分"│
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
🤖 AI: "张三的成绩是92分，评级A。目前排名前三的学生是：
        孙七(96分)、张三(92分)、王五(85分)"
```

---

## 五、三个文件的关系

```
math_server.py          client.py                  agent.py
(服务端)                (客户端)                (无工具对话)

  @mcp.tool()    ←──    load_mcp_tools()        纯 LLM + Prompt
  暴露工具              创建 AgentExecutor       无 MCP
  封装数据库             绑定工具到大模型
  标准格式返回           verbose=True

运行方式:
  python math_server.py              ← 单独启动（调试用）
  python client.py                      ← 实际使用（客户端内部启动服务端）
```

---

## 六、关键要点总结

| 概念 | 一句话解释 |
|---|---|
| **MCP 协议** | 定义 AI 如何发现和调用工具的通信标准 |
| **FastMCP** | 自动把 Python 函数变成 MCP 工具（协议层全透明） |
| **@mcp.tool()** | 声明函数 = 对外暴露的工具；参数类型 = 调用契约 |
| **数据源封装** | 大模型不碰数据库，工具函数是唯一的"安全边界" |
| **标准化返回** | 统一 `{status, message, data}` 格式，客户端无需特判 |
| **stdio 传输** | 通过子进程 stdin/stdout 通信，零网络配置 |
| **load_mcp_tools** | 自动发现服务端工具 → 转为 LangChain 可用的 Tool 对象 |
| **agent_scratchpad** | ReAct 循环的"中转站"，存放工具调用的中间结果 |
