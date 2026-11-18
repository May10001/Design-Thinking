"""插件: CORE API 论文检索
CORE 是全球最大的开放论文库 (136M+)，个人免费使用。
注册: https://core.ac.uk/services/api
"""
import json
import os
import time
import urllib3

from pydantic import BaseModel, Field
from langchain_core.tools import tool


# ── Pydantic args schema（定义工具参数）────────────────

class SearchPapersInput(BaseModel):
    """search-papers 工具的参数 Schema"""
    query: str = Field(description="CORE API 搜索查询语句，支持 AND/OR/字段过滤/范围查询")
    max_papers: int = Field(
        default=1,
        ge=1,
        le=10,
        description="返回论文数量上限 (1-10)",
    )


# ── API 封装 ─────────────────────────────────────────

class CoreAPIWrapper(BaseModel):
    """封装 CORE API v3 的论文检索，含指数退避重试。"""
    base_url: str = "https://api.core.ac.uk/v3"
    api_key: str = os.environ.get("CORE_API_KEY", "")
    top_k_results: int = Field(default=1)

    def _get_search_response(self, query: str) -> dict:
        http = urllib3.PoolManager()
        max_retries = 5

        for attempt in range(max_retries):
            response = http.request(
                "GET",
                f"{self.base_url}/search/outputs",
                headers={"Authorization": f"Bearer {self.api_key}"},
                fields={"q": query, "limit": self.top_k_results},
            )
            if 200 <= response.status < 300:
                return json.loads(response.data.decode("utf-8"))
            elif attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 2))  # 指数退避: 4s, 8s, 16s, 32s
            else:
                raise Exception(
                    f"CORE API returned {response.status}: {response.data}"
                )

    def search(self, query: str) -> str:
        response = self._get_search_response(query)
        results = response.get("results", [])
        if not results:
            return "No relevant results were found"

        docs = []
        for r in results:
            published = r.get("publishedDate") or r.get("yearPublished", "")
            authors = " and ".join(
                [a["name"] for a in r.get("authors", [])]
            )
            docs.append(
                f"* ID: {r.get('id', '')}\n"
                f"* Title: {r.get('title', '')}\n"
                f"* Published: {published}\n"
                f"* Authors: {authors}\n"
                f"* Abstract: {r.get('abstract', '')}\n"
                f"* URLs: {r.get('sourceFulltextUrls') or r.get('downloadUrl', '')}"
            )
        return "\n-----\n".join(docs)


# ── 工具定义 ─────────────────────────────────────────

@tool("search-papers", args_schema=SearchPapersInput)
def search_papers(query: str, max_papers: int = 1) -> str:
    """Search for scientific papers using the CORE API.

    Supports complex queries with field filters (yearPublished, authors, title, etc.)
    and boolean operators (AND, OR). See the CORE API query language docs for details.

    Example:
    {"query": "attention mechanism AND yearPublished:2023", "max_papers": 5}

    Returns:
        Formatted list of papers with ID, title, authors, abstract, and URLs.
    """
    try:
        return CoreAPIWrapper(top_k_results=max_papers).search(query)
    except Exception as e:
        return f"Error searching papers: {e}"


def get_tools():
    """插件入口: 返回此模块提供的工具列表"""
    return [search_papers]
