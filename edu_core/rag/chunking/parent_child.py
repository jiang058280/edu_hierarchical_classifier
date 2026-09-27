"""教学资料的确定性父子分块。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


@dataclass(frozen=True)
class ChunkDraft:
    chunk_kind: str
    order_no: int
    content: str
    content_hash: str
    parent_order_no: int | None = None
    chapter: str | None = None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _heading(paragraph: str) -> str | None:
    match = re.match(r"^(?:#{1,6}\s+|第[一二三四五六七八九十\d]+[章节单元]\s*)?(.{2,80})$", paragraph.strip())
    if match and (paragraph.startswith("#") or paragraph.startswith("第")):
        return match.group(1).strip()
    return None


def _split_to_limit(text: str, limit: int) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit + 1]
        cut = max(window.rfind(mark) for mark in ("\n", "。", "！", "？", ";", "；", ".", "!", "?"))
        cut = limit if cut < max(1, limit // 3) else cut + 1
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return [piece for piece in pieces if piece]


def build_parent_child_chunks(text: str, *, parent_chars: int, child_chars: int,
                              overlap_chars: int = 0) -> list[ChunkDraft]:
    if parent_chars < 100 or child_chars < 50 or child_chars > parent_chars:
        raise ValueError("分块长度配置不合法")
    if overlap_chars < 0 or overlap_chars >= parent_chars:
        raise ValueError("重叠长度必须非负且小于父块长度")
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    parent_texts: list[tuple[str, str | None]] = []
    buffer: list[str] = []
    size, chapter = 0, None
    for paragraph in paragraphs:
        if detected_heading := _heading(paragraph):
            # 先按旧章节归档，不能把前一章缓冲区标成新章节。
            if buffer:
                parent_texts.extend((part, chapter) for part in
                                    _split_to_limit("\n\n".join(buffer), parent_chars))
                buffer, size = [], 0
            chapter = detected_heading
        if len(paragraph) > parent_chars:
            # 长段落也必须保持原文顺序，不能越过未输出的短段落。
            if buffer:
                parent_texts.extend((part, chapter) for part in
                                    _split_to_limit("\n\n".join(buffer), parent_chars))
                buffer, size = [], 0
            parent_texts.extend((part, chapter) for part in _split_to_limit(paragraph, parent_chars))
            continue
        addition = len(paragraph) + (2 if buffer else 0)
        if buffer and size + addition > parent_chars:
            joined = "\n\n".join(buffer)
            parent_texts.extend((part, chapter) for part in _split_to_limit(joined, parent_chars))
            available = max(0, parent_chars - len(paragraph) - 2)
            tail_size = min(overlap_chars, available)
            tail = joined[-tail_size:].strip() if tail_size else ""
            buffer, size = ([tail] if tail else []), len(tail)
        size += len(paragraph) + (2 if buffer else 0)
        buffer.append(paragraph)
    if buffer:
        parent_texts.extend((part, chapter) for part in _split_to_limit("\n\n".join(buffer), parent_chars))

    chunks: list[ChunkDraft] = []
    child_order_no = 0
    for parent_order, (parent_text, chapter) in enumerate(parent_texts, start=1):
        chunks.append(ChunkDraft("parent", parent_order, parent_text, _hash(parent_text), chapter=chapter))
        for child_text in _split_to_limit(parent_text, child_chars):
            child_order_no += 1
            chunks.append(ChunkDraft("child", child_order_no, child_text, _hash(child_text),
                                     parent_order_no=parent_order, chapter=chapter))
    return chunks
