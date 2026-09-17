"""Emplacements des données (jamais versionnées)."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = PACKAGE_DIR / "web"
_REPO_ROOT = PACKAGE_DIR.parent


def _data_dir() -> Path:
    if os.environ.get("JOBBOT_DATA"):
        return Path(os.environ["JOBBOT_DATA"]).expanduser()
    if (_REPO_ROOT / "pyproject.toml").exists():  # installation depuis le dépôt (pip install -e .)
        return _REPO_ROOT / "data"
    return Path.home() / ".jobbot"


DATA_DIR = _data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "jobbot.db"
CONFIG_PATH = DATA_DIR / "config.json"
GEO_CACHE_PATH = DATA_DIR / "geo_cache.json"
CSV_PATH = DATA_DIR / "offres_aide_soignant.csv"
JSON_PATH = DATA_DIR / "offres_aide_soignant.json"
