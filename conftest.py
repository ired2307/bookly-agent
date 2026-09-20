import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture(autouse=True)
def reset_shared_state():
    """Clear all in-memory write logs before and after every test."""
    from data import PENDING_REFUNDS, REFUNDS_INITIATED

    REFUNDS_INITIATED.clear()
    PENDING_REFUNDS.clear()
    yield
    REFUNDS_INITIATED.clear()
    PENDING_REFUNDS.clear()
