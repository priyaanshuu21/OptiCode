"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REPO_ROOT = BACKEND_ROOT.parent
#: The curated corpus, at the repository root rather than inside the package: it is something a
#: person browses and a client downloads, not an implementation detail. ``valid/`` holds the
#: programs that compile, ``invalid/`` the ones that are meant to fail.
EXAMPLES_DIR = REPO_ROOT / "examples"
VALID_EXAMPLES_DIR = EXAMPLES_DIR / "valid"
INVALID_EXAMPLES_DIR = EXAMPLES_DIR / "invalid"


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return VALID_EXAMPLES_DIR


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from opticode.api.main import app

    with TestClient(app) as test_client:
        yield test_client
