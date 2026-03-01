"""Shared test configuration and fixtures."""

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest

_REQUIRED_ENV_VARS = {
    "REVOLUT_X_API_KEY": "test-api-key",
    "REVOLUT_X_PRIVATE_KEY_PATH": "/dev/null",
}


@pytest.fixture(autouse=True)
def _set_required_env_vars() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Provide dummy values for env vars required by settings.yaml.

    The default configuration references ``${REVOLUT_X_API_KEY}`` and
    ``${REVOLUT_X_PRIVATE_KEY_PATH}`` without defaults, so any test that
    triggers ``ConfigLoader`` against the real ``settings.yaml`` will fail
    in CI where those variables are not set.  This fixture injects harmless
    placeholders so the config loads cleanly everywhere.
    """
    missing = {k: v for k, v in _REQUIRED_ENV_VARS.items() if k not in os.environ}
    if not missing:
        yield
        return
    with patch.dict(os.environ, missing):
        yield
