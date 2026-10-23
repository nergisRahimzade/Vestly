"""Synthetic suite for the run_tests tool: two passes and one skip. Never collected by the main run."""

import pytest


def test_passes():
    assert 1 + 1 == 2


def test_also_passes():
    assert "vestly".upper() == "VESTLY"


@pytest.mark.skip(reason="planted skip")
def test_skipped():
    pass
