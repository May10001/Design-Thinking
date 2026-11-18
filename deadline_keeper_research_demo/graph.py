"""LangGraph workflow定义 — 5 节点 + 条件路由 + 自检循环"""
import json
import os

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, ToolMessage, AIMessage
from langgraph.graph import END, StateGraph

from .state import (
    AgentState,
    DecisionMakingOutput,
    JudgeOutput,
)
from .tools import (
    search_papers,
    download_paper,
    ask_human_feedback,
    format_tools_description,
)
from .prompts import (
    DECISION_MAKING_PROMPT,
    PLANNING_PROMPT,
    AGENT_PROMPT,
    JUDGE_PROMPT,
)

# 工具注册 
tools = [search_papers, download_paper, ask_human_feedback]
tools_dict = {t.name: t for t in tools}

# LLMs 
# 使用 DeepSeek (OpenAI 兼容协议) 替代 GPT-4o
# DeepSeek 支持 function calling，OpenAI 兼容协议调用 DeepSeek 模型—— 这是现在 AI 行业最通用的 "通用语言"
base_llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    temperature=0.0,#
)

# 结构化输出 LLM — 输出 JSON 由 Pydantic 自动校验
# method="function_calling": 用工具调用的 JSON 能力实现结构化输出
# DeepSeek 完全支持此模式
#它注册了 Agent 能调用的所有工具，并把同一个 DeepSeek 基础模型，配置成了 3 个不同功能的专用 LLM，分别负责「决策判断」「工具调用」「答案评判」。

#决策专用LLM
decision_making_llm = base_llm.with_structured_output(
    DecisionMakingOutput, method="function_calling"
) #保证输出严格符合DecisionMakingOutput结构
#工具专用LLM
agent_llm = base_llm.bind_tools(tools)  #绑定工具,

#评判专用LLM
judge_llm = base_llm.with_structured_output(
    JudgeOutput, method="function_calling"
)#输出要严格符合JudgeOutput结构


# 节点 1: 决策节点 — 入口，判断是否需要搜索论文
def decision_making_node(state: AgentState) -> dict:
    """判断用户问题是直接回答，还是需要搜索论文。
    简单对话 → 直接回复 (END)
    需要证据 → 路由到 planning 节点"""
    system = SystemMessage(content=DECISION_MAKING_PROMPT)  # 决策节点提示，DECISION_MAKING_PROMPT是prompts.py中的决策节点提示模板
    response: DecisionMakingOutput = decision_making_llm.invoke( #是一个绑定了DecisionMakingOutput格式的 LLM，它会强制 LLM 只能输出符合DecisionMakingOutput结构的 JSON
        [system] + state["messages"]
    )
    output: dict = {"requires_research": response.requires_research} #是否需要检索论文，
    if response.answer:
        output["messages"] = [AIMessage(content=response.answer)] #如果LLM有直接回答，就添加到消息历史中
    return output


def router(state: AgentState) -> str:
    """条件路由：根据决策结果选择下一节点"""
    return "planning" if state["requires_research"] else "end"


# 节点 2: 规划节点 — 制定分步检索计划
def planning_node(state: AgentState) -> dict:
    """根据用户问题和可用工具，生成分步检索计划。

    如果历史中有评判反馈，会将反馈融入新计划中。"""
    tools_desc = format_tools_description(tools)
    system = SystemMessage(
        content=PLANNING_PROMPT.format(tools=tools_desc)
    )
    response = base_llm.invoke([system] + state["messages"])
    return {"messages": [response]}


# 节点 3: 工具执行节点 — 实际调用 CORE API / PDF 解析
def tools_node(state: AgentState) -> dict:
    """执行 LLM 请求的 tool_calls，将结果以 ToolMessage 写入消息历史。

    这是 MCP 之外的另一种工具集成方式：直接在 LangGraph 节点中
    调用 langchain Tool 对象。"""
    last_message = state["messages"][-1]
    outputs = []
    for tool_call in last_message.tool_calls:
        tool_result = tools_dict[tool_call["name"]].invoke(tool_call["args"])
        outputs.append(
            ToolMessage(
                content=json.dumps(tool_result),
                name=tool_call["name"],
                tool_call_id=tool_call["id"],
            )
        )
    return {"messages": outputs}


# 节点 4: 代理执行节点 — LLM 根据计划 + 工具结果回答问题
def agent_node(state: AgentState) -> dict:
    """使用绑定了工具的 LLM 执行检索计划中的当前步骤。

    如果 LLM 返回 tool_calls → 回到 tools 节点
    如果 LLM 返回纯文本 → 路由到 judge 节点进行自检"""
    system = SystemMessage(content=AGENT_PROMPT)
    response = agent_llm.invoke([system] + state["messages"])
    return {"messages": [response]}


def should_continue(state: AgentState) -> str:
    """条件路由：LLM 是否还需要调用更多工具"""
    last_message = state["messages"][-1]
    return "continue" if last_message.tool_calls else "end"


# 节点 5: 评判节点 — LLM 自检答案质量
def judge_node(state: AgentState) -> dict:
    """自检答案质量。最多重试 2 次（控制延迟）。

    不通过 → 返回带反馈的 plan 节点重新检索
    通过   → END"""
    num_feedback = state.get("num_feedback_requests", 0)
    if num_feedback >= 2:
        return {"is_good_answer": True}

    system = SystemMessage(content=JUDGE_PROMPT) # 评判节点提示
    response: JudgeOutput = judge_llm.invoke(
        [system] + state["messages"]
    )
    output: dict = {
        "is_good_answer": response.is_good_answer,
        "num_feedback_requests": num_feedback + 1,
    }
    if response.feedback:
        output["messages"] = [AIMessage(content=response.feedback)]
    return output


def final_answer_router(state: AgentState) -> str:
    """条件路由：答案是否通过自检"""
    return "end" if state["is_good_answer"] else "planning"


# 构建 & 编译 StateGraph
def build_graph() -> StateGraph:
    """构建完整的 LangGraph 论文研究代理工作流"""
    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("decision_making", decision_making_node)
    workflow.add_node("planning", planning_node)
    workflow.add_node("tools", tools_node)
    workflow.add_node("agent", agent_node)
    workflow.add_node("judge", judge_node)

    # 入口
    workflow.set_entry_point("decision_making")

    # 连线 & 条件路由
    workflow.add_conditional_edges(
        "decision_making",
        router,
        {"planning": "planning", "end": END},
    )
    workflow.add_edge("planning", "agent")
    workflow.add_edge("tools", "agent")
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"continue": "tools", "end": "judge"},
    )
    workflow.add_conditional_edges(
        "judge",
        final_answer_router,
        {"planning": "planning", "end": END},
    )
    return workflow.compile()


# 全局编译实例
app = build_graph()
