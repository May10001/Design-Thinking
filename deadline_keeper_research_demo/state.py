"""
    Agent 状态定义 & Pydantic 
    结构化输出模型
"""
from typing import Annotated, Optional, Sequence, TypedDict
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

#Field是一个pydantic库的自定义描述器，用于在定义模型字段时添加元数据。规范LLM输出的结构。
#元数据用于描述字段的属性，例如默认值、验证规则、文档字符串等。

class AgentState(TypedDict):
    """LangGraph 全局状态 — 在所有节点间流转"""
    requires_research: bool # 是否需要检索论文
    num_feedback_requests: int # 已请求的反馈次数
    is_good_answer: bool # 是否答案质量符合要求
    messages: Annotated[Sequence[BaseMessage], add_messages]  # 所有消息记录 


class DecisionMakingOutput(BaseModel): # LLM输出
    """决策节点：是否需要搜索论文"""
    requires_research: bool = Field( # 是否需要检索论文
        description="Whether the user query requires paper research"
    )
    answer: Optional[str] = Field( # 直接回答（如果不需要检索论文）
        default=None,
        description="Direct answer if no research needed, None otherwise",
    )


class JudgeOutput(BaseModel): # 评判节点输出
    """评判节点：答案质量评估"""
    is_good_answer: bool = Field( # 是否答案质量符合要求
        description="Whether the final answer is satisfactory"
    )
    feedback: Optional[str] = Field( # 改进建议（如果答案质量不符合要求）   
        default=None,
        description="Improvement suggestions if answer is not good",
    )


class SearchPapersInput(BaseModel):
    """论文检索参数，调用论文搜索 API" 节点的强制输入格式"""
    query: str = Field(description="CORE API 搜索查询语句") # 搜索查询语句关键词
    max_papers: int = Field(  # 返回论文数量上限（1-10）
        default=1,
        ge=1,
        le=10,
        description="返回论文数量上限 (1-10)",
    )
