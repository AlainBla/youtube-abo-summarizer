"""Persists the last-checked timestamp per channel in last_run.json."""

import json
import os
from datetime import datetime, timezone

STATE_FILE = os.path.join(os.path.dirname(__file__), "last_run.json")


def _load() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_last_run(channel_id: str) -> datetime | None:
    data = _load()
    ts = data.get(channel_id)
    if ts is None:
        return None
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def set_last_run(channel_id: str, dt: datetime) -> None:
    data = _load()
    data[channel_id] = dt.astimezone(timezone.utc).isoformat()
    _save(data)
