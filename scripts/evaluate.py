import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, TypeAdapter

from app.config import load_settings
from app.evidence import normalized
from app.models import Answer, Document, Page


class Case(BaseModel):
    id: str
    question: str
    files: list[str]
    expected_status: str
    expected_text: list[str]
    expected_pages: list[tuple[str, int]]
    category: Literal["accounting", "retrieval", "abstention"]


class Result(BaseModel):
    id: str
    category: str
    status_correct: bool
    expected_facts_present: bool
    source_page_coverage: float | None
    citation_excerpts_valid: bool
    answer: Answer


def checked(response: httpx.Response) -> httpx.Response:
    if response.is_error:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path}: HTTP {response.status_code}: {response.text}"
        )
    return response


def upload_samples(client: httpx.Client, cases: list[Case]) -> dict[str, Document]:
    existing = TypeAdapter(list[Document]).validate_json(
        checked(client.get("/api/documents")).content
    )
    by_hash = {document.sha256: document for document in existing}
    documents: dict[str, Document] = {}
    for filename in dict.fromkeys(filename for case in cases for filename in case.files):
        content = (Path("samples") / filename).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        document = by_hash.get(digest)
        if document is None:
            document = Document.model_validate_json(
                checked(client.post("/api/documents", files={"file": (filename, content)})).content
            )
        if document.status == "failed":
            raise RuntimeError(
                f"{filename}: {document.error}. Retry explicitly after addressing the failure."
            )
        documents[filename] = document
    deadline = time.monotonic() + 900
    while any(document.status != "ready" for document in documents.values()):
        if time.monotonic() > deadline:
            raise TimeoutError(
                "Sample ingestion exceeded 15 minutes. Inspect document processing errors."
            )
        time.sleep(2)
        documents = {
            name: Document.model_validate_json(
                checked(client.get(f"/api/documents/{document.id}")).content
            )
            for name, document in documents.items()
        }
        for name, document in documents.items():
            if document.status == "failed":
                raise RuntimeError(f"{name}: {document.error}")
    return documents


def evaluate_case(client: httpx.Client, case: Case, documents: dict[str, Document]) -> Result:
    response = checked(
        client.post(
            "/api/chat",
            json={
                "question": case.question,
                "document_ids": [documents[name].id for name in case.files],
                "as_of": "2026-10-31",
                "history": [],
            },
        )
    )
    answer = Answer.model_validate_json(response.content)
    text = " ".join(
        [
            answer.title,
            *(claim.text for claim in answer.claims),
            *(issue for issue in answer.issues),
        ]
    )
    actual_pages = {(source.document_name, source.page) for source in answer.sources}
    covered = sum(tuple(expected) in actual_pages for expected in case.expected_pages)
    page_cache: dict[str, list[Page]] = {}
    for source in answer.sources:
        if source.document_id not in page_cache:
            page_cache[source.document_id] = TypeAdapter(list[Page]).validate_json(
                checked(client.get(f"/api/documents/{source.document_id}/pages")).content
            )
    valid = all(
        any(
            page.number == source.page and normalized(source.excerpt) in normalized(page.text)
            for page in page_cache[source.document_id]
        )
        for source in answer.sources
    )
    return Result(
        id=case.id,
        category=case.category,
        status_correct=answer.status == case.expected_status,
        expected_facts_present=all(
            expected.casefold() in text.casefold() for expected in case.expected_text
        ),
        source_page_coverage=covered / len(case.expected_pages) if case.expected_pages else None,
        citation_excerpts_valid=valid,
        answer=answer,
    )


def main() -> None:
    """Run paid, real-provider checks against a running application; never fabricate scores."""
    parser = argparse.ArgumentParser(
        description="Upload synthetic fixtures and run live model evaluation."
    )
    parser.add_argument("--url", required=True)
    arguments = parser.parse_args()
    settings = load_settings()
    cases = TypeAdapter(list[Case]).validate_json(Path("samples/evaluation.json").read_text())
    with httpx.Client(
        base_url=arguments.url,
        headers={"Authorization": f"Bearer {settings.reviewer_key.get_secret_value()}"},
        timeout=240,
    ) as client:
        documents = upload_samples(client, cases)
        results = [evaluate_case(client, case, documents) for case in cases]
    report = {
        "model": settings.chat_model,
        "embedding_model": settings.embedding_model,
        "note": "Source-page coverage measures evidence returned to the user, not semantic entailment. Claim support still needs human review.",
        "accounting_correct": sum(
            result.status_correct and result.expected_facts_present
            for result in results
            if result.category == "accounting"
        ),
        "accounting_cases": sum(result.category == "accounting" for result in results),
        "abstention_correct": sum(
            result.status_correct for result in results if result.category == "abstention"
        ),
        "abstention_cases": sum(result.category == "abstention" for result in results),
        "citation_excerpts_valid": sum(result.citation_excerpts_valid for result in results),
        "results": [result.model_dump() for result in results],
    }
    Path("evaluation-results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    if not all(
        result.status_correct
        and result.expected_facts_present
        and result.citation_excerpts_valid
        and result.source_page_coverage in (None, 1.0)
        for result in results
    ):
        raise SystemExit("Evaluation failures were recorded in evaluation-results.json.")


if __name__ == "__main__":
    main()
