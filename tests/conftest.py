import os
from pathlib import Path

import pytest

from app.config.settings import Settings


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set (see README: Testing)")
    return url


@pytest.fixture
def make_settings(tmp_path: Path):
    def build(url: str = "postgresql://u:p@localhost/db", **overrides) -> Settings:
        values = dict(database_url=url, generated_dir=tmp_path / "generated", logs_dir=tmp_path / "logs",
                      target_schema="agent_test_shop", _env_file=None)
        values.update(overrides)
        return Settings(**values)
    return build
