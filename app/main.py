import asyncio
import hashlib
import secrets
import shutil
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import APIStatusError, OpenAIError

from app.answers import answer_question
from app.config import Settings, load_settings
from app.errors import AppError
from app.extract import media_type
from app.ingestion import ingestion_worker
from app.models import Answer, ChatRequest, Document, ErrorBody, Page
from app.provider import Provider
from app.storage import Store


def create_app(settings: Settings) -> FastAPI:
    """Compose external connectors once; application logic stays in small functions."""
    store = Store(settings)
    provider = Provider(settings)
    worker: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        nonlocal worker
        store.initialize()
        worker = asyncio.create_task(ingestion_worker(store, provider, settings))
        try:
            yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            await provider.close()

    app = FastAPI(title="LedgerLens API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def identify(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def application_error(request: Request, error: AppError) -> JSONResponse:
        body = ErrorBody(
            code=error.code, message=error.message, request_id=request.state.request_id
        )
        return JSONResponse(status_code=error.status, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        issues = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        return JSONResponse(
            status_code=422,
            content=ErrorBody(
                code="invalid_request", message=issues, request_id=request.state.request_id
            ).model_dump(),
        )

    @app.exception_handler(OpenAIError)
    async def provider_error(request: Request, error: OpenAIError) -> JSONResponse:
        status = error.status_code if isinstance(error, APIStatusError) else None
        provider_request_id = error.request_id if isinstance(error, APIStatusError) else None
        structlog.get_logger().error(
            "provider_failed",
            model=settings.chat_model,
            status=status,
            provider_request_id=provider_request_id,
            detail=str(error),
        )
        message = f"Model request failed ({type(error).__name__}, status={status}, provider request={provider_request_id}). Check the backend API key, model access and billing. {error}"
        return JSONResponse(
            status_code=502,
            content=ErrorBody(
                code="provider_failed", message=message, request_id=request.state.request_id
            ).model_dump(),
        )

    async def authorize(request: Request) -> None:
        authorization = request.headers.get("Authorization", "")
        supplied = authorization.removeprefix("Bearer ")
        if not authorization.startswith("Bearer ") or not secrets.compare_digest(
            supplied, settings.reviewer_key.get_secret_value()
        ):
            raise AppError(
                "unauthorized", "Enter the reviewer access key to open this workspace.", 401
            )

    router = APIRouter(prefix="/api", dependencies=[Depends(authorize)])

    @router.get("/status")
    async def status() -> dict[str, str | bool | int]:
        return {
            "model": settings.chat_model,
            "model_configured": bool(settings.openai_api_key.get_secret_value()),
            "ocr_installed": shutil.which("tesseract") is not None,
            "max_file_bytes": settings.max_file_bytes,
            "max_pages": settings.max_pages,
        }

    @router.get("/documents")
    async def documents() -> list[Document]:
        return store.list_documents()

    @router.post("/documents", status_code=202)
    async def upload(file: Annotated[UploadFile, File()]) -> Document:
        provider.require_key()
        content = await file.read(settings.max_file_bytes + 1)
        await file.close()
        if not content:
            raise AppError("empty_file", "The uploaded file is empty.", 422)
        if len(content) > settings.max_file_bytes:
            raise AppError("file_too_large", "Upload a file smaller than 10 MB.", 413)
        kind = media_type(content)
        name = Path((file.filename or "document").replace("\\", "/")).name[:180]
        expected = {
            "application/pdf": {".pdf"},
            "image/png": {".png"},
            "image/jpeg": {".jpg", ".jpeg"},
        }
        if Path(name).suffix.lower() not in expected[kind]:
            raise AppError(
                "file_type_mismatch",
                "The filename extension does not match its contents. Use .pdf, .png, .jpg or .jpeg as appropriate.",
                422,
            )
        digest = hashlib.sha256(content).hexdigest()
        duplicate = store.find_hash(digest)
        if duplicate:
            raise AppError(
                "duplicate_document",
                f"This exact file is already uploaded as {duplicate.name} ({duplicate.id}).",
                409,
            )
        try:
            return store.add_document(uuid4().hex, name, kind, content, digest)
        except sqlite3.IntegrityError as error:
            raise AppError(
                "duplicate_document",
                "Another upload of this file was just accepted. Refresh the document list.",
                409,
            ) from error

    @router.get("/documents/{document_id}")
    async def document(document_id: str) -> Document:
        return store.get_document(document_id)

    @router.get("/documents/{document_id}/pages")
    async def pages(document_id: str) -> list[Page]:
        store.get_document(document_id)
        return store.pages(document_id)

    @router.get("/documents/{document_id}/file")
    async def original(document_id: str) -> FileResponse:
        item = store.get_document(document_id)
        return FileResponse(
            store.file_path(item.id),
            media_type=item.media_type,
            filename=item.name,
            content_disposition_type="inline",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/documents/{document_id}/retry")
    async def retry(document_id: str) -> Document:
        provider.require_key()
        return store.retry(document_id)

    @router.delete("/documents/{document_id}", status_code=204)
    async def delete(document_id: str) -> Response:
        store.delete(document_id)
        return Response(status_code=204)

    @router.post("/chat")
    async def chat(body: ChatRequest) -> Answer:
        return await answer_question(body, store, provider, settings)

    @app.get("/health")
    async def health() -> dict[str, str]:
        if worker is None or worker.done():
            raise AppError(
                "worker_unavailable",
                "The ingestion worker is not running. Check backend logs.",
                503,
            )
        return {"status": "ok"}

    app.include_router(router)
    frontend = Path(__file__).resolve().parent.parent / "web"

    @app.get("/config.js")
    async def frontend_config() -> Response:
        return Response("export const API_URL = '';", media_type="text/javascript")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(frontend / "index.html")

    app.mount("/assets", StaticFiles(directory=frontend), name="assets")
    app.mount("/samples", StaticFiles(directory=frontend.parent / "samples"), name="samples")
    return app


def create_application() -> FastAPI:
    return create_app(load_settings())
