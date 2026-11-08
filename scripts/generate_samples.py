import json
from pathlib import Path

import pymupdf
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas


def invoice_lines(
    number: str, vendor: str, total: str, currency: str, due: str, status: str, balance: str
) -> list[str]:
    return [
        f"Vendor: {vendor}",
        f"Invoice number: {number}",
        "Invoice date: 2026-09-15",
        f"Due date: {due}",
        f"Currency: {currency}",
        f"Description: Office supplies and services for {number}",
        f"Subtotal: {currency} {total}",
        f"Tax: {currency} 0.00",
        f"Total amount: {currency} {total}",
        f"Payment status: {status}",
        f"Balance due: {currency} {balance}",
        "Customer: Example Accounting LLC",
        "Synthetic fixture. Not a real invoice.",
    ]


def write_pdf(path: Path, title: str, pages: list[list[str]]) -> None:
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    width, height = A4
    for index, lines in enumerate(pages, 1):
        canvas.setFillColor(HexColor("#173f34"))
        canvas.rect(0, height - 130, width, 130, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#e7efde"))
        canvas.setFont("Helvetica", 10)
        canvas.drawString(48, height - 45, "LEDGERLENS / SYNTHETIC DEMONSTRATION DATA")
        canvas.setFont("Helvetica-Bold", 25)
        canvas.drawString(48, height - 90, title)
        canvas.setFillColor(HexColor("#253f35"))
        canvas.setFont("Helvetica", 11)
        for offset, line in enumerate(lines):
            canvas.drawString(48, height - 170 - offset * 25, line)
        canvas.setStrokeColor(HexColor("#dce4d4"))
        canvas.line(48, 65, width - 48, 65)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(
            48, 44, "For software evaluation only. All names and figures are fictional."
        )
        canvas.drawRightString(width - 48, 44, f"Page {index} of {len(pages)}")
        canvas.showPage()
    canvas.save()


def main() -> None:
    directory = Path("samples")
    directory.mkdir(exist_ok=True)
    cedar = invoice_lines(
        "CED-001", "Cedar Supplies", "1200.00", "AED", "2026-10-01", "unpaid", "1200.00"
    )
    write_pdf(directory / "cedar-001.pdf", "Invoice CED-001", [cedar])
    write_pdf(
        directory / "cedar-002.pdf",
        "Invoice CED-002",
        [
            invoice_lines(
                "CED-002", "Cedar Supplies", "850.00", "AED", "2026-11-15", "unpaid", "850.00"
            )
        ],
    )
    write_pdf(
        directory / "cedar-003.pdf",
        "Invoice CED-003",
        [invoice_lines("CED-003", "Cedar Supplies", "450.00", "AED", "2026-09-30", "paid", "0.00")],
    )
    write_pdf(
        directory / "nova-001.pdf",
        "Invoice NOV-001",
        [
            invoice_lines(
                "NOV-001", "Nova Studio", "100.00", "USD", "2026-10-10", "partial", "40.00"
            )
        ],
    )
    write_pdf(
        directory / "atlas-unknown.pdf",
        "Invoice ATL-001",
        [
            invoice_lines(
                "ATL-001",
                "Atlas Services",
                "900.00",
                "AED",
                "2026-10-15",
                "Not provided",
                "Not provided",
            )
        ],
    )
    statement = [
        "Statement: October 2026",
        "Account holder: Example Accounting LLC",
        "Currency: AED",
        "Period: 2026-10-01 to 2026-10-31",
        "Date | Description | Debit AED | Credit AED",
        "2026-10-02 | Utilities expense | 125.00 | 0.00",
        "2026-10-05 | Software expense | 75.00 | 0.00",
        "2026-10-08 | Office supplies expense | 200.00 | 0.00",
        "2026-10-12 | Internal account transfer | 500.00 | 0.00",
        "2026-10-20 | Customer deposit | 0.00 | 1000.00",
        "Opening balance: AED 1500.00",
        "Closing balance: AED 1600.00",
        "Synthetic bank statement. Not a real financial record.",
    ]
    write_pdf(directory / "october-statement.pdf", "October account statement", [statement])
    write_pdf(
        directory / "two-invoices.pdf",
        "Two invoices",
        [
            invoice_lines(
                "BUN-001", "Bundle Office", "50.00", "AED", "2026-10-01", "unpaid", "50.00"
            ),
            invoice_lines(
                "BUN-002", "Bundle Office", "75.00", "AED", "2026-10-02", "unpaid", "75.00"
            ),
        ],
    )
    write_pdf(
        directory / "multipage-invoice.pdf",
        "Invoice MULTI-001",
        [
            [
                "Vendor: Horizon Office",
                "Invoice number: MULTI-001",
                "Invoice date: 2026-09-15",
                "Line item 1: AED 60.00",
                "Continued on next page",
            ],
            [
                "Continuation of invoice MULTI-001",
                "Vendor: Horizon Office",
                "Due date: 2026-10-01",
                "Currency: AED",
                "Subtotal: AED 100.00",
                "Tax: AED 0.00",
                "Total amount: AED 100.00",
                "Payment status: unpaid",
                "Balance due: AED 100.00",
            ],
        ],
    )
    with pymupdf.open(directory / "cedar-001.pdf") as original:
        pixmap = original.load_page(0).get_pixmap(dpi=180)
        pixmap.save(directory / "cedar-scan.png")
        with pymupdf.open() as scanned:
            page = scanned.new_page(width=A4[0], height=A4[1])
            page.insert_image(page.rect, stream=pixmap.tobytes("png"))
            scanned.save(directory / "cedar-scan.pdf")
        with pymupdf.open(directory / "cedar-002.pdf") as mixed:
            page = mixed.new_page(width=A4[0], height=A4[1])
            page.insert_image(page.rect, stream=pixmap.tobytes("png"))
            mixed.save(directory / "mixed-pages.pdf")
    scale = directory / "scale"
    scale.mkdir(exist_ok=True)
    for index in range(1, 25):
        number = f"AUR-{index:03d}"
        write_pdf(
            scale / f"{number.lower()}.pdf",
            f"Invoice {number}",
            [
                invoice_lines(
                    number, "Aurora Paper", "10.25", "AED", "2026-10-01", "unpaid", "10.25"
                )
            ],
        )
    (directory / "expected.json").write_text(
        json.dumps(
            {
                "as_of": "2026-10-31",
                "cedar_total_aed": "2500.00",
                "statement_expenses_aed": "400.00",
                "bundle_total_aed": "125.00",
                "aurora_invoice_count": 24,
                "aurora_total_aed": "246.00",
                "confirmed_overdue": ["CED-001", "NOV-001"],
                "unknown_payment_status": ["ATL-001"],
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
