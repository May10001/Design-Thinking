"""工具集 — CORE API 论文检索、PDF 下载解析（含本地保存）、人工反馈"""
import hashlib
import io
import json
import os
import time
import urllib3
import pdfplumber

from typing import ClassVar
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool, tool

from .state import SearchPapersInput

# ── 下载目录 ─────────────────────────────────────────
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_DOWNLOADS_DIR = os.path.join(_TOOLS_DIR, "downloads")


def _save_to_local(pdf_bytes: bytes, url: str) -> str:
    """将 PDF 字节保存到本地 downloads/ 目录，返回绝对路径"""
    os.makedirs(_DOWNLOADS_DIR, exist_ok=True)
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    filepath = os.path.join(_DOWNLOADS_DIR, f"paper_{url_hash}.pdf")
    with open(filepath, "wb") as f:
        f.write(pdf_bytes)
    return os.path.abspath(filepath)


# ── CORE API 封装 ────────────────────────────────────

class CoreAPIWrapper(BaseModel):
    """封装 CORE API v3 的论文检索。CORE 是全球最大的开放论文库之一
    (136M+ 论文)，个人使用免费，需在 https://core.ac.uk/services/api 申请 Key。

    国内用户通常可以直连，如遇网络问题可使用代理。"""
    base_url: ClassVar[str] = "https://api.core.ac.uk/v3"
    api_key: ClassVar[str] = os.environ.get("CORE_API_KEY", "")

    top_k_results: int = Field(default=1, description="返回论文数量上限")

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
                time.sleep(2 ** (attempt + 2))
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


# ── LangChain Tools ──────────────────────────────────

@tool("search-papers", args_schema=SearchPapersInput)
def search_papers(query: str, max_papers: int = 1) -> str:
    """Search for scientific papers using the CORE API.

    Supports complex queries with field filters (yearPublished, authors, title, etc.)
    and boolean operators (AND, OR).

    Example:
    {"query": "attention mechanism AND yearPublished:2023", "max_papers": 5}

    Returns:
        Formatted list of papers with ID, title, authors, abstract, and URLs.
    """
    try:
        return CoreAPIWrapper(top_k_results=max_papers).search(query)
    except Exception as e:
        return f"Error searching papers: {e}"


@tool("download-paper")
def download_paper(url: str) -> str:
    """Download a scientific paper PDF, save it locally, and extract its text.

    The PDF is automatically saved to the deadline_keeper_research_demo/downloads/ folder.
    Use this when the user asks to download, save, or read a paper.

    Example:
    {"url": "https://example.com/paper.pdf"}

    Returns:
        Extracted text content AND the local file path where the PDF was saved.
    """
    try:
        http = urllib3.PoolManager()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            ),
        }
        max_retries = 3
        for attempt in range(max_retries):
            response = http.request("GET", url, headers=headers)
            if 200 <= response.status < 300:
                pdf_bytes = response.data

                # ── 保存到本地 ──
                local_path = _save_to_local(pdf_bytes, url)

                # ── 提取文本 ──
                pdf_file = io.BytesIO(pdf_bytes)
                with pdfplumber.open(pdf_file) as pdf:
                    text = ""
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"

                return (
                    f"[本地保存] PDF 已保存至: {local_path}\n"
                    f"[页数] {len(pdf.pages)} 页\n"
                    f"[内容]\n{text}"
                )
            elif attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 2))
            else:
                raise Exception(
                    f"Failed to download paper: HTTP {response.status}"
                )
    except Exception as e:
        return f"Error downloading paper: {e}"


@tool("ask-human-feedback")
def ask_human_feedback(question: str) -> str:
    """Ask the user for feedback when encountering unexpected situations."""
    return input(f"\n🤔 {question}\n👉 ")


# ── 辅助函数 ────────────────────────────────────────

def format_tools_description(tools: list[BaseTool]) -> str:
    """将工具列表格式化为 LLM 可读的描述字符串"""
    return "\n\n".join([
        f"- {t.name}: {t.description}\n  Arguments: {t.args}"
        for t in tools
    ])
