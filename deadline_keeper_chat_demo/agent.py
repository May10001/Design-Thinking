import os
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.chat_history import (
    BaseChatMessageHistory,
    InMemoryChatMessageHistory,
)
from langchain_core.messages import HumanMessage, AIMessage

# ── 加载 .env ──────────────────────────────────────────────
load_dotenv()

# ── 历史存储 (按 session_id 隔离) ─────────────────────────
store: dict[str, InMemoryChatMessageHistory] = {}


def get_session_history(session_id: str) -> BaseChatMessageHistory:
    if session_id not in store:
        store[session_id] = InMemoryChatMessageHistory()
    return store[session_id]#用于存储每个会话的历史记录


# ── 构建代理 ───────────────────────────────────────────────
def create_agent() -> RunnableWithMessageHistory:
    """创建基于 DeepSeek 的 LangChain 会话代理"""

    llm = ChatOpenAI(
        model="deepseek-chat",
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        temperature=0.7,
        max_tokens=2048,
    )

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "你是一个友好、活泼的猫娘AI助手。请用中文回答问题，每次回答问题最后要添加一个喵，还有感叹号！"
            "如果问题涉及代码，请给出清晰的示例。",
        ),
        MessagesPlaceholder(variable_name="history"),
        ("human", "{input}"),
    ])

    chain = prompt | llm

    return RunnableWithMessageHistory(
        chain,
        get_session_history,
        input_messages_key="input",
        history_messages_key="history",
    )


# ── 会话循环 ───────────────────────────────────────────────
def main():
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("❌ 请在 .env 文件中设置 DEEPSEEK_API_KEY")
        return

    agent = create_agent()
    session_id = "default"
    config = {"configurable": {"session_id": session_id}}

    print("=" * 50)
    print("  deadline_keeper 会话示例 (DeepSeek + LangChain)")
    print("  命令: quit 退出 | clear 清空历史 | hist 查看历史")
    print("=" * 50)

    while True:
        try:
            user_input = input("\n🧑 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("👋 再见！")
            break

        if user_input.lower() == "clear":
            store[session_id] = InMemoryChatMessageHistory()
            print("✅ 对话历史已清空")
            continue

        if user_input.lower() == "hist":
            history = store.get(session_id)
            if not history or not history.messages:
                print("📭 暂无对话历史")
                continue
            for msg in history.messages:
                role = "🧑 你" if isinstance(msg, HumanMessage) else "🤖 AI"
                print(f"{role}: {msg.content}")
            continue

        # 调用代理
        response = agent.invoke({"input": user_input}, config=config)
        print(f"🤖 AI: {response.content}")


if __name__ == "__main__":
    main()
