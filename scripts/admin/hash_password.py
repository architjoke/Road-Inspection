"""Print a bcrypt hash for pasting into DBeaver. Does not write to the database."""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth.passwords import hash_password


def main():
    password = getpass.getpass('Password to hash: ')
    if not password:
        print('No password entered.')
        sys.exit(1)
    print(hash_password(password))


if __name__ == '__main__':
    main()
