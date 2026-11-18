"""
Web 服务 — FastAPI + SSE 流式输出, 为前端提供实时 Agent 处理过程

运行:  python server.py
      然后在浏览器打开 http://127.0.0.1:8000
"""
import json
import os
import sys
import asyncio

# ── 加载 .env（必须在导入 graph 之前）─────────────────
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_current_dir)
if not __package__:
    sys.path.insert(0, _project_root)
    __package__ = "deadline_keeper_research_demo"

_env_path = os.path.join(_project_root, ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path)

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage

from .graph import app as agent_app

# ── FastAPI 实例 ─────────────────────────────────────
server = FastAPI(title="deadline_keeper 论文检索示例", version="1.0")

# 静态文件（前端 HTML/CSS/JS）
server.mount("/static", StaticFiles(directory=os.path.join(_current_dir, "static")), name="static")


# ── JSON 序列化辅助 ──────────────────────────────────

def _msg_to_dict(msg) -> dict:
    """将 LangChain 消息转为 JSON 安全字典。
    ToolMessage 附带 tool_name 方便前端识别论文数据。"""
    d: dict = {"type": msg.type}

    if hasattr(msg, "content") and msg.content:
        content = msg.content
        # AIMessage 的 content 可能是 list[dict] (多模态格式)
        if isinstance(content, list):
            content = " ".join(
                c.get("text", str(c)) if isinstance(c, dict) else str(c)
                for c in content
            )
        d["content"] = str(content)

    if isinstance(msg, ToolMessage):
        d["tool_name"] = getattr(msg, "name", "unknown")

    if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
        d["tool_calls"] = [
            {"name": tc["name"], "args": tc["args"]}
            for tc in msg.tool_calls
        ]

    return d


# ── SSE 流 ───────────────────────────────────────────

def _sse(data: dict) -> str:
    """格式化一条 SSE 事件"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_agent(user_input: str):
    """异步生成器 — 逐条推送 Agent 处理过程中的事件"""
    # 用户消息事件
    yield _sse({"event": "user_input", "text": user_input})

    node_labels = {
        "decision_making": "🔍 分析问题意图",
        "planning": "📋 制定检索计划",
        "agent": "🤔 执行检索分析",
        "tools": "🔧 调用外部工具",
        "judge": "✅ 自检验证结果",
    }

    try:
        all_messages = []
        async for chunk in agent_app.astream(
            {"messages": [user_input]}, stream_mode="updates"
        ):
            for node_name, updates in chunk.items():
                node_label = node_labels.get(node_name, node_name)

                event = {
                    "event": "node_update",
                    "node": node_name,
                    "node_label": node_label,
                }

                if messages := updates.get("messages"):
                    all_messages.extend(messages)
                    event["messages"] = [_msg_to_dict(m) for m in messages]

                if "requires_research" in updates:
                    event["requires_research"] = updates["requires_research"]
                if "is_good_answer" in updates:
                    event["is_good_answer"] = updates["is_good_answer"]

                yield _sse(event)

        # 最终答案
        final = None
        for msg in reversed(all_messages):
            if isinstance(msg, AIMessage) and msg.content and not (
                hasattr(msg, "tool_calls") and msg.tool_calls and not msg.content
            ):
                final = msg
                break

        if final:
            yield _sse({
                "event": "done",
                "final_content": str(final.content),
            })
        else:
            yield _sse({
                "event": "error",
                "message": "Agent 未能生成最终回复",
            })

    except Exception as e:
        yield _sse({
            "event": "error",
            "message": f"处理请求时出错: {str(e)}",
        })


# ── 路由 ─────────────────────────────────────────────

@server.get("/", response_class=HTMLResponse)
async def index():
    """返回前端页面"""
    html_path = os.path.join(_current_dir, "static", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


@server.post("/chat")
async def chat(request: Request):
    """SSE 流式对话端点"""
    body = await request.json()
    user_input = body.get("message", "").strip()
    if not user_input:
        return StreamingResponse(
            iter([_sse({"event": "error", "message": "消息不能为空"})]),
            media_type="text/event-stream",
        )

    return StreamingResponse(
        _stream_agent(user_input),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # 禁用 nginx 缓冲
        },
    )


# ── 入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print("=" * 60)
    print("  📚 deadline_keeper 论文检索示例 Web 版")
    print(f"  浏览器打开: http://{host}:{port}")
    if host == "0.0.0.0":
        print(f"  局域网访问: http://<本机IP>:{port}")
    print("=" * 60)
    # 直接传 app 对象避免模块路径问题
    uvicorn.run(server, host=host, port=port, reload=False)
