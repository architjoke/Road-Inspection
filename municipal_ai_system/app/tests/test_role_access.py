"""Role access tests require DB — skipped offline."""

import pytest


@pytest.mark.skip(reason="Requires DB-backed role fixtures")
def test_role_access_placeholder():
    assert True
