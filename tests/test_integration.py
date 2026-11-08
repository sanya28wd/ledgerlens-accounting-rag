import asyncio
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.accounting import overdue_invoices, vendor_total
from app.chunking import count_tokens, split_pages
from app.config import Settings
from app.errors import AppError
from app.extract import extract_pages
from app.ingestion import native_extract
from app.main import create_app
from app.models import Page
from app.storage import Store
from tests.helpers import index_invoice


def test_sqlite_vector_keyword_persistence_and_delete(store: Store, settings: Settings) -> None:
    document = index_invoice(
        store,
        Path("samples/cedar-001.pdf"),
        "CED-001",
        "Cedar Supplies",
        "1200.00",
        "AED",
        "2026-10-01",
        "unpaid",
        "1200.00",
    )
    assert document.accounting is not None and document.accounting.complete
    assert store.semantic_search([1.0, 0.0, 0.0], [document.id], 20)[0].document_id == document.id
    assert "CED-001" in store.keyword_search('"CED"', [document.id], 20)[0].text
    restarted = Store(settings)
    restarted.initialize()
    assert restarted.get_document(document.id).status == "ready"
    restarted.delete(document.id)
    assert restarted.list_documents() == []
    assert restarted.semantic_search([1.0, 0.0, 0.0], [document.id], 20) == []
    assert not restarted.file_path(document.id).exists()


def test_ocr_images_and_mixed_pages() -> None:
    for name in ("cedar-scan.png", "cedar-scan.pdf", "mixed-pages.pdf"):
        pages = extract_pages(Path("samples") / name, 25)
        assert any(page.ocr for page in pages)
        assert "CED-001" in " ".join(page.text for page in pages)
        assert "1200.00" in " ".join(page.text for page in pages)
    mixed = extract_pages(Path("samples/mixed-pages.pdf"), 25)
    assert [page.ocr for page in mixed] == [False, True]


def test_native_subprocess_and_page_limits(store: Store, settings: Settings) -> None:
    content = Path("samples/cedar-001.pdf").read_bytes()
    document = store.add_document(
        "subprocess", "sample.pdf", "application/pdf", content, sha256(content).hexdigest()
    )
    pages = asyncio.run(native_extract(document, store, settings))
    assert pages[0].number == 1 and pages[0].blocks
    with pytest.raises(AppError, match="1–1 pages"):
        extract_pages(Path("samples/two-invoices.pdf"), 1)


def test_complete_vendor_totals_exceed_search_limits(store: Store) -> None:
    documents = [
        index_invoice(
            store,
            Path(f"samples/scale/aur-{index:03d}.pdf"),
            f"AUR-{index:03d}",
            "Aurora Paper",
            "10.25",
            "AED",
            "2026-10-01",
            "unpaid",
            "10.25",
        )
        for index in range(1, 25)
    ]
    answer = vendor_total("Aurora Paper", documents, date(2026, 10, 31))
    assert len(answer.calculations) == 24
    assert "246.00" in answer.claims[0].text
    assert "24 invoices" in answer.claims[0].text


def test_duplicate_scan_and_overdue_semantics(store: Store) -> None:
    documents = [
        index_invoice(
            store,
            Path("samples/cedar-001.pdf"),
            "CED-001",
            "Cedar Supplies",
            "1200.00",
            "AED",
            "2026-10-01",
            "unpaid",
            "1200.00",
        ),
        index_invoice(
            store,
            Path("samples/cedar-scan.pdf"),
            "CED-001",
            "Cedar Supplies",
            "1200.00",
            "AED",
            "2026-10-01",
            "unpaid",
            "1200.00",
        ),
        index_invoice(
            store,
            Path("samples/cedar-003.pdf"),
            "CED-003",
            "Cedar Supplies",
            "450.00",
            "AED",
            "2026-09-30",
            "paid",
            "0.00",
        ),
        index_invoice(
            store,
            Path("samples/atlas-unknown.pdf"),
            "ATL-001",
            "Atlas Services",
            "900.00",
            "AED",
            "2026-10-15",
            None,
            None,
        ),
    ]
    total = vendor_total("Cedar Supplies", documents, date(2026, 10, 31))
    assert "1,650.00" in total.claims[0].text
    assert any("Duplicate invoice" in issue for issue in total.issues)
    overdue = overdue_invoices(documents, date(2026, 10, 31))
    assert any(
        "CED-001" in claim.text and "confirmed overdue" in claim.text for claim in overdue.claims
    )
    assert any("CED-003" in claim.text and "paid /" in claim.text for claim in overdue.claims)
    assert any("ATL-001" in claim.text and "unknown" in claim.text for claim in overdue.claims)


def test_chunk_boundaries_and_interrupted_jobs(store: Store, settings: Settings) -> None:
    pages = extract_pages(Path("samples/multipage-invoice.pdf"), 25)
    chunks = split_pages("demo", pages, 50, 10)
    assert {chunk.page for chunk in chunks} == {1, 2}
    assert all(chunk.text in pages[chunk.page - 1].text for chunk in chunks)
    unicode_page = Page(number=1, text="Supplier reference 界" * 100, ocr=False, blocks=[])
    unicode_chunks = split_pages("unicode", [unicode_page], 50, 0)
    assert "".join(chunk.text for chunk in unicode_chunks) == unicode_page.text
    assert all(count_tokens(chunk.text) <= 50 for chunk in unicode_chunks)
    store.add_document(
        "interrupted", "document.pdf", "application/pdf", b"%PDF-demo", "interrupted"
    )
    assert store.claim_next() is not None
    Store(settings).initialize()
    assert store.get_document("interrupted").status == "failed"
    assert store.retry("interrupted").status == "queued"


def test_api_access_validation_and_missing_key(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/api/documents").status_code == 401
        headers = {"Authorization": "Bearer integration-reviewer-secret"}
        assert client.get("/api/documents", headers=headers).json() == []
        assert client.get("/api/status", headers=headers).json()["model_configured"] is False
        missing = client.post(
            "/api/documents",
            headers=headers,
            files={"file": ("sample.pdf", b"%PDF-data", "application/pdf")},
        )
        assert missing.status_code == 503 and missing.json()["code"] == "api_key_missing"
        invalid = client.post(
            "/api/chat", headers=headers, json={"question": "", "document_ids": []}
        )
        assert invalid.status_code == 422 and "request_id" in invalid.json()
        assert (
            client.get("/api/documents/../../etc/passwd/file", headers=headers).status_code == 404
        )


def test_api_rejects_unsupported_empty_and_large_files(settings: Settings) -> None:
    configured = settings.model_copy(update={"openai_api_key": SecretStr("invalid-test-key")})
    headers = {"Authorization": "Bearer integration-reviewer-secret"}
    with TestClient(create_app(configured)) as client:
        for name, content, expected in [
            ("empty.pdf", b"", "empty_file"),
            ("document.txt", b"not a document", "extraction_failed"),
            ("wrong.png", b"%PDF-test", "file_type_mismatch"),
            ("large.pdf", b"%PDF-" + b"0" * settings.max_file_bytes, "file_too_large"),
        ]:
            response = client.post(
                "/api/documents", headers=headers, files={"file": (name, content)}
            )
            assert response.status_code in {413, 422}
            assert response.json()["code"] == expected
