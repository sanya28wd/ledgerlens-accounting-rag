from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.chunking import split_pages
from app.evidence import validate_accounting
from app.extract import extract_pages, media_type
from app.models import AccountingData, Document, Evidence, Fact, Invoice, Page
from app.storage import Store


def field(pages: list[Page], label: str, value: str | None) -> Fact:
    if value is None:
        return Fact(value=None, evidence=None)
    for page in pages:
        for line in page.text.splitlines():
            if line.startswith(label):
                return Fact(value=value, evidence=Evidence(page=page.number, excerpt=line))
    raise ValueError(f"Fixture lacks expected label {label!r}")


def invoice_record(
    pages: list[Page],
    number: str,
    vendor: str,
    total: str,
    currency: str,
    due: str,
    payment_status: str | None,
    balance: str | None,
) -> Invoice:
    return Invoice(
        kind="invoice",
        number=field(pages, "Invoice number:", number),
        vendor=field(pages, "Vendor:", vendor),
        issue_date=field(pages, "Invoice date:", "2026-09-15"),
        due_date=field(pages, "Due date:", due),
        currency=field(pages, "Currency:", currency),
        subtotal=field(pages, "Subtotal:", total),
        tax=field(pages, "Tax:", "0.00"),
        total=field(pages, "Total amount:", total),
        amount_paid=field(pages, "Amount paid:", None),
        balance_due=field(pages, "Balance due:", balance),
        payment_status=field(pages, "Payment status:", payment_status),
    )


def index_invoice(
    store: Store,
    path: Path,
    number: str,
    vendor: str,
    total: str,
    currency: str,
    due: str,
    payment_status: str | None,
    balance: str | None,
) -> Document:
    content = path.read_bytes()
    document = store.add_document(
        uuid4().hex, path.name, media_type(content), content, sha256(content).hexdigest()
    )
    pages = extract_pages(path, 25)
    invoice = invoice_record(pages, number, vendor, total, currency, due, payment_status, balance)
    accounting = validate_accounting(
        AccountingData(
            document_type="invoice", invoices=[invoice], transactions=[], complete=True, issues=[]
        ),
        pages,
    )
    chunks = split_pages(document.id, pages, 500, 75)
    store.commit_ingestion(
        document.id, pages, chunks, [[1.0, 0.0, 0.0] for _ in chunks], accounting
    )
    return store.get_document(document.id)
