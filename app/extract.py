import argparse
import json
import sys
from pathlib import Path

import pymupdf

from app.errors import ExtractionError
from app.models import Page, TextBlock


def media_type(content: bytes) -> str:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise ExtractionError(
        "Unsupported file. Upload a PDF, PNG, or JPEG with a valid file signature."
    )


def extract_pages(path: Path, max_pages: int) -> list[Page]:
    """Extract in a dedicated process; OCR image regions, keeping native text."""
    content = path.read_bytes()
    kind = media_type(content)
    filetype = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpeg"}[kind]
    try:
        original = pymupdf.open(stream=content, filetype=filetype)
        document = (
            original
            if kind == "application/pdf"
            else pymupdf.open(stream=original.convert_to_pdf(), filetype="pdf")
        )
        try:
            if document.needs_pass:
                raise ExtractionError(
                    "Password-protected PDFs are unsupported. Upload an unlocked copy."
                )
            if not 1 <= document.page_count <= max_pages:
                raise ExtractionError(f"Upload a document containing 1–{max_pages} pages.")
            pages: list[Page] = []
            for index in range(document.page_count):
                native = document.load_page(index)
                native_text = native.get_text("text", sort=True)
                needs_ocr = bool(native.get_images()) or len(native_text.strip()) < 30
                textpage = (
                    native.get_textpage_ocr(language="eng", dpi=300, full=not native_text.strip())
                    if needs_ocr
                    else native.get_textpage()
                )
                text = native.get_text("text", textpage=textpage, sort=True)
                blocks = [
                    TextBlock(text=block[4], bbox=(block[0], block[1], block[2], block[3]))
                    for block in native.get_text("blocks", textpage=textpage, sort=True)
                    if block[6] == 0 and block[4].strip()
                ]
                pages.append(
                    Page(number=native.number + 1, text=text, ocr=needs_ocr, blocks=blocks)
                )
            if not any(page.text.strip() for page in pages):
                raise ExtractionError(
                    "No readable text was found, including after OCR. Upload a clearer printed document."
                )
            return pages
        finally:
            if document is not original:
                document.close()
            original.close()
    except (pymupdf.FileDataError, RuntimeError, ValueError) as error:
        raise ExtractionError(
            f"PDF/image extraction failed: {error}. Check the file and Tesseract English language installation."
        ) from error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("max_pages", type=int)
    arguments = parser.parse_args()
    try:
        pages = extract_pages(arguments.path, arguments.max_pages)
    except (ExtractionError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps([page.model_dump() for page in pages]))


if __name__ == "__main__":
    main()
