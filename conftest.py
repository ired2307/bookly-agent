import os
import sys

# Set a fake key so tests can import SupportAgent without EnvironmentError
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

sys.path.insert(0, os.path.dirname(__file__))

import pytest


@pytest.fixture(autouse=True)
def reset_shared_state():
    """Clear all in-memory write logs before and after every test."""
    from data import PENDING_REFUNDS, REFUNDS_INITIATED
    REFUNDS_INITIATED.clear()
    PENDING_REFUNDS.clear()
    yield
    REFUNDS_INITIATED.clear()
    PENDING_REFUNDS.clear()
