"""Shared fixtures."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import catalog  # noqa: E402
from core.database import QueryEngine  # noqa: E402


@pytest.fixture(scope="session")
def datasets():
    return catalog.load_builtin_datasets()


@pytest.fixture(scope="session")
def engines(datasets):
    return {k: QueryEngine(v, max_rows=1_000, timeout_seconds=5) for k, v in datasets.items()}
