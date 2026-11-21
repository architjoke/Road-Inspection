from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from flask import Flask

load_dotenv(Path(__file__).resolve().parents[1] / '.env')

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.config import Config
from app.extensions import db

flask_app = Flask(__name__)
flask_app.config.from_object(Config)
db.init_app(flask_app)
flask_app.app_context().push()

target_metadata = db.metadata


def get_url():
    return Config.SQLALCHEMY_DATABASE_URI


_db_url = get_url()
if _db_url:
    # Alembic interpolates %, so escape connection-string percent encodings.
    config.set_main_option("sqlalchemy.url", _db_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = db.engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
