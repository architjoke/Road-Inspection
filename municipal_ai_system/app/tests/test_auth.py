"""Auth tests require DB — skipped offline."""

import pytest


@pytest.mark.skip(reason="Requires offline-safe auth fixtures / DB")
def test_auth_placeholder():
    assert True
