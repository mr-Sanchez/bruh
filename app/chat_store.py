"""The ONLY reader/writer of assistant chats: data/assistant/<id>.json.

One file per chat: the selection it started from, the card around it, the
screen, and every message in order. Answers are paid for and the learner
wants to come back to them (decided 2026-09-27: «храним историю»), so a
chat is only ever added to - nothing is rewritten away. A question is
stored before Claude is asked, so a failed call never loses it and the
answer can be asked for again.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config, utils

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
ID_PATTERN = r"^chat-[0-9]{8}-[0-9]{6}(-[0-9]{1,3})?$"
TITLE_MAX_CHARS = 80

_lock = threading.Lock()


def _path(chat_id: str) -> Path:
    return config.assistant_dir() / f"{chat_id}.json"


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def new_chat_id(now: Optional[dt.datetime] = None) -> str:
    """chat-YYYYMMDD-HHMMSS, with a suffix if two chats land in one second."""
    base = f"chat-{(now or dt.datetime.now()).strftime('%Y%m%d-%H%M%S')}"
    candidate, suffix = base, 1
    while _path(candidate).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def title_for(selection: str, question: str) -> str:
    """The selected words on one line, or the question when nothing was selected."""
    text = " ".join((selection or question).split())
    return text if len(text) <= TITLE_MAX_CHARS else text[: TITLE_MAX_CHARS - 1] + "…"


def create_chat(
    *,
    question: str,
    selection: str,
    context: str,
    screen: Dict[str, str],
) -> Dict[str, Any]:
    """A new chat holding the first question; no answer yet."""
    with _lock:
        config.assistant_dir().mkdir(parents=True, exist_ok=True)
        now = _now()
        chat = {
            "schema_version": SCHEMA_VERSION,
            "id": new_chat_id(),
            "created_at": now,
            "updated_at": now,
            "title": title_for(selection, question),
            "screen": screen,
            "selection": selection,
            "context": context,
            "messages": [{"role": "user", "text": question, "at": now}],
        }
        utils.write_json(_path(chat["id"]), chat)
    return chat


def load_chat(chat_id: str) -> Optional[Dict[str, Any]]:
    """A chat by id (the caller validates it against ID_PATTERN), or None."""
    path = _path(chat_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read assistant chat %s", path)
        return None
    return data if isinstance(data, dict) and data.get("id") == chat_id else None


def add_message(chat_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
    """Append a message ({"role", "text", ...}) and return the whole chat."""
    with _lock:
        chat = load_chat(chat_id)
        if chat is None:
            raise KeyError(chat_id)
        now = _now()
        chat.setdefault("messages", []).append({**message, "at": now})
        chat["updated_at"] = now
        utils.write_json(_path(chat_id), chat)
    return chat


def list_chats() -> List[Dict[str, Any]]:
    """Every readable chat, the most recently active first."""
    directory = config.assistant_dir()
    if not directory.is_dir():
        return []
    chats = [load_chat(path.stem) for path in directory.glob("chat-*.json")]
    readable = [chat for chat in chats if chat is not None]
    readable.sort(key=lambda chat: chat.get("updated_at", ""), reverse=True)
    return readable


def awaits_answer(chat: Dict[str, Any]) -> bool:
    """True when the last message is the learner's: an answer is owed."""
    messages = chat.get("messages") or []
    return bool(messages) and messages[-1].get("role") == "user"


def chat_summary(chat: Dict[str, Any]) -> Dict[str, Any]:
    """A chat without its messages and context, for the list."""
    messages = chat.get("messages") or []
    return {
        "id": chat["id"],
        "title": chat.get("title", ""),
        "screen": chat.get("screen"),
        "created_at": chat.get("created_at"),
        "updated_at": chat.get("updated_at"),
        "messages": len(messages),
        "cost_usd": round(sum(m.get("cost_usd") or 0.0 for m in messages), 4),
    }
