"""
Shared utility functions used across modules.

Includes:
  - load_config()     — read and return config.json as a dict
  - save_config()     — write an updated config dict back to config.json atomically
  - log_event()       — insert a row into the logs table
  - slugify()         — convert a string to a filename-safe slug
  - ensure_dir()      — create a directory if it doesn't exist
"""

import json
import os
import re


CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")


def load_config(path: str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(data: dict, path: str = CONFIG_PATH) -> None:
    # Write to a sibling .tmp file then use os.replace for atomic swap.
    # Avoids shutil.move + tempfile quirks on Windows (empty dir_, locking).
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def log_event(db_conn, level: str, module: str, message: str) -> None:
    """Insert a log row. db_conn is a sqlite3.Connection."""
    db_conn.execute(
        "INSERT INTO logs (level, module, message) VALUES (?, ?, ?)",
        (level.upper(), module, message),
    )
    db_conn.commit()


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_-]+", "-", text)


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path
