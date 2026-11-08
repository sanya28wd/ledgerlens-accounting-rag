import re

from app.models import Chunk, Document, Source
from app.provider import Provider
from app.storage import Store


def fuse_rankings(semantic: list[Chunk], lexical: list[Chunk], limit: int) -> list[Chunk]:
    scores: dict[str, float] = {}
    chunks: dict[str, Chunk] = {}
    for ranking in (semantic, lexical):
        for rank, chunk in enumerate(ranking, 1):
            scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (60 + rank)
            chunks[chunk.id] = chunk
    return [
        chunks[key] for key in sorted(scores, key=lambda key: scores[key], reverse=True)[:limit]
    ]


async def retrieve(
    question: str, documents: list[Document], store: Store, provider: Provider
) -> list[Source]:
    ids = [document.id for document in documents]
    vectors = await provider.embeddings([question])
    semantic = store.semantic_search(vectors[0], ids, 20)
    terms = re.findall(r"[\w]+", question)
    query = " OR ".join(f'"{term}"' for term in dict.fromkeys(terms))
    lexical = store.keyword_search(query, ids, 20) if query else []
    names = {document.id: document.name for document in documents}
    return [
        Source(
            id=f"S{index}",
            document_id=chunk.document_id,
            document_name=names[chunk.document_id],
            page=chunk.page,
            excerpt=chunk.text,
        )
        for index, chunk in enumerate(fuse_rankings(semantic, lexical, 8), 1)
    ]


def full_sources(documents: list[Document], store: Store) -> list[Source]:
    return [
        Source(
            id=f"D{document.id}P{page.number}",
            document_id=document.id,
            document_name=document.name,
            page=page.number,
            excerpt=page.text,
        )
        for document in documents
        for page in store.pages(document.id)
        if page.text.strip()
    ]
