"""本地教学资料文本加载；只处理已上传的字节，不向外部服务传送原始文件。"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re


class DocumentLoadError(ValueError):
    """文件类型不支持或无法解析为有效文字。"""


@dataclass(frozen=True)
class LoadedDocument:
    source_type: str
    text: str
    page_count: int | None = None


_SUPPORTED_SUFFIXES = {".pdf": "pdf", ".docx": "docx", ".md": "markdown", ".markdown": "markdown", ".txt": "txt"}


def supported_source_type(source_name: str) -> str:
    source_type = _SUPPORTED_SUFFIXES.get(Path(source_name).suffix.lower())
    if source_type is None:
        raise DocumentLoadError("仅支持 PDF、DOCX、Markdown 和 TXT 格式的资料")
    return source_type


def clean_text(raw_text: str) -> str:
    """保留段落语义，移除控制字符与无意义空白。"""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_document_bytes(source_name: str, content: bytes) -> LoadedDocument:
    """将支持的资料转换为清洗后的纯文本。"""
    if not content:
        raise DocumentLoadError("上传资料为空")
    source_type = supported_source_type(source_name)
    try:
        if source_type in {"markdown", "txt"}:
            raw_text, page_count = content.decode("utf-8-sig"), None
        elif source_type == "docx":
            from docx import Document

            document = Document(BytesIO(content))
            paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    paragraphs.append(" | ".join(cell.text.strip() for cell in row.cells if cell.text.strip()))
            raw_text, page_count = "\n\n".join(paragraphs), None
        else:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            raw_text, page_count = "\n\n".join(pages), len(pages)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DocumentLoadError("资料无法解析，请确认文件未损坏且文字可提取") from exc
    text = clean_text(raw_text)
    if not text:
        raise DocumentLoadError("未从资料中提取到可用文字；扫描件请先进行 OCR")
    return LoadedDocument(source_type=source_type, text=text, page_count=page_count)
