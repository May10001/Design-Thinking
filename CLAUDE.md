# deadline_keeper 仓库说明

`deadline_keeper` 是课程项目的通知提取与任务匹配模块。CLI 以 `python -m deadline_keeper` 运行，安装和模型配置见 [README](README.md)。

## 目录

- `deadline_keeper/` 暴露项目 CLI。
- `backend/app/adapters/agent_adapter.py` 调用模型并校验提取和匹配结果。
- `backend/app/schemas/agent.py` 定义后端共用数据模型。
- `backend/app/agent/prompts/` 定义提取和匹配提示词。
- `backend/tests/` 包含固定响应测试和真实公告验收脚本。

从仓库根目录运行 `python -m unittest discover -s backend/tests -v`。需要真实模型时，使用 `python -m backend.tests.live_notices --env-file backend/.env`。

教学示例分别位于 `deadline_keeper_chat_demo/`、`deadline_keeper_mcp_demo/` 和 `deadline_keeper_research_demo/`，使用根目录 `requirements.txt` 和 `.env`。论文检索示例可通过 `python -m deadline_keeper_research_demo.main` 或 `python -m deadline_keeper_research_demo.server` 启动。
