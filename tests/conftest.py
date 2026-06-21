"""Shared fixtures: a tiny on-disk repo and an indexed copy of it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # make `support` importable

from support import make_settings, reindex  # noqa: E402

CALCULATOR = '''\
"""Calculator utilities for arithmetic."""


class Calculator:
    """A simple calculator."""

    def add(self, a, b):
        """Add two numbers and return the sum."""
        return a + b

    def subtract(self, a, b):
        return a - b


def multiply(a, b):
    """Multiply two numbers together."""
    return a * b
'''

EMAIL = '''\
"""Email sending service for vendor notifications."""


def send_vendor_email(vendor, body):
    """Send an email to a vendor after purchase order approval."""
    return f"sent to {vendor}: {body}"
'''

TEST_FILE = '''\
from pkg.calculator import Calculator


def test_add():
    assert Calculator().add(1, 2) == 3
'''

README = "# Sample\n\nThis fixture repo exercises indexing of vendor email and arithmetic.\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "calculator.py").write_text(CALCULATOR, encoding="utf-8")
    (tmp_path / "pkg" / "email_service.py").write_text(EMAIL, encoding="utf-8")
    (tmp_path / "tests" / "test_calculator.py").write_text(TEST_FILE, encoding="utf-8")
    (tmp_path / "README.md").write_text(README, encoding="utf-8")
    return tmp_path


@pytest.fixture
def settings(repo: Path):
    return make_settings(repo)


@pytest.fixture
def indexed(settings):
    reindex(settings, incremental=False)
    return settings
