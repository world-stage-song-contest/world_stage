from copy import deepcopy
from typing import Any

from psycopg.types.json import Jsonb

from .db import get_db

type Settings = dict[str, Any]


def get_user_settings(user_id: int) -> Settings:
    row = get_db().execute("SELECT settings FROM account WHERE id = %s", (user_id,)).fetchone()
    if row is None or not isinstance(row["settings"], dict):
        return {}
    return row["settings"]


def setting(settings: Settings, *path: str, default: Any = None) -> Any:
    value: Any = settings
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _merge_settings(current: Settings, patch: Settings) -> Settings:
    merged = deepcopy(current)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_settings(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def update_user_settings(cursor, user_id: int, patch: Settings) -> Settings:
    """Atomically merge a structured patch into an account's settings."""
    cursor.execute("SELECT settings FROM account WHERE id = %s FOR UPDATE", (user_id,))
    row = cursor.fetchone()
    if row is None:
        raise LookupError(f"Account {user_id} does not exist")
    current = row["settings"] if isinstance(row["settings"], dict) else {}
    merged = _merge_settings(current, patch)
    cursor.execute(
        "UPDATE account SET settings = %s WHERE id = %s",
        (Jsonb(merged), user_id),
    )
    return merged
