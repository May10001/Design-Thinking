# Design-Thinking

# deadline_keeper

`deadline_keeper` 从脱敏通知中提取任务和截止日期，并判断新任务与候选旧任务的关系。日期或任务身份含糊时，输出供人工检查的信息。

## 启动

需要 Python 3.11 或以上。在仓库根目录安装依赖，复制配置示例并在 `backend/.env` 中填入 `LLM_API_KEY`。

```powershell
python -m pip install -r backend/requirements-agent.txt
Copy-Item backend/.env.agent.example backend/.env
python -X utf8 -m deadline_keeper extract backend/tests/fixtures/extraction.json --env-file backend/.env
python -X utf8 -m deadline_keeper match backend/tests/fixtures/matching.json --env-file backend/.env
```

模型配置、FastAPI 接入、错误处理和验收结果见[交付说明](backend/README.md)。

## 项目目录

- `deadline_keeper/` 提供项目的命令行入口。
- `backend/app/` 提供 `extract_tasks()`、`match_tasks()`、校验模型和提示词。
- `backend/tests/` 包含接口测试、公开通知验收和示例输入。
- `deadline_keeper_chat_demo/`、`deadline_keeper_mcp_demo/` 和 `deadline_keeper_research_demo/` 是独立教学示例，使用根目录 `requirements.txt` 的依赖。[论文检索示例说明](deadline_keeper_research_demo/README.md)
- `reference/` 存放教学参考 Notebook。


## 交付与项目文档

- [后端接入交付说明](docs/deadline_keeper_后端交付说明.md)
- [Agent 交付说明](docs/deadline_keeper_交付说明.md)
- [V2 后端方案](docs/Deadline-Keeper-V2-后端方案.md)
- [V2 Agent 需求说明](docs/Deadline-Keeper-V2-Agent需求说明.md)
- [项目开题方案](docs/华南理工大学_设计思维课程_智能任务管理助手开题方案.docx)
