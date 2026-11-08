from collections import defaultdict
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import cast

from app.evidence import decimal_value, normalized
from app.models import Answer, CalculationRow, Claim, Document, Fact, Invoice, Source


def format_amount(amount: Decimal) -> str:
    """Preserve supplied decimal precision, including currencies with three decimals."""
    places = max(2, len(format(amount, "f").partition(".")[2]))
    return f"{amount:,.{places}f}"


def fact_source(document: Document, fact: Fact) -> Source | None:
    if fact.evidence is None or fact.value is None:
        return None
    evidence = fact.evidence
    identifier = (
        f"{document.id[:8]}p{evidence.page}-{sha256(evidence.excerpt.encode()).hexdigest()[:10]}"
    )
    return Source(
        id=identifier,
        document_id=document.id,
        document_name=document.name,
        page=evidence.page,
        excerpt=evidence.excerpt,
    )


def sources_for_invoice(document: Document, invoice: Invoice) -> list[Source]:
    sources = [
        fact_source(document, cast(Fact, getattr(invoice, name)))
        for name in Invoice.model_fields
        if name != "kind"
    ]
    return list({source.id: source for source in sources if source is not None}.values())


def accounting_issues(documents: list[Document]) -> list[str]:
    return [
        f"{document.name}: {issue}"
        for document in documents
        if document.accounting
        for issue in document.accounting.issues
    ] + [
        f"{document.name}: accounting extraction is incomplete; a complete result cannot be established."
        for document in documents
        if document.accounting
        and not document.accounting.complete
        and not document.accounting.issues
    ]


def unique_invoices(documents: list[Document]) -> tuple[list[tuple[Document, Invoice]], list[str]]:
    groups: dict[tuple[str, str, str], list[tuple[Document, Invoice]]] = defaultdict(list)
    issues: list[str] = []
    for document in documents:
        if document.accounting is None:
            continue
        for index, invoice in enumerate(document.accounting.invoices):
            if invoice.vendor.value is None or invoice.number.value is None:
                issues.append(
                    f"{document.name}: invoice {index + 1} has no verified vendor or number; excluded from calculations."
                )
                continue
            groups[
                (normalized(invoice.vendor.value), normalized(invoice.number.value), invoice.kind)
            ].append((document, invoice))
    result: list[tuple[Document, Invoice]] = []
    for group in groups.values():
        signatures = {
            tuple(
                cast(Fact, getattr(invoice, name)).value
                for name in Invoice.model_fields
                if name != "kind"
            )
            for _, invoice in group
        }
        if len(signatures) > 1:
            issues.append(
                f"Conflicting versions of invoice {group[0][1].number.value}: all versions excluded. Review {', '.join(document.name for document, _ in group)}."
            )
        else:
            result.append(group[0])
            if len(group) > 1:
                issues.append(
                    f"Duplicate invoice {group[0][1].number.value}: counted once; duplicate evidence in {', '.join(document.name for document, _ in group)}."
                )
    return result, issues


