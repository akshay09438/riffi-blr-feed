from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(autouse=True)
def never_the_real_database(tmp_path, monkeypatch):
    """A test that forgets --db must never write into data/engine.db: it holds the two-week test's evidence."""
    monkeypatch.setenv("RIFFI_DB_PATH", str(tmp_path / "default-engine.db"))
