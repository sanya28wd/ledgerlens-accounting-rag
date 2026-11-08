from datetime import date
from pathlib import Path

from app.accounting import invoice_details, statement_summary, vendor_total
from app.evidence import validate_accounting
from app.extract import extract_pages
from app.models import AccountingData, Evidence, Fact, Transaction
from app.storage import Store
from tests.helpers import field, index_invoice, invoice_record


def test_complete_statement_expenses_exclude_transfers_and_deposits(store: Store) -> None:
    path = Path("samples/october-statement.pdf")
    pages = extract_pages(path, 25)
    transactions: list[Transaction] = []
    expected = [
        ("2026-10-02", "Utilities expense", "125.00"),
        ("2026-10-05", "Software expense", "75.00"),
        ("2026-10-08", "Office supplies expense", "200.00"),
    ]
    for day, description, amount in expected:
        transactions.append(
            Transaction(
                date=field(pages, day, day),
                description=field(pages, day, description),
                amount=field(pages, day, amount),
                currency=field(pages, "Currency:", "AED"),
                category="expense",
                direction="debit",
            )
        )
    transactions.append(
        Transaction(
            date=field(pages, "2026-10-12", "2026-10-12"),
            description=field(pages, "2026-10-12", "Internal account transfer"),
            amount=field(pages, "2026-10-12", "500.00"),
            currency=field(pages, "Currency:", "AED"),
            category="transfer",
            direction="debit",
        )
    )
    document = store.add_document(
        "statement", path.name, "application/pdf", path.read_bytes(), "statement"
    )
    data = validate_accounting(
        AccountingData(
            document_type="statement",
            invoices=[],
            transactions=transactions,
            complete=True,
            issues=[],
        ),
        pages,
    )
    document = document.model_copy(update={"status": "ready", "accounting": data})
    result = statement_summary([document], date(2026, 10, 31))
    assert "400.00" in result.claims[0].text
    assert len(result.calculations) == 3
    assert any("1 non-expense" in issue for issue in result.issues)


def test_two_invoices_and_continuation_page_sources(store: Store) -> None:
    path = Path("samples/two-invoices.pdf")
    pages = extract_pages(path, 25)
    invoices = [
        invoice_record(
            [pages[0]], "BUN-001", "Bundle Office", "50.00", "AED", "2026-10-01", "unpaid", "50.00"
        ),
        invoice_record(
            [pages[1]], "BUN-002", "Bundle Office", "75.00", "AED", "2026-10-02", "unpaid", "75.00"
        ),
    ]
    document = store.add_document(
        "bundle", path.name, "application/pdf", path.read_bytes(), "bundle"
    )
    accounting = validate_accounting(
        AccountingData(
            document_type="invoice", invoices=invoices, transactions=[], complete=True, issues=[]
        ),
        pages,
    )
    document = document.model_copy(update={"status": "ready", "accounting": accounting})
    total = vendor_total("Bundle Office", [document], date(2026, 10, 31))
    assert "125.00" in total.claims[0].text
    assert {source.page for source in total.sources} == {1, 2}
    path = Path("samples/multipage-invoice.pdf")
    pages = extract_pages(path, 25)
    continued = invoice_record(
        pages, "MULTI-001", "Horizon Office", "100.00", "AED", "2026-10-01", "unpaid", "100.00"
    )
    data = validate_accounting(
        AccountingData(
            document_type="invoice", invoices=[continued], transactions=[], complete=True, issues=[]
        ),
        pages,
    )
    detail = invoice_details([document.model_copy(update={"accounting": data})], date(2026, 10, 31))
    assert len(detail.claims) == 1
    assert {source.page for source in detail.sources} == {1, 2}


def test_conflicting_versions_do_not_disappear_into_vendor_clarification(store: Store) -> None:
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
    assert document.accounting is not None
    invoice = document.accounting.invoices[0]
    changed = invoice.model_copy(
        update={
            "payment_status": Fact(
                value="paid", evidence=Evidence(page=1, excerpt="Payment status: paid")
            )
        }
    )
    other = document.model_copy(
        update={
            "id": "conflicting",
            "accounting": document.accounting.model_copy(update={"invoices": [changed]}),
        }
    )
    result = vendor_total("Cedar Supplies", [document, other], date(2026, 10, 31))
    assert result.status == "insufficient_information"
    assert not result.calculations
    assert any("Conflicting versions" in issue for issue in result.issues)


def test_missing_currency_remains_searchable_but_not_a_complete_total(store: Store) -> None:
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
    assert document.accounting is not None
    invoice = document.accounting.invoices[0].model_copy(
        update={"currency": Fact(value=None, evidence=None)}
    )
    incomplete = document.model_copy(
        update={
            "accounting": document.accounting.model_copy(
                update={
                    "invoices": [invoice],
                    "complete": False,
                    "issues": ["Currency unresolved."],
                }
            )
        }
    )
    result = vendor_total("Cedar Supplies", [incomplete], date(2026, 10, 31))
    assert result.status == "insufficient_information"
    assert store.keyword_search('"Cedar"', [document.id], 20)
    assert any("currency" in issue for issue in result.issues)
