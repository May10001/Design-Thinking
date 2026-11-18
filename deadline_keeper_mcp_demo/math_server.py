"""
MCP 服务端 — 学生信息管理系统
演示 MCP 三个核心流程:
  1. 接收客户端发来的 MCP 指令（工具调用请求）
  2. 封装对底层数据源的访问逻辑（增删改查）
  3. 向客户端返回标准化结果（统一 JSON 格式）
"""
from datetime import datetime
from mcp.server.fastmcp import FastMCP

# ── 底层数据源（模拟数据库）──────────────────────────
# 真实场景这里是 MySQL / PostgreSQL / Redis / 外部 API
# MCP 服务端的作用就是把"裸数据"封装成"有意义的工具"
_students_db: list[dict] = [
    {"id": 1, "name": "张三", "score": 92, "grade": "A", "updated_at": "2026-05-20"},
    {"id": 2, "name": "李四", "score": 78, "grade": "B", "updated_at": "2026-05-20"},
    {"id": 3, "name": "王五", "score": 85, "grade": "B+", "updated_at": "2026-05-21"},
    {"id": 4, "name": "赵六", "score": 63, "grade": "C", "updated_at": "2026-05-21"},
    {"id": 5, "name": "孙七", "score": 96, "grade": "A+", "updated_at": "2026-05-22"},
]

# ── 标准化响应格式 ──────────────────────────────────
def _ok(data=None, message="操作成功"):
    """所有工具统一使用此格式返回，保证客户端解析一致"""
    return {"status": "success", "message": message, "data": data}


def _fail(message="操作失败"):
    return {"status": "error", "message": message, "data": None}


# ── 服务端实例 ─────────────────────────────────────
mcp = FastMCP("deadline_keeper_mcp_demo")


# ═══════════════════════════════════════════════════════
# 工具 1: 查询学生
#   - 接收: MCP 客户端发来的 query_student(name="张三") 指令
#   - 封装: 遍历数据库、模糊匹配、格式化结果
#   - 返回: 标准化 JSON {status, message, data}
# ═══════════════════════════════════════════════════════
@mcp.tool()
def query_student(name: str) -> dict:
    """Query student information by name (supports partial match).
    Use this when the user asks about a specific student's score or info."""
    # ▼ 封装了对底层数据的查询逻辑 ▼
    matches = [s for s in _students_db if name in s["name"]]
    if not matches:
        return _fail(f"未找到名字包含「{name}」的学生")
    return _ok(matches, f"找到 {len(matches)} 条记录")


# ═══════════════════════════════════════════════════════
# 工具 2: 添加学生
# ═══════════════════════════════════════════════════════
@mcp.tool()
def add_student(name: str, score: float) -> dict:
    """Add a new student record. Use this when the user wants to create
    a new student entry with a name and score."""
    # ▼ 封装了数据校验 + 写入逻辑 ▼
    if not name.strip():
        return _fail("学生姓名不能为空")
    if not (0 <= score <= 100):
        return _fail("分数必须在 0~100 之间")

    new_id = max(s["id"] for s in _students_db) + 1
    grade = _compute_grade(score)
    new_student = {
        "id": new_id,
        "name": name.strip(),
        "score": score,
        "grade": grade,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _students_db.append(new_student)
    return _ok(new_student, f"学生「{name}」添加成功")


# ═══════════════════════════════════════════════════════
# 工具 3: 排名查询
# ═══════════════════════════════════════════════════════
@mcp.tool()
def get_top_students(n: int = 3) -> dict:
    """Get the top N students ranked by score. Use this when the user asks
    about the best students or ranking."""
    # ▼ 封装了排序 + Top-N 逻辑 ▼
    sorted_students = sorted(_students_db, key=lambda s: s["score"], reverse=True)
    return _ok(sorted_students[:n], f"前 {min(n, len(_students_db))} 名学生")


# ── 内部辅助（不对外暴露）───────────────────────────
def _compute_grade(score: float) -> str:
    if score >= 95:
        return "A+"
    elif score >= 90:
        return "A"
    elif score >= 85:
        return "B+"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C+"
    elif score >= 60:
        return "C"
    else:
        return "D"


if __name__ == "__main__":
    mcp.run()