def vendor_total(vendor: str | None, documents: list[Document], as_of: date) -> Answer:
    records, duplicate_issues = unique_invoices(documents)
    vendors = {
        normalized(invoice.vendor.value): invoice.vendor.value
        for document in documents
        if document.accounting is not None
        for invoice in document.accounting.invoices
        if invoice.vendor.value
    }
    matches = (
        [normalized(vendor)]
        if vendor and normalized(vendor) in vendors
        else [key for key in vendors if vendor and normalized(vendor) in key]
    )
    if len(matches) != 1:
        return Answer(
            status="clarification_needed",
            title="Choose a vendor",
            claims=[],
            sources=[],
            calculations=[],
            issues=[
                "Specify one verified vendor name. Available vendors: "
                + ", ".join(sorted(vendors.values()))
            ],
            as_of=as_of.isoformat(),
            coverage="all_accounting_records",
        )
    selected = [
        (document, invoice)
        for document, invoice in records
        if invoice.vendor.value and normalized(invoice.vendor.value) == matches[0]
    ]
    issues = accounting_issues(documents) + duplicate_issues
    totals: dict[str, Decimal] = defaultdict(Decimal)
    rows: list[CalculationRow] = []
    sources: list[Source] = []
    for document, invoice in selected:
        if invoice.kind == "credit_note":
            issues.append(
                f"Credit note {invoice.number.value} is excluded from gross invoiced totals."
            )
            continue
        if invoice.total.value is None or invoice.currency.value is None:
            issues.append(
                f"Invoice {invoice.number.value} has no verified total or currency; excluded."
            )
            continue
        evidence = sources_for_invoice(document, invoice)
        sources.extend(evidence)
        totals[invoice.currency.value] += decimal_value(invoice.total.value)
        rows.append(
            CalculationRow(
                label=f"Invoice {invoice.number.value} · {document.name}",
                amount=invoice.total.value,
                currency=invoice.currency.value,
                source_ids=[source.id for source in evidence],
            )
        )
    incomplete = any(
        document.accounting is not None and not document.accounting.complete
        for document in documents
    ) or any("excluded" in issue for issue in issues)
    claims = [
        Claim(
            text=f"{'Verified subtotal' if incomplete else 'Total invoiced'} by {vendors[matches[0]]}: {currency} {format_amount(amount)} across {sum(row.currency == currency for row in rows)} invoices. Paid invoices are included.",
            source_ids=[
                source.id
                for source in sources
                if any(row.currency == currency and source.id in row.source_ids for row in rows)
            ],
        )
        for currency, amount in sorted(totals.items())
    ]
    return Answer(
        status="answered" if claims else "insufficient_information",
        title=f"Invoices from {vendors[matches[0]]}",
        claims=claims,
        sources=list({source.id: source for source in sources}.values()),
        calculations=rows,
        issues=issues,
        as_of=as_of.isoformat(),
        coverage="all_accounting_records",
    )


def overdue_invoices(documents: list[Document], as_of: date) -> Answer:
    records, duplicate_issues = unique_invoices(documents)
    issues = accounting_issues(documents) + duplicate_issues
    claims: list[Claim] = []
    sources: list[Source] = []
    rows: list[CalculationRow] = []
    for document, invoice in records:
        if invoice.kind != "invoice":
            continue
        evidence = sources_for_invoice(document, invoice)
        sources.extend(evidence)
        ids = [source.id for source in evidence]
        label = f"{invoice.number.value} · {invoice.vendor.value}"
        balance = (
            decimal_value(invoice.balance_due.value)
            if invoice.balance_due.value is not None
            else None
        )
        if invoice.payment_status.value == "paid" and balance is not None and balance > 0:
            text = f"{label}: conflicting payment evidence (marked paid with an outstanding balance); overdue payment cannot be established."
        elif invoice.payment_status.value == "paid" or (balance is not None and balance <= 0):
            text = f"{label}: paid / no outstanding balance."
        elif invoice.due_date.value is None:
            text = f"{label}: cannot determine overdue status; no verified due date."
        elif date.fromisoformat(invoice.due_date.value) >= as_of:
            text = f"{label}: not overdue as of {as_of}; due {invoice.due_date.value}."
        elif (
            balance is not None
            and balance > 0
            or invoice.payment_status.value in {"unpaid", "partial"}
        ):
            text = f"{label}: confirmed overdue; due {invoice.due_date.value}."
            if balance is not None and invoice.currency.value:
                rows.append(
                    CalculationRow(
                        label=label + " · outstanding",
                        amount=str(balance),
                        currency=invoice.currency.value,
                        source_ids=ids,
                    )
                )
        else:
            text = f"{label}: past its due date ({invoice.due_date.value}), but payment status is unknown; overdue payment is unconfirmed."
        claims.append(Claim(text=text, source_ids=ids))
    return Answer(
        status="answered" if claims else "insufficient_information",
        title=f"Payment review · {as_of.isoformat()}",
        claims=claims,
        sources=list({source.id: source for source in sources}.values()),
        calculations=rows,
        issues=issues,
        as_of=as_of.isoformat(),
        coverage="all_accounting_records",
    )


