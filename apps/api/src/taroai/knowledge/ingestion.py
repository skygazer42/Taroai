import re

from taroai.knowledge.models import DocumentChunkCreate


def chunk_text_content(
    content: str,
    source_document_id: str,
    max_characters: int = 1200,
    overlap_characters: int = 120,
) -> list[DocumentChunkCreate]:
    if max_characters <= 0:
        raise ValueError("max_characters must be greater than 0")
    if overlap_characters < 0 or overlap_characters >= max_characters:
        raise ValueError(
            "overlap_characters must be greater than or equal to 0 and less than max_characters"
        )

    normalized = content.strip()
    if not normalized:
        return []

    chunks: list[DocumentChunkCreate] = []
    start = 0
    while start < len(normalized):
        end = min(start + max_characters, len(normalized))
        if end < len(normalized):
            split_at = _split_boundary(normalized, start, end)
            if split_at > start:
                end = split_at
        chunk_content = normalized[start:end].strip()
        if chunk_content:
            chunks.append(
                DocumentChunkCreate(
                    content=chunk_content,
                    citation={
                        "source_document_id": source_document_id,
                        "chunk_index": len(chunks),
                        "char_start": start,
                        "char_end": end,
                    },
                )
            )
        if end >= len(normalized):
            break
        next_start = max(0, end - overlap_characters)
        if next_start <= start:
            next_start = end
        start = _skip_leading_whitespace(normalized, next_start)

    chunk_count = len(chunks)
    return [
        chunk.model_copy(
            update={
                "citation": {
                    **chunk.citation,
                    "chunk_count": chunk_count,
                }
            }
        )
        for chunk in chunks
    ]


def _split_boundary(content: str, start: int, end: int) -> int:
    minimum = start + max(1, (end - start) // 2)
    for pattern in [r"\n\s*\n", r"(?<=[.!?])\s+", r"\s+"]:
        matches = list(re.finditer(pattern, content[start:end]))
        for match in reversed(matches):
            split_at = start + match.start()
            if split_at >= minimum:
                return split_at
    return end


def _skip_leading_whitespace(content: str, start: int) -> int:
    while start < len(content) and content[start].isspace():
        start += 1
    return start
