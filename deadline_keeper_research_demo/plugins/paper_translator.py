"""插件: 论文翻译 — 用 DeepSeek 本身做翻译，无需外部翻译 API

场景:
  - 阅读英文论文: 将摘要/结论翻译为中文
  - 构造检索式: 将中文研究问题翻译为英文关键词用于 CORE/arXiv 搜索

设计依据: 用 LLM 做翻译而非调用翻译 API，因为:
  1. 零额外成本（复用已有的 DeepSeek API）
  2. 领域术语翻译更准确（LLM 理解学术上下文）
  3. 无需注册额外服务
"""
import os

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from pydantic import BaseModel, Field

# ── 翻译专用 LLM（temperature 稍高以产生自然的翻译）─
_translator_llm = ChatOpenAI(
    model="deepseek-chat",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    temperature=0.3,  # 翻译需要比逻辑推理稍高的温度
)


# ── Pydantic args schema ─────────────────────────────

class TranslateInput(BaseModel):
    """翻译工具的参数 Schema"""
    text: str = Field(description="要翻译的文本")
    target_language: str = Field(
        default="chinese",
        description="目标语言: 'chinese'（译中）或 'english'（译英）",
    )


# ── 工具定义 ─────────────────────────────────────────

@tool("translate-text", args_schema=TranslateInput)
def translate_text(text: str, target_language: str = "chinese") -> str:
    """Translate academic text between Chinese and English.

    Use this tool when:
    - Reading English papers: translate abstracts/conclusions to Chinese
    - Preparing search queries: translate Chinese research questions to English
      keywords for CORE API or arXiv search

    The translation preserves academic terminology and formal style.

    Example:
    {"text": "This paper proposes a novel attention mechanism...", "target_language": "chinese"}
    {"text": "量子机器学习在药物发现中的应用", "target_language": "english"}

    Returns:
        Translated text with preserved academic style and terminology.
    """
    try:
        if target_language.lower() in ("chinese", "中文", "zh"):
            prompt = (
                "请将以下英文学术文本翻译为中文。要求:\n"
                "- 保留学术术语的准确性\n"
                "- 保持正式的学术语气\n"
                "- 对于没有通用中文译名的术语，保留英文并附括号说明\n\n"
                f"原文:\n{text}"
            )
        else:
            prompt = (
                "Translate the following Chinese academic text to English. Requirements:\n"
                "- Preserve academic terminology accuracy\n"
                "- Maintain formal academic tone\n"
                "- Use standard English academic writing style\n\n"
                f"原文:\n{text}"
            )

        response = _translator_llm.invoke(prompt)
        return response.content
    except Exception as e:
        return f"Error translating text: {e}"


def get_tools():
    """插件入口: 返回此模块提供的工具列表"""
    return [translate_text]
