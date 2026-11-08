from decimal import Decimal

import pytest

from app.accounting import format_amount
from app.answers import validate_generated
from app.errors import EvidenceError
from app.evidence import date_in_excerpt, validate_fact
from app.models import Claim, Evidence, Fact, GeneratedAnswer, Page


def test_financial_field_evidence_rejects_wrong_label_and_ambiguous_dates() -> None:
    assert format_amount(Decimal("1234.125")) == "1,234.125"
    pages = [
        Page(
            number=1,
            text="Subtotal: AED 100.00\nTotal amount: AED 120.00\nDue date: 03/04/2026",
            ocr=False,
            blocks=[],
        )
    ]
    value, issue = validate_fact(
        "total",
        Fact(value="100.00", evidence=Evidence(page=1, excerpt="Subtotal: AED 100.00")),
        pages,
    )
    assert value.value is None and issue
    value, issue = validate_fact(
        "total",
        Fact(value="120.00", evidence=Evidence(page=1, excerpt="Total amount: AED 120.00")),
        pages,
    )
    assert value.value == "120.00" and issue is None
    assert not date_in_excerpt("2026-04-03", "Due date: 03/04/2026")
    assert date_in_excerpt("2026-04-13", "Due date: 13/04/2026")


def test_invalid_answer_citations_are_not_displayed() -> None:
    generated = GeneratedAnswer(
        status="answered",
        title="Unsupported",
        claims=[Claim(text="A fabricated total", source_ids=["invented"])],
        issues=[],
    )
    with pytest.raises(EvidenceError):
        validate_generated(generated, [])
