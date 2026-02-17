"""Example test to demonstrate TDD approach and ensure the test suite works."""

import pytest

from trading_tools import __version__


def test_version() -> None:
    """Test that version is defined."""
    assert __version__ == "0.1.0"


def test_example_addition() -> None:
    """Example test case - replace with real tests."""
    assert 1 + 1 == 2


@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        (0, 0),
        (1, 1),
        (-1, -1),
    ],
)
def test_parametrized_example(input_value: int, expected: int) -> None:
    """Example parametrized test."""
    assert input_value == expected
