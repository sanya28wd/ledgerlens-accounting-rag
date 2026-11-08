import asyncio
from collections.abc import Awaitable, Callable
from functools import partial

import structlog
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from app.config import Settings
from app.errors import AppError, ProviderError


async def retry_request[R](
    operation: Callable[[], Awaitable[R]], operation_name: str, model: str
) -> R:
    for attempt in range(3):
        try:
            return await operation()
        except (APIConnectionError, APITimeoutError, RateLimitError) as error:
            structlog.get_logger().warning(
                "provider_retry",
                operation=operation_name,
                model=model,
                attempt=attempt + 1,
                error_type=type(error).__name__,
            )
            if attempt == 2:
                raise
            await asyncio.sleep(2**attempt)
        except APIStatusError as error:
            if error.status_code < 500 or attempt == 2:
                raise
            structlog.get_logger().warning(
                "provider_retry",
                operation=operation_name,
                model=model,
                attempt=attempt + 1,
                status=error.status_code,
                request_id=error.request_id,
            )
            await asyncio.sleep(2**attempt)
    raise ProviderError(f"Retry loop ended unexpectedly for {operation_name}.")


class Provider:
    """OpenAI connector with bounded, observable retries and typed responses."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value() or "not-configured",
            max_retries=0,
            timeout=60,
        )

    def require_key(self) -> None:
        if not self.settings.openai_api_key.get_secret_value():
            raise AppError(
                "api_key_missing",
                "Set OPENAI_API_KEY in the backend .env file, then restart to process documents and ask questions.",
                503,
            )

    async def structured[T: BaseModel](
        self, schema: type[T], instructions: str, content: str, output_tokens: int
    ) -> T:
        self.require_key()

        async def request() -> T:
            response = await self.client.responses.parse(
                model=self.settings.chat_model,
                input=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": content},
                ],
                text_format=schema,
                max_output_tokens=output_tokens,
                store=False,
            )
            if response.status != "completed" or response.output_parsed is None:
                raise ProviderError(
                    f"Model {self.settings.chat_model} returned status {response.status} without a complete {schema.__name__}. Reduce document size or increase the configured output budget; no partial output was accepted."
                )
            structlog.get_logger().info(
                "model_completed",
                model=self.settings.chat_model,
                operation=schema.__name__,
                input_tokens=response.usage.input_tokens if response.usage else None,
                output_tokens=response.usage.output_tokens if response.usage else None,
            )
            return response.output_parsed

        return await retry_request(request, schema.__name__, self.settings.chat_model)

    async def embeddings(self, texts: list[str]) -> list[list[float]]:
        self.require_key()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 32):
            vectors.extend(
                await retry_request(
                    partial(self.embedding_batch, texts[start : start + 32]),
                    "embeddings",
                    self.settings.embedding_model,
                )
            )
        return vectors

    async def embedding_batch(self, texts: list[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=self.settings.embedding_model,
            input=texts,
            dimensions=self.settings.embedding_dimensions,
        )
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    async def close(self) -> None:
        await self.client.close()
