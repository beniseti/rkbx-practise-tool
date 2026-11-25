from pathlib import Path

import pytest

from rkbx_practice.config import SessionConfig
from rkbx_practice.library import InMemoryLibrary

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def library() -> InMemoryLibrary:
    return InMemoryLibrary.load_json(FIXTURES / "library.json")


@pytest.fixture
def session_cfg() -> SessionConfig:
    return SessionConfig(optimize_time_budget_s=0.2)
