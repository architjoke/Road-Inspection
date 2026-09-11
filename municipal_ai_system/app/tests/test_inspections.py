"""Inspection route tests require DB — skipped offline."""

import pytest


@pytest.mark.skip(reason="Requires DB-backed inspection fixtures")
def test_inspections_placeholder():
    assert True
