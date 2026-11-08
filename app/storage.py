import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import sqlite_vec

from app.config import Settings
from app.errors import AppError
from app.models import AccountingData, Chunk, Document, Page


class Store:
    """SQLite connector: every operation owns its connection and transaction."""

    def __init__(self, settings: Settings) -> None:
        self.directory = settings.data_dir.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "files").mkdir(exist_ok=True)
        self.database = self.directory / "ledger.sqlite3"
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if not hasattr(connection, "enable_load_extension"):
            connection.close()
            raise AppError(
                "sqlite_extensions_unavailable",
                "This Python build cannot load SQLite extensions. Use the Docker image or a Python distribution with SQLite extension support.",
                503,
            )
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection, connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS configuration (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, media_type TEXT NOT NULL,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL,
                    page_count INTEGER NOT NULL, ocr_pages INTEGER NOT NULL,
                    error TEXT, accounting TEXT
                );
                CREATE TABLE IF NOT EXISTS pages (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(document_id, number)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    page INTEGER NOT NULL, text TEXT NOT NULL, embedding BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_search USING fts5(id UNINDEXED, text);
            """)
            configuration = {
                "schema": "1",
                "embedding_model": self.model,
                "embedding_dimensions": str(self.dimensions),
            }
            for key, value in configuration.items():
                row = connection.execute(
                    "SELECT value FROM configuration WHERE key=?", (key,)
                ).fetchone()
                if row is not None and row["value"] != value:
                    raise AppError(
                        "index_configuration_changed",
                        f"Stored {key} differs. Restore the original configuration or explicitly rebuild the data directory.",
                        503,
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO configuration VALUES (?, ?)", (key, value)
                )
            connection.execute(
                "UPDATE documents SET status='failed', stage='Interrupted', error='Processing was interrupted by a restart. Retry this document.' WHERE status='processing'"
            )

    def file_path(self, document_id: str) -> Path:
        return self.directory / "files" / document_id

    def list_documents(self) -> list[Document]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
            return [decode_document(row) for row in rows]

    def get_document(self, document_id: str) -> Document:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if row is None:
                raise AppError(
                    "document_not_found",
                    "This document no longer exists. Refresh the document list.",
                    404,
                )
            return decode_document(row)

    def find_hash(self, sha256: str) -> Document | None:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM documents WHERE sha256=?", (sha256,)).fetchone()
            return None if row is None else decode_document(row)

    def add_document(
        self, document_id: str, name: str, media_type: str, content: bytes, sha256: str
    ) -> Document:
        path = self.file_path(document_id)
        path.write_bytes(content)
        try:
            with closing(self.connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO documents VALUES (?,?,?,?,?,'queued','Waiting',?,0,0,NULL,NULL)",
                    (
                        document_id,
                        name,
                        media_type,
                        len(content),
                        sha256,
                        datetime.now(UTC).isoformat(),
                    ),
                )
        except sqlite3.Error:
            path.unlink()
            raise
        return self.get_document(document_id)

    def set_stage(self, document_id: str, stage: str) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute("UPDATE documents SET stage=? WHERE id=?", (stage, document_id))

    def claim_next(self) -> Document | None:
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM documents WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE documents SET status='processing', stage='Extracting pages', error=NULL WHERE id=?",
                (row["id"],),
            )
            return decode_document(row)

    def fail(self, document_id: str, message: str) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE documents SET status='failed', stage='Needs attention', error=? WHERE id=?",
                (message, document_id),
            )

    def retry(self, document_id: str) -> Document:
        document = self.get_document(document_id)
        if document.status != "failed":
            raise AppError("retry_not_allowed", "Only failed documents can be retried.", 409)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE documents SET status='queued', stage='Waiting', error=NULL WHERE id=? AND status='failed'",
                (document_id,),
            )
        return self.get_document(document_id)

    def commit_ingestion(
        self,
        document_id: str,
        pages: list[Page],
        chunks: list[Chunk],
        embeddings: list[list[float]],
        accounting: AccountingData,
    ) -> None:
        if len(chunks) != len(embeddings) or any(
            len(vector) != self.dimensions for vector in embeddings
        ):
            raise AppError(
                "embedding_shape",
                "The provider returned an unexpected embedding count or dimension.",
                502,
            )
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "DELETE FROM chunk_search WHERE id IN (SELECT id FROM chunks WHERE document_id=?)",
                (document_id,),
            )
            connection.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            connection.execute("DELETE FROM pages WHERE document_id=?", (document_id,))
            connection.executemany(
                "INSERT INTO pages VALUES (?,?,?)",
                [(document_id, page.number, page.model_dump_json()) for page in pages],
            )
            for chunk, vector in zip(chunks, embeddings, strict=True):
                connection.execute(
                    "INSERT INTO chunks VALUES (?,?,?,?,?)",
                    (
                        chunk.id,
                        document_id,
                        chunk.page,
                        chunk.text,
                        sqlite_vec.serialize_float32(vector),
                    ),
                )
                connection.execute("INSERT INTO chunk_search VALUES (?,?)", (chunk.id, chunk.text))
            connection.execute(
                "UPDATE documents SET status='ready', stage='Ready', page_count=?, ocr_pages=?, accounting=?, error=NULL WHERE id=?",
                (
                    len(pages),
                    sum(page.ocr for page in pages),
                    accounting.model_dump_json(),
                    document_id,
                ),
            )

    def pages(self, document_id: str) -> list[Page]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT payload FROM pages WHERE document_id=? ORDER BY number", (document_id,)
            ).fetchall()
            return [Page.model_validate_json(row["payload"]) for row in rows]

    def semantic_search(
        self, vector: list[float], document_ids: list[str], limit: int
    ) -> list[Chunk]:
        placeholders = ",".join("?" for _ in document_ids)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"SELECT id,document_id,page,text FROM chunks WHERE document_id IN ({placeholders}) ORDER BY vec_distance_cosine(embedding,?) LIMIT ?",
                (*document_ids, sqlite_vec.serialize_float32(vector), limit),
            ).fetchall()
            return [decode_chunk(row) for row in rows]

    def keyword_search(self, query: str, document_ids: list[str], limit: int) -> list[Chunk]:
        placeholders = ",".join("?" for _ in document_ids)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"SELECT c.id,c.document_id,c.page,c.text FROM chunk_search JOIN chunks c ON c.id=chunk_search.id WHERE chunk_search MATCH ? AND c.document_id IN ({placeholders}) ORDER BY rank LIMIT ?",
                (query, *document_ids, limit),
            ).fetchall()
            return [decode_chunk(row) for row in rows]

    def delete(self, document_id: str) -> None:
        document = self.get_document(document_id)
        if document.status == "processing":
            raise AppError(
                "document_busy", "Wait for processing to finish before deleting this document.", 409
            )
        path = self.file_path(document_id)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "DELETE FROM chunk_search WHERE id IN (SELECT id FROM chunks WHERE document_id=?)",
                (document_id,),
            )
            connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
        try:
            path.unlink()
        except OSError as error:
            raise AppError(
                "file_cleanup_failed",
                f"Index entries were removed, but the original could not be deleted: {path}. Remove this file manually. {error}",
                500,
            ) from error


def decode_document(row: sqlite3.Row) -> Document:
    values = dict(row)
    values["accounting"] = None if row["accounting"] is None else json.loads(row["accounting"])
    return Document.model_validate(values)


def decode_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        id=cast(str, row["id"]),
        document_id=cast(str, row["document_id"]),
        page=cast(int, row["page"]),
        text=cast(str, row["text"]),
    )
