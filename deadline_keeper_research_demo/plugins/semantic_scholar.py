"""插件: Semantic Scholar API 论文检索

Semantic Scholar 是 Allen AI 的免费学术搜索引擎，收录 2 亿+ 论文。
相比 CORE API 更强的地方:
  - 完全免费，无需 API Key（基础版 100 次/5min）
  - 覆盖面更广（2 亿 vs 1.36 亿）
  - 自带引用次数、高影响力引用、TLDR 摘要
  - 支持按年份、领域、期刊/会议过滤

API 文档: https://api.semanticscholar.org/api-docs/
"""
import urllib3
import json

from pydantic import BaseModel, Field
from langchain_core.tools import tool

# ═══════════════════════════════════════════════════════
# Pydantic args schema
# ═══════════════════════════════════════════════════════

class SemanticScholarInput(BaseModel):
    """semantic-scholar 工具的参数"""
    query: str = Field(
        description="搜索关键词，支持英文。例如: 'attention mechanism', "
                    "'CRISPR gene editing'"
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="返回论文数量 (1-20)",
    )
    year: str = Field(
        default="",
        description="可选。限定年份范围，例如 '2023-2024' 或 '2023-' 或 '-2022'",
    )
    fields_of_study: str = Field(
        default="",
        description="可选。限定领域，如 'Computer Science', 'Medicine', "
                    "'Biology', 'Physics', 'Chemistry', 'Engineering' 等",
    )


# ═══════════════════════════════════════════════════════
# API 封装
# ═══════════════════════════════════════════════════════

_S2_BASE = "https://api.semanticscholar.org/graph/v1"
# 请求字段: 标题、作者、摘要、年份、引用数、期刊、DOI、URL
_FIELDS = "title,authors,abstract,year,citationCount,publicationVenue,externalIds,openAccessPdf,fieldsOfStudy,tldr"


def _search_s2(query: str, max_results: int, year: str = "",
               fields_of_study: str = "") -> str:
    """调用 Semantic Scholar API 搜索论文并格式化为 LLM 可读文本"""
    http = urllib3.PoolManager()

    # 构造查询 URL
    params = [f"query={urllib3.request.urlencode({'q': query})[2:]}",
              f"limit={max_results}",
              f"fields={_FIELDS}"]

    if year:
        params.append(f"year={year}")
    if fields_of_study:
        # URL-encode the comma-separated fields
        params.append(f"fieldsOfStudy={urllib3.request.urlencode({'q': fields_of_study})[2:]}")

    url = f"{_S2_BASE}/paper/search?{'&'.join(params)}"

    response = http.request("GET", url)
    if response.status != 200:
        return f"Semantic Scholar API returned HTTP {response.status}: {response.data.decode('utf-8')[:200]}"

    data = json.loads(response.data.decode("utf-8"))
    papers = data.get("data", [])
    total = data.get("total", 0)

    if not papers:
        return f"Semantic Scholar 共找到 {total} 篇论文，但本次未返回结果。请尝试调整搜索词或放宽限制。"

    docs = []
    for i, p in enumerate(papers):
        # ── 标题 ──
        title = p.get("title", "Unknown")

        # ── 作者 (取前 5 位) ──
        authors = p.get("authors", [])
        author_names = [a.get("name", "") for a in authors[:5]]
        authors_str = ", ".join(author_names)
        if len(authors) > 5:
            authors_str += f" et al. ({len(authors)} total)"

        # ── 年份 ──
        year_str = str(p.get("year", "")) if p.get("year") else "N/A"

        # ── 引用数 ──
        citations = p.get("citationCount", 0)

        # ── 期刊/会议 ──
        venue_info = ""
        venue = p.get("publicationVenue")
        if venue and venue.get("name"):
            venue_info = f"{venue.get('name', '')}"
            if venue.get("type"):
                venue_info += f" ({venue['type']})"

        # ── 领域 ──
        fos = p.get("fieldsOfStudy", [])
        fos_str = ", ".join(fos) if fos else "N/A"

        # ── 摘要 ──
        abstract = p.get("abstract", "")
        # TLDR 是 Semantic Scholar 的 AI 生成的超短摘要 (≤20 词)
        tldr = p.get("tldr", {})
        tldr_text = tldr.get("text", "") if tldr else ""

        # ── 链接 ──
        paper_id = p.get("paperId", "")
        s2_url = f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else ""
        doi = p.get("externalIds", {}).get("DOI", "")
        doi_url = f"https://doi.org/{doi}" if doi else ""

        # PDF
        pdf_info = ""
        oa = p.get("openAccessPdf")
        if oa and oa.get("url"):
            pdf_info = f"\n* Open Access PDF: {oa['url']}"

        # ── 格式化输出 ──
        parts = [
            f"* 标题: {title}",
            f"* 作者: {authors_str}",
            f"* 年份: {year_str}  |  引用数: {citations}",
        ]
        if venue_info:
            parts.append(f"* 发表: {venue_info}")
        parts.append(f"* 领域: {fos_str}")
        if tldr_text:
            parts.append(f"* TLDR: {tldr_text}")
        if abstract:
            parts.append(f"* 摘要: {abstract[:400]}{'...' if len(abstract) > 400 else ''}")
        links = []
        if s2_url:
            links.append(f"Semantic Scholar: {s2_url}")
        if doi_url:
            links.append(f"DOI: {doi_url}")
        parts.append(f"* 链接: {' | '.join(links)}")
        if pdf_info:
            parts.append(pdf_info)

        docs.append("\n".join(parts))

    summary = (f"Semantic Scholar 共找到 {total} 篇论文，"
               f"以下是最相关的 {len(papers)} 篇:\n\n")
    return summary + "\n\n-----\n\n".join(docs)


# ═══════════════════════════════════════════════════════
# 工具定义
# ═══════════════════════════════════════════════════════

@tool("semantic-scholar", args_schema=SemanticScholarInput)
def semantic_scholar(query: str, max_results: int = 5, year: str = "",
                     fields_of_study: str = "") -> str:
    """Search for academic papers on Semantic Scholar (200M+ papers, free, no API key).

    Semantic Scholar is one of the largest free academic search engines.
    It covers all major disciplines and provides citation counts, TLDR summaries,
    and open-access PDF links.

    Compared to CORE API and arXiv:
    - CORE API: smaller coverage, requires API key, but better for PDF access
    - arXiv: only preprints, no peer-reviewed journal papers
    - Semantic Scholar: largest coverage among free APIs, includes both preprints
      and published papers, provides citation metrics

    Use this tool when:
    - CORE API returns too few results
    - You need recent papers with high citation counts
    - You want papers from a specific field or year range

    Parameters:
    - query: search keywords (English)
    - max_results: number of papers to return (1-20)
    - year: optional year filter (e.g., "2023-2024", "2023-", "-2022")
    - fields_of_study: optional field filter (e.g., "Computer Science", "Medicine")

    Example:
    {"query": "transformer attention mechanism", "max_results": 10, "year": "2023-2024"}
    {"query": "CRISPR clinical trial", "max_results": 5, "fields_of_study": "Medicine"}

    Returns:
        Formatted paper list with title, authors, year, citations, venue, TLDR,
        abstract, and clickable links.
    """
    try:
        return _search_s2(query, max_results, year, fields_of_study)
    except Exception as e:
        return f"Error searching Semantic Scholar: {e}"


def get_tools():
    """插件入口"""
    return [semantic_scholar]
