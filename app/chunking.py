from uuid import uuid4

import tiktoken

from app.models import Chunk, Page


def count_tokens(text: str) -> int:
    return len(tiktoken.get_encoding("cl100k_base").encode(text))


def split_long_line(text: str, target: int) -> list[str]:
    """Split on character boundaries so token cuts never corrupt Unicode evidence."""
    segments: list[str] = []
    remaining = text
    while count_tokens(remaining) > target:
        low, high = 1, len(remaining)
        while low < high:
            midpoint = (low + high + 1) // 2
            if count_tokens(remaining[:midpoint]) <= target:
                low = midpoint
            else:
                high = midpoint - 1
        segments.append(remaining[:low])
        remaining = remaining[low:]
    if remaining:
        segments.append(remaining)
    return segments


def split_pages(document_id: str, pages: list[Page], target: int, overlap: int) -> list[Chunk]:
    """Keep page boundaries and full lines; split oversized lines by token count."""
    if target < 4 or not 0 <= overlap < target:
        raise ValueError(
            "Chunk target must be at least four tokens and overlap must be smaller than target."
        )
    chunks: list[Chunk] = []
    for page in pages:
        lines: list[str] = []
        for block in page.text.splitlines(keepends=True):
            lines.extend(split_long_line(block, target))
        buffer: list[str] = []
        for line in lines:
            if buffer and count_tokens("".join(buffer) + line) > target:
                chunks.append(
                    Chunk(
                        id=uuid4().hex,
                        document_id=document_id,
                        page=page.number,
                        text="".join(buffer),
                    )
                )
                kept: list[str] = []
                for previous in reversed(buffer):
                    if count_tokens("".join([previous, *kept])) > overlap:
                        break
                    kept = [previous, *kept]
                buffer = kept
                while buffer and count_tokens("".join(buffer) + line) > target:
                    buffer = buffer[1:]
            buffer = [*buffer, line]
        if "".join(buffer).strip():
            chunks.append(
                Chunk(
                    id=uuid4().hex, document_id=document_id, page=page.number, text="".join(buffer)
                )
            )
    return chunks
