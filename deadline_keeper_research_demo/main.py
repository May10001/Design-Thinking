"""入口 — 交互式deadline_keeper 论文检索示例

运行方式（在项目根目录）:
    python -m deadline_keeper_research_demo.main       # 模块方式
    cd deadline_keeper_research_demo && python main.py  # 直接方式
"""
import os
import asyncio
import sys

# ── 确保能找到项目根目录的 .env ──────────────────────
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_current_dir)
if not __package__:
    sys.path.insert(0, _project_root)
    __package__ = "deadline_keeper_research_demo"

_env_path = os.path.join(_project_root, ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path)

from langgraph.graph.state import CompiledStateGraph
from .graph import app


def check_env() -> bool:
    missing = []
    if not os.getenv("DEEPSEEK_API_KEY"):
        missing.append("DEEPSEEK_API_KEY")
    if not os.getenv("CORE_API_KEY"):
        missing.append("CORE_API_KEY（可选，用于论文检索；不影响 PDF 下载）")

    if missing:
        print("⚠️  缺少以下环境变量（请在项目根目录 .env 中设置）:")
        for m in missing:
            print(f"   • {m}")
        if "DEEPSEEK_API_KEY" in str(missing):
            return False
    return True


async def print_stream(app: CompiledStateGraph, user_input: str):
    """流式执行 StateGraph，在每个节点间显示 thinking 进度"""
    print("\n" + "=" * 60)
    print(f"📝 问题: {user_input}")
    print("=" * 60 + "\n")

    all_messages = []
    node_emojis = {
        "decision_making": "🔍 分析问题",
        "planning": "📋 制定计划",
        "agent": "🤔 执行检索",
        "tools": "🔧 调用工具",
        "judge": "✅ 自检答案",
    }

    def _show_thinking():
        print("⏳ thinking...", end="", flush=True)

    def _clear_thinking():
        print("\r" + " " * 30 + "\r", end="")

    _show_thinking()

    async for chunk in app.astream(
        {"messages": [user_input]}, stream_mode="updates"
    ):
        _clear_thinking()

        for node_name, updates in chunk.items():
            label = node_emojis.get(node_name, node_name)
            print(f"── {label} ──")

            if messages := updates.get("messages"):
                all_messages.extend(messages)
                for msg in messages:
                    content = str(msg.content)
                    # tool 返回的原始数据可能很长，适当截断
                    if len(content) > 600:
                        content = content[:600] + "\n...(截断，完整内容见上方文件路径)"
                    print(f"  [{msg.type}] {content}")
            elif updates.get("requires_research") is not None:
                action = "需要检索论文" if updates["requires_research"] else "直接回答"
                print(f"  [决策] {action}")
            elif updates.get("is_good_answer") is not None:
                verdict = "通过" if updates["is_good_answer"] else "需改进"
                print(f"  [评判] {verdict}")

        print()
        _show_thinking()

    _clear_thinking()

    if all_messages:
        # 找最后一个 AI 消息作为最终回复
        final = None
        for msg in reversed(all_messages):
            if msg.type == "ai" and hasattr(msg, "content") and msg.content:
                # 跳过 tool_calls 消息
                if not (
                    hasattr(msg, "tool_calls")
                    and msg.tool_calls
                    and not msg.content
                ):
                    final = msg
                    break
        if final:
            print("=" * 60)
            print(f"📄 最终回复:\n{final.content}")
            print("=" * 60)
            return final

    return all_messages[-1] if all_messages else None


async def main():
    print("=" * 60)
    print("  📚 deadline_keeper 论文检索示例 (DeepSeek + LangGraph)")
    print("  ────────────────────────────────────")
    print("  需要: DEEPSEEK_API_KEY (必须)")
    print("        CORE_API_KEY   (可选，用于论文检索)")
    print("                      注册: https://core.ac.uk/services/api")
    print("=" * 60)

    if not check_env():
        return

    print("\n✅ 代理就绪！试试输入:")
    print('   • "Download and summarize: https://arxiv.org/pdf/1706.03762.pdf"')
    print('   • "Find 5 papers about quantum machine learning"')
    print('   • "Find recent CRISPR papers from 2023-2024"\n')

    while True:
        try:
            user_input = input("🧑 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if user_input.lower() == "quit":
            print("👋 再见！")
            break
        if not user_input:
            continue

        await print_stream(app, user_input)


if __name__ == "__main__":
    asyncio.run(main())
