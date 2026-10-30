"""
file: config.py
importable: Settings, WebSettings, BACKEND_DIR

Settings reads its values from environment variables first and then from the
.env file in the backend folder. GITHUB_TOKEN is required, if it is missing
Settings() raises pydantic's ValidationError right away, so a bad setup fails
on startup and not in the middle of a long scan. The token is held as a
SecretStr, printing the settings shows stars instead of the token and the real
value is read with settings.github_token.get_secret_value().

WebSettings holds what the app needs before it starts, on its own it can be
built without a token. Settings has all of its fields too.

description: Loads and validates the app settings from the environment and the .env file.
"""

# XXX: BACKEND_DIR is worked out from where this file sits inside the source
# tree. That holds while the package is installed in editable mode, which is
# what uv sync does. A regular install puts the file under site-packages and
# the path would point somewhere meaningless, if that ever matters the default
# database location has to come from somewhere else.

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Paths ---

# config.py lives in backend/src/merge_hunt, so two levels up is backend.
# Paths hang off this instead of the current folder, that way running the app
# from the repo root or from backend finds the same .env and the same database.
BACKEND_DIR = Path(__file__).resolve().parents[2]

# --- Settings ---

# Middleware has to be added while the app is built, on import, and that has to
# keep working without a token. So these fields live apart from Settings. In an
# environment variable the origins are written as a JSON list.
class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # unrelated keys in .env should not crash the app
    )

    cors_origins: list[str] = ["http://localhost:5173"]

# Only main.py should create this. Other modules get the single value they need
# passed in, they never build Settings themselves.
class Settings(WebSettings):
    github_token: SecretStr
    # Four slashes after the scheme mean an absolute path for SQLAlchemy.
    database_url: str = f"sqlite+aiosqlite:///{BACKEND_DIR / 'data' / 'merge_hunt.db'}"
