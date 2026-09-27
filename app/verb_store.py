"""The only reader/writer of data/irregular_verbs.jsonl - the irregular-verb drill log.

One line per checked verb. The file is append-only and AUTHORITATIVE, like
attempts.jsonl: never rewritten or truncated; everything shown (the table's
marks, which verbs a drill picks, the streak) is derived from it on read.
The rules themselves live in app.irregular_verbs.
"""

from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config, irregular_verbs, utils

_lock = threading.Lock()
RECORD_VERSION = 1


def _path() -> Path:
    return config.data_dir() / config.IRREGULAR_VERBS_FILENAME


def all_results() -> List[Dict[str, Any]]:
    return utils.read_jsonl(_path())


def append_result(graded: Dict[str, Any], now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    record = {
        "v": RECORD_VERSION,
        "ts": (now or dt.datetime.now()).isoformat(timespec="seconds"),
        "verb": graded["verb"],
        "answers": [form["answer"] for form in graded["forms"]],
        "forms_correct": [form["correct"] for form in graded["forms"]],
        "correct": graded["correct"],
    }
    with _lock:
        utils.append_jsonl(_path(), record)
    return record


def stats() -> Dict[str, Dict[str, Any]]:
    return irregular_verbs.verb_stats(all_results())


def active_days() -> set:
    """Days with at least one checked verb - they keep the streak alive."""
    return irregular_verbs.active_days(all_results())
