"""The ONLY reader/writer of data/themes.json: own contexts, the last one used,
and the speaking prompts Claude wrote for own contexts.

The file is small and authoritative (the prompts were paid for), written
atomically as a whole under a lock:

  {"version": 1,
   "custom": [{"key": "own_1", "label": "Ремонт машины", "created_at": ...}],
   "last":   {"key": "it_backend", "label": "IT / бэкенд"},   # key None = one-off
   "prompts": {"own_1": {"prompts": [{"question", "hint"}], "generated_at",
                         "model", "cost_usd"}}}

A theme everywhere else is {"key", "label"} (see app.themes); an own theme
that is deleted later still shows under its label wherever it was stored.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config, themes, utils

logger = logging.getLogger(__name__)

FILE_VERSION = 1
CUSTOM_PREFIX = "own_"

_lock = threading.Lock()

Theme = Dict[str, Optional[str]]


class ThemeError(ValueError):
    """A theme choice that cannot be used; the message is shown to the learner."""


def _path() -> Path:
    return config.data_dir() / config.THEMES_FILENAME


def _load() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    path = _path()
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            logger.warning("Could not read %s; starting from defaults", path)
    custom = [
        c for c in data.get("custom") or []
        if isinstance(c, dict) and isinstance(c.get("key"), str) and c.get("label")
    ]
    prompts = data.get("prompts") if isinstance(data.get("prompts"), dict) else {}
    last = data.get("last") if isinstance(data.get("last"), dict) else None
    return {"version": FILE_VERSION, "custom": custom, "last": last, "prompts": prompts}


def _save(data: Dict[str, Any]) -> None:
    config.data_dir().mkdir(parents=True, exist_ok=True)
    utils.write_json(_path(), data)


def _custom_by_key(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {c["key"]: c for c in data["custom"]}


# ------------------------------------------------------------------- reading
def overview() -> Dict[str, Any]:
    """Everything the picker shows: built-in and own themes, the default."""
    data = _load()
    custom = [
        {
            "key": c["key"],
            "label": c["label"],
            "has_prompts": bool((data["prompts"].get(c["key"]) or {}).get("prompts")),
        }
        for c in data["custom"]
    ]
    return {"builtin": themes.builtin_list(), "custom": custom, "last": last_used(data)}


def last_used(data: Optional[Dict[str, Any]] = None) -> Theme:
    """The default for the next generation: the last theme used, if it still
    exists (an own theme may have been deleted since), else IT / backend."""
    data = data if data is not None else _load()
    last = data.get("last") or {}
    key, label = last.get("key"), themes.clean_label(last.get("label"))
    if themes.is_builtin(key):
        return {"key": key, "label": themes.THEME_BY_KEY[key].label}
    if key and key in _custom_by_key(data):
        return {"key": key, "label": _custom_by_key(data)[key]["label"]}
    if not key and label:
        return {"key": None, "label": label}
    return themes.default_theme()


def resolve(key: Optional[str] = None, label: Optional[str] = None) -> Theme:
    """A theme from a request: a known key, or a one-off label, or - with
    neither - the last one used."""
    if key:
        if themes.is_builtin(key):
            return {"key": key, "label": themes.THEME_BY_KEY[key].label}
        custom = _custom_by_key(_load()).get(key)
        if custom is None:
            raise ThemeError("Такого уклона нет — возможно, он удалён.")
        return {"key": key, "label": custom["label"]}
    cleaned = themes.clean_label(label)
    if cleaned:
        return {"key": None, "label": cleaned}
    return last_used()


# ------------------------------------------------------------------- writing
def remember(theme: Theme) -> None:
    """Make `theme` the default for the next generation."""
    with _lock:
        data = _load()
        data["last"] = {"key": theme.get("key"), "label": theme.get("label")}
        _save(data)


def add_custom(label: str, now: Optional[dt.datetime] = None) -> Theme:
    """Save an own theme; the same label again returns the existing one."""
    cleaned = themes.clean_label(label)
    if not cleaned:
        raise ThemeError("Напишите, о чём должен быть уклон.")
    with _lock:
        data = _load()
        for existing in data["custom"]:
            if existing["label"].casefold() == cleaned.casefold():
                return {"key": existing["key"], "label": existing["label"]}
        for builtin in themes.BUILTIN_THEMES:
            if builtin.label.casefold() == cleaned.casefold():
                return {"key": builtin.key, "label": builtin.label}
        if len(data["custom"]) >= config.THEMES_MAX_CUSTOM:
            raise ThemeError("Слишком много своих уклонов — удалите ненужные.")
        numbers = [
            int(c["key"][len(CUSTOM_PREFIX):])
            for c in data["custom"]
            if c["key"].startswith(CUSTOM_PREFIX) and c["key"][len(CUSTOM_PREFIX):].isdigit()
        ]
        key = f"{CUSTOM_PREFIX}{max(numbers, default=0) + 1}"
        data["custom"].append(
            {
                "key": key,
                "label": cleaned,
                "created_at": (now or dt.datetime.now()).isoformat(timespec="seconds"),
            }
        )
        _save(data)
    return {"key": key, "label": cleaned}


def delete_custom(key: str) -> bool:
    """Remove an own theme and its prompts; sets and analyses keep its label."""
    with _lock:
        data = _load()
        if key not in _custom_by_key(data):
            return False
        data["custom"] = [c for c in data["custom"] if c["key"] != key]
        data["prompts"].pop(key, None)
        if (data.get("last") or {}).get("key") == key:
            data["last"] = None
        _save(data)
    return True


def store_prompts(
    key: str, prompts: List[Dict[str, str]], *, model: str, cost_usd: Optional[float]
) -> None:
    with _lock:
        data = _load()
        if key not in _custom_by_key(data):
            raise ThemeError("Такого уклона нет — возможно, он удалён.")
        data["prompts"][key] = {
            "prompts": [{"question": p["question"], "hint": p["hint"]} for p in prompts],
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "model": model,
            "cost_usd": cost_usd,
        }
        _save(data)


# ------------------------------------------------------------------ prompts
def prompts_for(key: str) -> List[Dict[str, str]]:
    """A theme's speaking prompts with ids; [] for an own theme Claude has
    not written prompts for yet."""
    if themes.is_builtin(key):
        return themes.builtin_prompts(key)
    stored = (_load()["prompts"].get(key) or {}).get("prompts") or []
    return [
        {"id": f"{key}:{number}", "question": p["question"], "hint": p["hint"]}
        for number, p in enumerate(stored)
    ]


def resolve_prompt(prompt_id: str) -> Optional[Dict[str, Any]]:
    """A prompt by id, with the theme it belongs to; None when unknown."""
    try:
        key, number = themes.split_prompt_id(prompt_id)
        theme = resolve(key)
    except ValueError:
        return None
    prompts = prompts_for(key)
    if number >= len(prompts):
        return None
    return {**prompts[number], "theme": theme}


def prompt_of_day(today: dt.date, offset: int = 0) -> Dict[str, Any]:
    """Today's prompt in the last-used theme («Вперемешку» when that theme has
    no prompts: a one-off, or an own theme without generated prompts)."""
    theme = last_used()
    prompts = prompts_for(theme["key"]) if theme.get("key") else []
    if not prompts:
        theme = {"key": themes.MIXED, "label": themes.THEME_BY_KEY[themes.MIXED].label}
        prompts = themes.builtin_prompts(themes.MIXED)
    # `theme` is the list the prompt was picked from, so a screen opened on it
    # can offer the rest of that list («Другая тема»).
    return {**themes.prompt_of_day(prompts, today, offset), "theme": theme}
