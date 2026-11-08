from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextBlock(Model):
    text: str
    bbox: tuple[float, float, float, float]


class Page(Model):
    number: int
    text: str
    ocr: bool
    blocks: list[TextBlock]


class Chunk(Model):
    id: str
    document_id: str
    page: int
    text: str


class Evidence(Model):
    page: int
    excerpt: str


class Fact(Model):
    value: str | None
    evidence: Evidence | None


class Invoice(Model):
    kind: Literal["invoice", "credit_note"]
    number: Fact
    vendor: Fact
    issue_date: Fact
    due_date: Fact
    currency: Fact
    subtotal: Fact
    tax: Fact
    total: Fact
    amount_paid: Fact
    balance_due: Fact
    payment_status: Fact


class Transaction(Model):
    date: Fact
    description: Fact
    amount: Fact
    currency: Fact
    category: Literal["expense", "transfer", "deposit", "balance", "unknown"]
    direction: Literal["debit", "credit", "unknown"]


class AccountingData(Model):
    document_type: Literal["invoice", "statement", "mixed", "other"]
    invoices: list[Invoice]
    transactions: list[Transaction]
    complete: bool
    issues: list[str]


class Document(Model):
    id: str
    name: str
    media_type: str
    size: int
    sha256: str
    status: Literal["queued", "processing", "ready", "failed"]
    stage: str
    created_at: str
    page_count: int
    ocr_pages: int
    error: str | None
    accounting: AccountingData | None


class Source(Model):
    id: str
    document_id: str
    document_name: str
    page: int
    excerpt: str


class Claim(Model):
    text: str
    source_ids: list[str]


class CalculationRow(Model):
    label: str
    amount: str
    currency: str
    source_ids: list[str]


class Answer(Model):
    status: Literal["answered", "insufficient_information", "clarification_needed"]
    title: str
    claims: list[Claim]
    sources: list[Source]
    calculations: list[CalculationRow]
    issues: list[str]
    as_of: str
    coverage: Literal["retrieved_passages", "full_selected_documents", "all_accounting_records"]


class GeneratedAnswer(Model):
    status: Literal["answered", "insufficient_information", "clarification_needed"]
    title: str
    claims: list[Claim]
    issues: list[str]


class QuestionPlan(Model):
    intent: Literal["general", "invoice_details", "vendor_total", "overdue", "statement"]
    standalone_question: str
    vendor: str | None
    explicit_as_of: str | None


class Message(Model):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=6000)


class ChatRequest(Model):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] = Field(min_length=1, max_length=100)
    history: list[Message] = Field(default_factory=list, max_length=8)
    as_of: date | None = None


class ErrorBody(Model):
    code: str
    message: str
    request_id: str
