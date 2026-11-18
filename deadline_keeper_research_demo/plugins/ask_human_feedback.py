"""插件: 人工反馈 — 允许 Agent 在执行过程中向用户提问"""
from langchain_core.tools import tool


@tool("ask-human-feedback")
def ask_human_feedback(question: str) -> str:
    """Ask the user for feedback when encountering unexpected situations.

    Use this sparingly — only when genuinely stuck or when user input is
    required to proceed (e.g., choosing between multiple search results).

    Example:
    {"question": "I found 50 papers. Should I narrow to a specific year range?"}
    """
    return input(f"\n🤔 {question}\n👉 ")


def get_tools():
    """插件入口: 返回此模块提供的工具列表"""
    return [ask_human_feedback]
