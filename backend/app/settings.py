"""Runtime paths and the single approved model route for this assignment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


HW5_DIR = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = HW5_DIR.parent
load_dotenv(HW5_DIR / ".env", override=False)
load_dotenv(REPOSITORY_ROOT / ".env", override=False)

ORIGINAL_DB_PATH = HW5_DIR / "data" / "campus_customs.db"
_configured_db = os.getenv("CAMPUS_CUSTOMS_DB")
DB_PATH = Path(_configured_db).expanduser() if _configured_db else HW5_DIR / "data" / "campus_customs_new.db"
if not DB_PATH.is_absolute():
    DB_PATH = (HW5_DIR / DB_PATH).resolve()

PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY", "")
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
MODEL_NAME = "gpt-6-luna"
FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
