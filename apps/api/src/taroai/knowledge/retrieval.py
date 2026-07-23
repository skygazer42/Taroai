import re
from math import sqrt

from taroai.knowledge.models import DocumentChunk, RetrievalRequest, RetrievalResult


def retrieve_chunks(chunks: list[DocumentChunk], request: RetrievalRequest) -> list[RetrievalResult]:
    query_terms = retrieval_terms(request.query)
    results: list[RetrievalResult] = []
    for chunk in chunks:
        if not _can_read(chunk, request):
            continue
        score = _score_chunk(request, query_terms, chunk)
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
                sensitivity_level=chunk.sensitivity_level,
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


def _score_chunk(
    request: RetrievalRequest,
    query_terms: set[str],
    chunk: DocumentChunk,
) -> float:
    if request.query_embedding and chunk.embedding:
        return cosine_similarity(request.query_embedding, chunk.embedding)
    return term_relevance(query_terms, chunk.content)


def term_relevance(query_terms: set[str], content: str) -> float:
    if not query_terms:
        return 0
    content_terms = retrieval_terms(content)
    matches = query_terms & content_terms
    return len(matches) / len(query_terms)


def cosine_similarity(query_embedding: list[float], chunk_embedding: list[float]) -> float:
    if len(query_embedding) != len(chunk_embedding):
        return 0
    query_norm = sqrt(sum(value * value for value in query_embedding))
    chunk_norm = sqrt(sum(value * value for value in chunk_embedding))
    if query_norm == 0 or chunk_norm == 0:
        return 0
    score = sum(
        query_value * chunk_value
        for query_value, chunk_value in zip(query_embedding, chunk_embedding)
    ) / (query_norm * chunk_norm)
    return max(0, score)


def retrieval_terms(value: str) -> set[str]:
    value = value.lower()
    terms = set(re.findall(r"[a-z0-9_]+", value))
    # ponytail: CJK bigrams are the no-service fallback; enable embeddings for semantic recall.
    for text in re.findall(r"[\u3400-\u9fff]+", value):
        terms.update(text[index : index + 2] for index in range(max(1, len(text) - 1)))
    return terms
