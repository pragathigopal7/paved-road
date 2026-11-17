from pathlib import Path

import pytest

from paved import scaffold

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def generated(tmp_path):
    """A freshly scaffolded service on disk."""
    spec = scaffold.ServiceSpec(name="orders-consumer", team="payments", topic="orders")
    scaffold.write(scaffold.render(spec), tmp_path / "svc")
    return tmp_path / "svc"
