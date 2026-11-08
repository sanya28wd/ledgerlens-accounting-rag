import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from app.models import AccountingData, Evidence, Fact, Invoice, Page, Transaction


def normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def excerpt_exists(evidence: Evidence, pages: list[Page]) -> bool:
    return bool(evidence.excerpt.strip()) and any(
        page.number == evidence.page and normalized(evidence.excerpt) in normalized(page.text)
        for page in pages
    )


def decimal_value(value: str) -> Decimal:
    if not re.fullmatch(r"-?\d+(?:\.\d{1,4})?", value):
        raise ValueError(f"Invalid monetary decimal: {value!r}")
    amount = Decimal(value)
    if not amount.is_finite():
        raise ValueError("Monetary values must be finite.")
    return amount


def money_in_excerpt(value: str, excerpt: str) -> bool:
    amount = decimal_value(value)
    candidates = re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w.])", excerpt)
    return any(Decimal(candidate.replace(",", "")) == amount for candidate in candidates)


def date_in_excerpt(value: str, excerpt: str) -> bool:
    expected = datetime.strptime(value, "%Y-%m-%d").date()
    candidates = re.findall(
        r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{1,2} [A-Za-z]+ \d{4}|[A-Za-z]+ \d{1,2},? \d{4})\b",
        excerpt,
    )
    formats = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%B %d %Y",
    )
    for candidate in candidates:
        parsed = set()
        for pattern in formats:
            try:
                parsed.add(datetime.strptime(candidate, pattern).date())
            except ValueError:
                continue
        if parsed == {expected}:
            return True
    return False


def validate_fact(name: str, fact: Fact, pages: list[Page]) -> tuple[Fact, str | None]:
    if fact.value is None:
        return Fact(value=None, evidence=None), None
    valid = (
        bool(fact.value.strip())
        and fact.evidence is not None
        and excerpt_exists(fact.evidence, pages)
    )
    if valid and fact.evidence is not None:
        try:
            if name in {"subtotal", "tax", "total", "amount_paid", "balance_due", "amount"}:
                valid = money_in_excerpt(fact.value, fact.evidence.excerpt)
                labels = {
                    "subtotal": r"\bsub\s*total\b",
                    "tax": r"\b(?:tax|vat|gst)\b",
                    "total": r"\b(?:grand\s+total|total(?:\s+amount)?)\b",
                    "amount_paid": r"\b(?:paid|payments?)\b",
                    "balance_due": r"\b(?:balance|outstanding|amount\s+due)\b",
                }
                if name in labels:
                    valid = valid and bool(
                        re.search(labels[name], fact.evidence.excerpt, re.IGNORECASE)
                    )
            elif name in {"issue_date", "due_date", "date"}:
                valid = date_in_excerpt(fact.value, fact.evidence.excerpt)
            elif name == "currency":
                valid = bool(re.fullmatch(r"[A-Z]{3}", fact.value)) and bool(
                    re.search(rf"\b{re.escape(fact.value)}\b", fact.evidence.excerpt, re.IGNORECASE)
                )
            elif name == "payment_status":
                valid = fact.value in {"paid", "unpaid", "partial"} and bool(
                    re.search(rf"\b{re.escape(fact.value)}\b", fact.evidence.excerpt, re.IGNORECASE)
                )
            else:
                valid = normalized(fact.value) in normalized(fact.evidence.excerpt)
        except (ValueError, InvalidOperation):
            valid = False
    if not valid:
        return Fact(
            value=None, evidence=None
        ), f"{name}: extracted value could not be verified against its cited excerpt."
    return fact, None


def validate_accounting(data: AccountingData, pages: list[Page]) -> AccountingData:
    """Discard unsupported fields, not useful searchable document text."""
    issues = list(data.issues)
    invoices: list[Invoice] = []
    transactions: list[Transaction] = []
    for invoice in data.invoices:
        fields: dict[str, Fact] = {}
        for name in Invoice.model_fields:
            if name == "kind":
                continue
            fact, issue = validate_fact(name, cast(Fact, getattr(invoice, name)), pages)
            fields[name] = fact
            if issue:
                issues.append(issue)
        checked = Invoice.model_validate({"kind": invoice.kind, **fields})
        missing = [
            name for name in ("number", "vendor", "currency", "total") if fields[name].value is None
        ]
        if missing:
            issues.append(f"Invoice has unresolved required fields: {', '.join(missing)}.")
        if (
            checked.total.value is not None
            and checked.subtotal.value is not None
            and checked.tax.value is not None
        ):
            if decimal_value(checked.subtotal.value) + decimal_value(
                checked.tax.value
            ) != decimal_value(checked.total.value):
                issues.append(
                    f"Invoice {checked.number.value}: subtotal plus tax differs from total; verify discounts, charges, or extraction."
                )
        invoices.append(checked)
    for transaction in data.transactions:
        fields = {}
        for name in ("date", "description", "amount", "currency"):
            fact, issue = validate_fact(name, cast(Fact, getattr(transaction, name)), pages)
            fields[name] = fact
            if issue:
                issues.append(issue)
        if any(fields[name].value is None for name in ("description", "amount", "currency")):
            issues.append(
                "A statement transaction has unresolved description, amount, or currency."
            )
        transactions.append(
            Transaction.model_validate(
                {**fields, "category": transaction.category, "direction": transaction.direction}
            )
        )
    if data.document_type in {"invoice", "mixed"} and not invoices:
        issues.append("Invoice content was identified but no invoice records were extracted.")
    if data.document_type in {"statement", "mixed"} and not transactions:
        issues.append("Statement content was identified but no transactions were extracted.")
    return AccountingData(
        document_type=data.document_type,
        invoices=invoices,
        transactions=transactions,
        complete=data.complete and not issues,
        issues=list(dict.fromkeys(issues)),
    )
