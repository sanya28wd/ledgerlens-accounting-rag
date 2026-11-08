from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.storage import Store


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        reviewer_key=SecretStr("integration-reviewer-secret"),
        openai_api_key=SecretStr(""),
        data_dir=tmp_path,
        embedding_dimensions=3,
    )


@pytest.fixture
def store(settings: Settings) -> Store:
    connector = Store(settings)
    connector.initialize()
    return connector
