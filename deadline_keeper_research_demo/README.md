# deadline_keeper 论文检索示例

基于 LangGraph + DeepSeek 的智能论文研究代理。帮助研究者检索、下载和分析学术论文。

## 与原始项目的区别

| | 原始项目 (ipynb) | 本项目 |
|---|---|---|
| **大模型** | OpenAI GPT-4o | DeepSeek (国内可用，价格低) |
| **部署形式** | Jupyter Notebook | 纯 Python 模块 |
| **代码组织** | 单文件 Cell | 按职责分 5 个模块 |
| **API 接入** | 硬编码 key | `.env` 统一管理 |
| **论文下载** | 仅提取文本 | 文本 + 自动保存 PDF 到本地 |

## 架构概览

```
用户输入
    │
    ▼
┌─────────────────────────────────────────────────────┐
│                  LangGraph 工作流                     │
│                                                      │
│  ① decision_making ──→ 直接回答? ──→ END             │
│         │ 需要研究                                    │
│         ▼                                            │
│  ② planning ──→ 制定检索计划                          │
│         │                                            │
│         ▼                                            │
│  ③ agent (绑定工具) ←──────────┐                     │
│         │                      │                     │
│    ┌────┴────┐                 │                     │
│    │ 有tool  │ 无tool          │                     │
│    │ _calls  │                 │                     │
│    ▼         ▼                 │                     │
│  ④ tools    ⑤ judge           │                     │
│    │         │                 │                     │
│    └──→ agent    ┌─ 不通过 ───→ planning (重试≤2次)   │
│                  └─ 通过 ──→ END                      │
└─────────────────────────────────────────────────────┘
```

**五个节点的职责：**

| 节点 | 作用 | 关键 LLM 配置 |
|---|---|---|
| `decision_making` | 判断问题是闲聊还是需要研究 | `with_structured_output(DecisionMakingOutput)` |
| `planning` | 制定分步检索计划 | 基础 LLM，无工具 |
| `agent` | 按计划调用工具、综合答案 | `bind_tools(tools)` — 绑定论文检索和 PDF 解析 |
| `tools` | 执行 API 调用、下载 PDF 并**保存到本地** | 无 LLM，纯函数执行 |
| `judge` | 自检答案质量，不通过则重试 | `with_structured_output(JudgeOutput)` |

### 工作流运行时的进度显示

在节点间等待 LLM 响应时（最长可达数十秒），终端会显示 `⏳ thinking...` 避免长时间空白：

```
📝 问题: Find papers about quantum computing
============================================================

── 🔍 分析问题 ──
  [决策] 需要检索论文

⏳ thinking...   ← LLM 在制定计划，等待中...

── 📋 制定计划 ──
  [ai] 1. Search CORE API for "quantum computing"
  ...

⏳ thinking...   ← LLM 在执行检索，等待中...

── 🔧 调用工具 ──
  [tool] * Title: Quantum Machine Learning...
  [本地保存] PDF 已保存至: .../deadline_keeper_research_demo/downloads/paper_abc123.pdf
```

## 安装和使用

### 1. 申请 API Key

| Key | 用途 | 获取地址 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 大模型调用（必须） | https://platform.deepseek.com → API Keys |
| `CORE_API_KEY` | 论文检索（可选） | https://core.ac.uk/services/api → 免费注册 |

> 即使没有 CORE_API_KEY，仍然可以使用 PDF 下载和分析功能（直接粘贴论文 URL 即可）。

### 2. 配置环境

```bash
# 在项目根目录的 .env 中添加
DEEPSEEK_API_KEY=sk-your-deepseek-key
CORE_API_KEY=your-core-api-key    # 可选
```

### 3. 安装运行

```bash
# 在仓库根目录运行
pip install -r requirements.txt
python -m deadline_keeper_research_demo.main
```

### 4. 示例问题

```
🧑 你: Download and summarize this paper: https://arxiv.org/pdf/1706.03762.pdf

🧑 你: Find 5 papers about CRISPR gene editing from 2023-2024

🧑 你: Find papers about transformer architecture in protein folding,
       then download and compare their approaches

🧑 你: Download and save this paper to my local folder:
       https://example.com/paper.pdf
```

> PDF 文件自动保存到 `deadline_keeper_research_demo/downloads/` 目录下，文件名格式为 `paper_{url_hash}.pdf`。每次调用 `download-paper` 工具都会同时完成"文本提取"和"本地保存"。

## 文件职责

```
deadline_keeper_research_demo/
├── __init__.py    # 包声明
├── state.py       # AgentState (全局状态) + Pydantic 结构化输出模型
├── prompts.py     # 5 个节点的 Prompt 模板（驱动整个工作流的"灵魂"）
├── tools.py       # CORE API 封装 + PDF 下载解析 + 本地保存 + 人工反馈
├── graph.py       # LangGraph 节点函数 + 条件路由 + 编译图
├── main.py        # 入口：交互循环 + 流式输出 + thinking 进度
└── downloads/     # 下载的 PDF 文件存放处（自动创建）
```

## 关键技术点

### 1. StateGraph — 全局状态在节点间流转

```python
class AgentState(TypedDict):
    requires_research: bool          # 是否需要检索
    num_feedback_requests: int       # 自检重试次数
    is_good_answer: bool             # 答案是否通过
    messages: Annotated[Sequence[BaseMessage], add_messages]  # 对话历史
```

`add_messages` 是 LangGraph 的 reducer：新消息会**追加**而不会覆盖旧消息。每个节点只需 `return {"messages": [new_msg]}`，历史自动累积。

### 2. 结构化输出 — LLM 返回 JSON 而非自然语言

```python
decision_making_llm = base_llm.with_structured_output(
    DecisionMakingOutput, method="function_calling"
)
```

`with_structured_output` 让 LLM 的输出变成 `DecisionMakingOutput` 对象（Pydantic 自动校验），而不是自由文本。这对于条件路由至关重要——你需要一个确定的 `True/False` 来决定走哪个分支。

### 3. Agent + Tools 循环

```
agent_node ─→ 返回 tool_calls ─→ tools_node 执行
                                      │
                                      ▼
                                  agent_node 分析结果
                                      │
                          ┌── 还需要工具? ──┐
                          │ 是              │ 否
                          ▼                 ▼
                      tools_node          judge 自检
```

`should_continue` 函数检查 `last_message.tool_calls` 是否存在：有就继续调工具，没有就进入自检。

### 4. 自检循环 — 最多 2 次重试

评判节点检查答案质量，不通过时生成反馈，回到 planning 重新制定计划。`num_feedback_requests` 计数器防止无限循环。

## 局限性

- **CORE API 速率限制**：免费账号有调用频率上限
- **大 PDF 处理**：超大论文可能超出 DeepSeek 上下文窗口
- **非英文论文**：CORE API 主要收录英文论文
- **结构化输出**：DeepSeek 的 JSON 模式偶尔不如 GPT-4o 稳定，如遇问题可降级为手动 JSON 解析
