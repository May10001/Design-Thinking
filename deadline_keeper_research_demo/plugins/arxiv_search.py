"""插件: arXiv API 论文检索

arXiv 是全球最大的预印本平台 (2M+ 论文)，涵盖物理、数学、CS 等领域。
API 免费、无需注册、国内可直连。

与 search_papers 互补:
  - CORE API: 已发表的正式论文（同行评审后）
  - arXiv API: 最新预印本（同行评审前，更快获取前沿成果）
"""
import urllib3
import xml.etree.ElementTree as ET

from pydantic import BaseModel, Field
from langchain_core.tools import tool

# ── arXiv 命名空间 ───────────────────────────────────
_ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


# ── Pydantic args schema ─────────────────────────────

class ArxivSearchInput(BaseModel):
    """arxiv-search 工具的参数 Schema"""
    query: str = Field(description="arXiv 搜索关键词，支持 AND/OR 逻辑")
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="返回论文数量上限 (1-20)",
    )


# ── API 封装 ─────────────────────────────────────────

def _parse_arxiv_response(xml_text: str, max_results: int) -> str:
    """解析 arXiv API 返回的 Atom XML，格式化为 LLM 可读文本"""
    root = ET.fromstring(xml_text)
    entries = root.findall("atom:entry", _ARXIV_NS)

    if not entries:
        return "No arXiv papers found for this query"

    docs = []
    for i, entry in enumerate(entries[:max_results]):
        title = " ".join(
            entry.find("atom:title", _ARXIV_NS).text.split()
        )
        arxiv_id = entry.find("atom:id", _ARXIV_NS).text.split("/abs/")[-1]
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        abstract = " ".join(
            entry.find("atom:summary", _ARXIV_NS).text.split()
        )
        published = entry.find("atom:published", _ARXIV_NS).text[:10]

        authors = [
            author.find("atom:name", _ARXIV_NS).text
            for author in entry.findall("atom:author", _ARXIV_NS)
        ]
        authors_str = ", ".join(authors[:5])
        if len(authors) > 5:
            authors_str += f" et al. ({len(authors)} authors)"

        docs.append(
            f"* arXiv ID: {arxiv_id}\n"
            f"* Title: {title}\n"
            f"* Authors: {authors_str}\n"
            f"* Published: {published}\n"
            f"* Abstract: {abstract[:500]}...\n"
            f"* PDF: {pdf_url}"
        )

    return f"Found {len(entries)} arXiv papers (showing top {max_results}):\n\n" + \
           "\n-----\n".join(docs)


# ── 工具定义 ─────────────────────────────────────────

@tool("arxiv-search", args_schema=ArxivSearchInput)
def arxiv_search(query: str, max_results: int = 5) -> str:
    """Search for papers on arXiv.org, the largest preprint server.

    Use this for finding cutting-edge research that may not yet be published
    in journals. Best for: physics, mathematics, computer science, quantitative
    biology, and statistics preprints.

    The arXiv API is free and requires no API key.

    Example:
    {"query": "quantum machine learning", "max_results": 5}

    Returns:
        Formatted list of arXiv papers with ID, title, authors, abstract, and PDF URL.
    """
    try:
        http = urllib3.PoolManager()
        encoded_query = urllib3.request.urlencode({"q": query})[2:]
        url = (
            f"http://export.arxiv.org/api/query"
            f"?search_query=all:{encoded_query}"
            f"&start=0&max_results={max_results}"
            f"&sortBy=relevance&sortOrder=descending"
        )
        response = http.request("GET", url)
        if response.status != 200:
            return f"arXiv API returned HTTP {response.status}"

        return _parse_arxiv_response(response.data.decode("utf-8"), max_results)
    except Exception as e:
        return f"Error searching arXiv: {e}"


def get_tools():
    """插件入口: 返回此模块提供的工具列表"""
    return [arxiv_search]
