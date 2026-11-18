"""
MCP 客户端 — 通过 LangChain Agent 调用 MCP 工具

完整链路:
  客户端                              MCP 服务端
  ────────                            ──────────
  用户: "张三多少分？"
    │
  Agent 决策: 需要调用 query_student(name="张三")
    │
  langchain-mcp-adapters              ❶ 接收指令  ← 收到 tool call
  序列化为 MCP 协议 ─────────────────→ ❷ 封装逻辑  ← 查数据库、校验、格式化
    │
  langchain-mcp-adapters              ❸ 返回结果  → {status, message, data}
  反序列化为 LangChain ToolMessage ←──
    │
  Agent 读取结果，生成自然语言回复
    │
  用户: "张三的成绩是92分，评级A"
"""
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()


async def main():
    # ── 1. 启动 MCP 服务端子进程 ─────────────────────────
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("math_server.py"))],
    )

    # ── 2. 建立双向通信（stdin/stdout 管道）───────────────
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # ── 3. MCP 握手 ──────────────────────────────────
            # 客户端发送 initialize 请求，服务端返回能力列表
            await session.initialize()

            # ── 4. 发现并加载工具 ────────────────────────────
            # 客户端发送 tools/list 请求
            # 服务端返回: [query_student, add_student, get_top_students]
            # load_mcp_tools 将 MCP Tool 转为 LangChain BaseTool
            tools = await load_mcp_tools(session)
            print(f"✅ 从 MCP 服务端加载了 {len(tools)} 个工具:")
            for t in tools:
                print(f"   • {t.name}: {t.description[:50]}...")

            # ── 5. 创建带工具的大模型 ────────────────────────
            llm = ChatOpenAI(
                model="deepseek-chat",
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com",
                temperature=0,
            )

            # ── 6. 构建 Agent（ReAct / Tool Calling 模式）────
            # agent_scratchpad: 存放 思考→调用→结果 的中间步骤
            prompt = ChatPromptTemplate.from_messages([
                ("system", "你是一个学生管理系统助手。用户询问学生信息时，"
                           "请使用工具查询后，用自然语言告诉用户结果。"),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ])

            agent = create_tool_calling_agent(llm, tools, prompt)
            agent_executor = AgentExecutor(
                agent=agent,
                tools=tools,
                verbose=True,  # 打印完整的 思考→调用→结果 过程
            )

            # ── 7. 交互循环 ──────────────────────────────────
            print("\n" + "=" * 50)
            print("  deadline_keeper MCP 学生管理示例")
            print("  试试: 查张三 / 添加学生 / 排名前3")
            print("=" * 50)

            while True:
                try:
                    user_input = input("\n🧑 你: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\n👋 再见！")
                    break

                if user_input.lower() == "quit":
                    print("👋 再见！")
                    break
                if not user_input:
                    continue

                # ▸ 这里触发完整的 MCP 工具调用流程 ▸
                result = await agent_executor.ainvoke({"input": user_input})
                print(f"🤖 AI: {result['output']}")


if __name__ == "__main__":
    asyncio.run(main())
