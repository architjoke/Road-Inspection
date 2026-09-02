"""Baseline for existing DBeaver schema.

Revision ID: 001_baseline
Revises:
Create Date: 2026-09-02

The 20 application tables already exist in AWS RDS PostgreSQL.
This revision is a no-op baseline used with `alembic stamp head`.
Do not run DDL migrations against the existing schema unless you
intentionally want Alembic to manage future changes.
"""

from typing import Sequence, Union

revision: str = '001_baseline'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
