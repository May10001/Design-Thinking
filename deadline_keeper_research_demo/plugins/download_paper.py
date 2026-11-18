"""插件: PDF 论文下载 + 本地保存 + 文本提取"""
import hashlib
import io
import os
import time
import urllib3
import pdfplumber

from langchain_core.tools import tool


# ── 下载目录配置 ─────────────────────────────────────
_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOWNLOADS_DIR = os.path.join(_TOOLS_DIR, "downloads")


def _save_to_local(pdf_bytes: bytes, url: str) -> str:
    """将 PDF 字节保存到 deadline_keeper_research_demo/downloads/ 目录"""
    os.makedirs(_DOWNLOADS_DIR, exist_ok=True)
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    filepath = os.path.join(_DOWNLOADS_DIR, f"paper_{url_hash}.pdf")
    with open(filepath, "wb") as f:
        f.write(pdf_bytes)
    return os.path.abspath(filepath)


# ── 工具定义 ─────────────────────────────────────────

@tool("download-paper")
def download_paper(url: str) -> str:
    """Download a scientific paper PDF, save it locally, and extract its text.

    The PDF is automatically saved to the deadline_keeper_research_demo/downloads/ folder.
    Use this when the user asks to download, save, or read a specific paper.

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

                # 保存到本地
                local_path = _save_to_local(pdf_bytes, url)

                # 提取文本
                pdf_file = io.BytesIO(pdf_bytes)
                with pdfplumber.open(pdf_file) as pdf:
                    text = ""
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"

                # 截断显示文本（全文已保存本地，无需全部输出）
                display_text = text
                max_chars = 1500
                if len(text) > max_chars:
                    display_text = (
                        text[:max_chars]
                        + f"\n\n... (全文共 {len(text)} 字符, {len(pdf.pages)} 页"
                        + f", 已保存至 {local_path})"
                    )

                return (
                    f"[本地保存] {local_path}\n"
                    f"[来源] {url}\n"
                    f"[页数] {len(pdf.pages)} 页 | [大小] {len(pdf_bytes)/1024:.0f} KB\n"
                    f"[预览]\n{display_text}"
                )
            elif attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 2))
            else:
                raise Exception(
                    f"Failed to download paper: HTTP {response.status}"
                )
    except Exception as e:
        return f"Error downloading paper: {e}"


def get_tools():
    """插件入口: 返回此模块提供的工具列表"""
    return [download_paper]