def invoice_details(documents: list[Document], as_of: date) -> Answer:
    claims: list[Claim] = []
    sources: list[Source] = []
    for document in documents:
        if document.accounting is None:
            continue
        for invoice in document.accounting.invoices:
            evidence = sources_for_invoice(document, invoice)
            if not evidence:
                continue
            sources.extend(evidence)
            text = f"{document.name}: {invoice.kind.replace('_', ' ')} {invoice.number.value or '(number unresolved)'}; vendor {invoice.vendor.value or '(unresolved)'}; issued {invoice.issue_date.value or '(date unresolved)'}; total {invoice.currency.value or '(currency unresolved)'} {invoice.total.value or '(amount unresolved)'}."
            claims.append(Claim(text=text, source_ids=[source.id for source in evidence]))
    return Answer(
        status="answered" if claims else "insufficient_information",
        title="Invoice details",
        claims=claims,
        sources=list({source.id: source for source in sources}.values()),
        calculations=[],
        issues=accounting_issues(documents)
        + ([] if claims else ["No verified invoice records were extracted from this selection."]),
        as_of=as_of.isoformat(),
        coverage="all_accounting_records",
    )


def statement_summary(documents: list[Document], as_of: date) -> Answer:
    statements = [
        document
        for document in documents
        if document.accounting and document.accounting.document_type in {"statement", "mixed"}
    ]
    if len(statements) != 1:
        return Answer(
            status="clarification_needed",
            title="Select one statement",
            claims=[],
            sources=[],
            calculations=[],
            issues=[
                "Select exactly one statement to avoid double-counting overlapping statement periods. Invoice documents can remain selected."
            ],
            as_of=as_of.isoformat(),
            coverage="all_accounting_records",
        )
    document = statements[0]
    assert document.accounting is not None
    issues = accounting_issues([document])
    rows: list[CalculationRow] = []
    sources: list[Source] = []
    totals: dict[str, Decimal] = defaultdict(Decimal)
    excluded = 0
    for transaction in document.accounting.transactions:
        if (
            transaction.category != "expense"
            or transaction.direction != "debit"
            or transaction.amount.value is None
            or transaction.currency.value is None
            or transaction.description.value is None
        ):
            excluded += 1
            continue
        evidence = [
            fact_source(document, fact)
            for fact in (
                transaction.description,
                transaction.amount,
                transaction.currency,
                transaction.date,
            )
        ]
        verified = [source for source in evidence if source is not None]
        sources.extend(verified)
        amount = abs(decimal_value(transaction.amount.value))
        totals[transaction.currency.value] += amount
        rows.append(
            CalculationRow(
                label=transaction.description.value,
                amount=str(amount),
                currency=transaction.currency.value,
                source_ids=[source.id for source in verified],
            )
        )
    if excluded:
        issues.append(
            f"{excluded} non-expense, ambiguous, or incomplete rows excluded; transfers, deposits and balances are not treated as expenses."
        )
    claims = [
        Claim(
            text=f"{'Verified expense subtotal' if not document.accounting.complete else 'Identified expenses'}: {currency} {format_amount(total)} across {sum(row.currency == currency for row in rows)} expense entries.",
            source_ids=[
                source.id
                for source in sources
                if any(source.id in row.source_ids and row.currency == currency for row in rows)
            ],
        )
        for currency, total in sorted(totals.items())
    ]
    return Answer(
        status="answered" if claims else "insufficient_information",
        title="Statement expense summary",
        claims=claims,
        sources=list({source.id: source for source in sources}.values()),
        calculations=rows,
        issues=issues,
        as_of=as_of.isoformat(),
        coverage="all_accounting_records",
    )
