class AppError(Exception):
    """An actionable application failure safe to return to the reviewer."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ExtractionError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("extraction_failed", message, 422)


class EvidenceError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_evidence", message, 502)


class ProviderError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("provider_error", message, 502)
