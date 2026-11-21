import bcrypt
from werkzeug.security import check_password_hash as werkzeug_check_password_hash


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')


def classify_password_hash(password_hash: str) -> str:
    if not password_hash:
        return 'empty'
    if password_hash.startswith('$2'):
        return 'bcrypt'
    if password_hash.startswith(('pbkdf2:', 'scrypt:', 'argon2')):
        return 'werkzeug'
    return 'unknown'


def check_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False

    stored = password_hash.encode('utf-8') if isinstance(password_hash, str) else password_hash
    if stored.startswith(b'$2'):
        try:
            return bcrypt.checkpw(password.encode('utf-8'), stored)
        except ValueError:
            return False

    try:
        return werkzeug_check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False
