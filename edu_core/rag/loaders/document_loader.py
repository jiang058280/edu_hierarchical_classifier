"""本地教学资料文本加载；只处理已上传的字节，不向外部服务传送原始文件。"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re

from edu_core.config.settings import Settings
from .ocr import OCRError, recognize_pages
from .math_cleanup import normalize_ocr_math


class DocumentLoadError(ValueError):
    """文件类型不支持或无法解析为有效文字。"""


@dataclass(frozen=True)
class LoadedDocument:
    source_type: str
    text: str
    page_count: int | None = None
    ocr_pages: tuple[int, ...] = ()
    ocr_raw_text: str | None = None
    ocr_corrections: tuple[dict, ...] = ()


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


def load_document_bytes(source_name: str, content: bytes, *, settings: Settings | None = None) -> LoadedDocument:
    """将支持的资料转换为清洗后的纯文本。"""
    if not content:
        raise DocumentLoadError("上传资料为空")
    source_type = supported_source_type(source_name)
    ocr_pages = ()
    raw_ocr, corrections = None, []
    try:
        if source_type in {"markdown", "txt"}:
            raw_text, page_count = content.decode("utf-8-sig"), None
        elif source_type == "docx":
            from docx import Document
            from docx.text.paragraph import Paragraph

            document = Document(BytesIO(content))
            paragraphs = []
            for block in document.iter_inner_content():
                if isinstance(block, Paragraph):
                    if block.text.strip():
                        paragraphs.append(block.text)
                else:
                    for row in block.rows:
                        # 保留空单元格的位置，避免后续列与表头错位。
                        row_text = " | ".join(cell.text.strip() for cell in row.cells)
                        if any(cell.text.strip() for cell in row.cells):
                            paragraphs.append(row_text)
            raw_text, page_count = "\n\n".join(paragraphs), None
        else:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            if settings is not None and settings.rag_ocr_enabled:
                ocr_pages = tuple(index + 1 for index, text in enumerate(pages)
                                  if len(re.sub(r"\s", "", text)) < settings.rag_ocr_min_text_chars)
                recognized = recognize_pages(content, list(ocr_pages), settings)
                raw_ocr = "\n\n".join(f"[第 {page} 页]\n{text}" for page, text in recognized.items()) or None
                for page, text in recognized.items():
                    pages[page - 1], edits = normalize_ocr_math(text)
                    corrections.extend(edit | {"page": page} for edit in edits)
            raw_text, page_count = "\n\n".join(pages), len(pages)
    except OCRError as exc:
        raise DocumentLoadError(str(exc)) from None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DocumentLoadError("资料无法解析，请确认文件未损坏且文字可提取") from exc
    text = clean_text(raw_text)
    if not text:
        raise DocumentLoadError("未从资料中提取到可用文字；扫描件请先进行 OCR")
    return LoadedDocument(source_type=source_type, text=text, page_count=page_count, ocr_pages=ocr_pages,
                          ocr_raw_text=raw_ocr, ocr_corrections=tuple(corrections))
