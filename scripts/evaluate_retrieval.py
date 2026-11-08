"""Compare real retrievers on fixed, binary page labels; no answer generation or tuning."""

import asyncio
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from pydantic import Field

from app.config import load_settings
from app.models import Chunk, Model
from app.provider import Provider
from app.retrieval import fuse_rankings
from app.storage import Store


class RetrievalCase(Model):
    id: str
    question: str
    relevant_pages: list[tuple[str, int]] = Field(min_length=1)


class RetrievalDataset(Model):
    files: list[str] = Field(min_length=1)
    cases: list[RetrievalCase] = Field(min_length=1)


class Metrics(Model):
    precision: float
    recall: float
    reciprocal_rank: float
    ndcg: float


class RankingResult(Model):
    case_id: str
    method: str
    pages: list[tuple[str, int]]
    metrics: dict[str, Metrics]


def score_ranking(ranked: list[tuple[str, int]], relevant: set[tuple[str, int]], k: int) -> Metrics:
    """Binary page relevance; missing positions count against Precision@k, not Recall@k."""
    if not relevant or k < 1:
        raise ValueError("Ranking evaluation requires nonempty relevance labels and positive k.")
    if len(ranked) != len(set(ranked)):
        raise ValueError("Ranked pages must be deduplicated before scoring.")
    hits = [position for position, page in enumerate(ranked[:k], 1) if page in relevant]
    dcg = sum(1 / math.log2(position + 1) for position in hits)
    ideal = sum(1 / math.log2(position + 1) for position in range(1, min(k, len(relevant)) + 1))
    return Metrics(
        precision=len(hits) / k,
        recall=len(hits) / len(relevant),
        reciprocal_rank=1 / hits[0] if hits else 0,
        ndcg=dcg / ideal,
    )


def ranked_pages(chunks: list[Chunk], names: dict[str, str]) -> list[tuple[str, int]]:
    """Keep each page's first occurrence without changing the underlying chunk ranking."""
    return list(dict.fromkeys((names[chunk.document_id], chunk.page) for chunk in chunks))


async def main() -> None:
    settings = load_settings()
    store = Store(settings)
    dataset_path = Path("samples/retrieval-evaluation.json")
    dataset = RetrievalDataset.model_validate_json(dataset_path.read_text())
    documents = []
    for filename in dataset.files:
        digest = hashlib.sha256((Path("samples") / filename).read_bytes()).hexdigest()
        document = store.find_hash(digest)
        if document is None or document.status != "ready":
            raise ValueError(f"{filename} must be ingested and ready. Run scripts.evaluate first.")
        documents.append(document)
    names = {document.id: document.name for document in documents}
    available = {
        (document.name, page.number) for document in documents for page in store.pages(document.id)
    }
    for case in dataset.cases:
        if not set(case.relevant_pages) <= available:
            raise ValueError(f"Unknown labeled page in retrieval case {case.id}.")
    provider = Provider(settings)
    try:
        vectors = await provider.embeddings([case.question for case in dataset.cases])
    finally:
        await provider.close()
    results: list[RankingResult] = []
    ids = [document.id for document in documents]
    for case, vector in zip(dataset.cases, vectors, strict=True):
        semantic = store.semantic_search(vector, ids, 20)
        # Same quoted OR-token query policy as app.retrieval.retrieve().
        terms = dict.fromkeys(re.findall(r"[\w]+", case.question))
        query = " OR ".join(f'"{term}"' for term in terms)
        lexical = store.keyword_search(query, ids, 20) if query else []
        for method, chunks in {
            "bm25": lexical[:8],
            "vector": semantic[:8],
            "hybrid_rrf": fuse_rankings(semantic, lexical, 8),
        }.items():
            pages = ranked_pages(chunks, names)
            results.append(
                RankingResult(
                    case_id=case.id,
                    method=method,
                    pages=pages,
                    metrics={
                        str(k): score_ranking(pages, set(case.relevant_pages), k)
                        for k in (1, 3, 5, 8)
                    },
                )
            )
    summary = {
        method: {
            str(k): {
                metric: mean(
                    getattr(result.metrics[str(k)], metric)
                    for result in results
                    if result.method == method
                )
                for metric in Metrics.model_fields
            }
            for k in (1, 3, 5, 8)
        }
        for method in ("bm25", "vector", "hybrid_rrf")
    }
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "embedding_model": settings.embedding_model,
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "case_count": len(dataset.cases),
        "corpus_pages": len(available),
        "notes": [
            "Small synthetic diagnostic set, not a held-out production benchmark.",
            "Binary page relevance; duplicate chunks on the same page are collapsed after the 8-chunk cutoff.",
            "All methods use the same selected corpus, 20 candidates per search and an 8-chunk final budget.",
            "Precision uses k as denominator, including unfilled positions. Recall uses all labeled relevant pages.",
            "MRR is the macro mean of reciprocal_rank; nDCG uses binary relevance and log2 discount.",
            "No-relevant-document questions belong to the separate abstention suite; retrieval does not decide abstention.",
        ],
        "summary": summary,
        "results": [result.model_dump() for result in results],
    }
    Path("retrieval-evaluation-results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
