"""Stamp Alembic to the existing DBeaver schema baseline (no DDL)."""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-m', 'alembic', 'stamp', 'head'],
        cwd=project_root,
        check=False,
    )
    if result.returncode == 0:
        print('Alembic stamped to head. No database DDL was executed.')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
