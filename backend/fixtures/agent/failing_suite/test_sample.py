"""Synthetic suite for the run_tests tool: one pass, one failure, one skip. Never collected by the main run."""

import pytest


def test_passes():
    assert 1 + 1 == 2


def test_fails():
    assert 2 + 2 == 5, "planted failure"


@pytest.mark.skip(reason="planted skip")
def test_skipped():
    pass
