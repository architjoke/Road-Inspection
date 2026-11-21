"""Launcher / .env mode: real RDS URL must not be treated as unconfigured."""

from app.config import is_usable_database_url


def test_placeholder_database_url_rejected():
    assert not is_usable_database_url("")
    assert not is_usable_database_url(None)
    assert not is_usable_database_url(
        "postgresql+psycopg2://USERNAME:PASSWORD@YOUR-RDS-HOST:5432/postgres?sslmode=require"
    )
    assert not is_usable_database_url(
        "postgresql+psycopg2://USERNAME:PASSWORD@YOUR-SUPABASE-HOST:5432/postgres?sslmode=require"
    )


def test_real_rds_url_accepted():
    assert is_usable_database_url(
        "postgresql+psycopg2://postgres:Roadmsg123@"
        "database-4.ctmgcqss87g1.us-east-2.rds.amazonaws.com:5432/postgres?sslmode=require"
    )
