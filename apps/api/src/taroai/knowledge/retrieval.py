import re

from taroai.knowledge.models import DocumentChunk, RetrievalRequest, RetrievalResult


def retrieve_chunks(chunks: list[DocumentChunk], request: RetrievalRequest) -> list[RetrievalResult]:
    query_terms = _terms(request.query)
    results: list[RetrievalResult] = []
    for chunk in chunks:
        if not _can_read(chunk, request):
            continue
        score = _score(query_terms, chunk.content)
        if score <= 0:
            continue
        results.append(
            RetrievalResult(
                document_id=chunk.document_id,
                chunk_id=chunk.id,
                source_document_id=chunk.source_document_id,
                source_uri=chunk.source_uri,
                excerpt=chunk.content[:240],
                score=score,
                citation=chunk.citation,
            )
        )
    return sorted(results, key=lambda result: result.score, reverse=True)[: request.limit]


def _can_read(chunk: DocumentChunk, request: RetrievalRequest) -> bool:
    if chunk.tenant_id != request.tenant_id:
        return False
    if request.allowed_workspace_ids and chunk.workspace_id not in request.allowed_workspace_ids:
        return False
    if chunk.sensitivity_level > request.clearance_level:
        return False
    if not chunk.acl_subjects:
        return True
    return bool(set(chunk.acl_subjects) & set(request.acl_subjects))


def _score(query_terms: set[str], content: str) -> float:
    if not query_terms:
        return 0
    content_terms = _terms(content)
    matches = query_terms & content_terms
    return len(matches) / len(query_terms)


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-zA-Z0-9_]+", value.lower()) if term}
