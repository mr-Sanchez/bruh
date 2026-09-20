"""The only place dictation lessons are read from and written to disk.

One lesson is one directory under data/dictation/<video id>/:

    lesson.json     the video's metadata and its sentences (with timings)
    audio.<ext>     the audio as YouTube served it - never re-encoded
    subtitles.vtt   the caption track it was built from, kept verbatim
    results.jsonl   append-only, AUTHORITATIVE: every dictated sentence

`results.jsonl` is to a lesson what attempts.jsonl is to the learner model:
it is never rewritten or truncated, and everything shown about progress
(how far through a lesson, the day's count, the tricky words) is derived
from it on read. lesson.json, by contrast, can always be rebuilt by
importing the video again - so an import simply overwrites it.

Dictation results deliberately do not enter the item bank or the attempts
log (decided 2026-09-20): mishearing a word is not one of the taxonomy's
speaking mistakes. They surface as their own «сложные слова» list instead.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app import config, dictation, utils

logger = logging.getLogger(__name__)

_results_lock = threading.Lock()
RESULT_RECORD_VERSION = 1


# -------------------------------------------------------------------- paths
def lesson_dir(lesson_id: str) -> Path:
    return config.dictation_dir() / lesson_id


def _meta_path(lesson_id: str) -> Path:
    return lesson_dir(lesson_id) / config.LESSON_META_FILENAME


def _results_path(lesson_id: str) -> Path:
    return lesson_dir(lesson_id) / config.LESSON_RESULTS_FILENAME


def audio_path(lesson: Dict[str, Any]) -> Optional[Path]:
    name = lesson.get("audio_filename")
    if not name:
        return None
    path = lesson_dir(lesson["id"]) / name
    return path if path.is_file() else None


# ------------------------------------------------------------------ lessons
def load_lesson(lesson_id: str) -> Optional[Dict[str, Any]]:
    path = _meta_path(lesson_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read the lesson at %s", path)
        return None
    return data if isinstance(data, dict) else None


def save_lesson(lesson: Dict[str, Any]) -> Path:
    directory = lesson_dir(lesson["id"])
    directory.mkdir(parents=True, exist_ok=True)
    lesson["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return utils.write_json(directory / config.LESSON_META_FILENAME, lesson)


def start_import(video_id: str, url: str, language_key: str) -> Dict[str, Any]:
    """Create (or reset) a lesson in the `importing` state and return it.

    Re-importing a video keeps its directory - and therefore its
    results.jsonl - so an interrupted download can simply be repeated.
    """
    existing = load_lesson(video_id) or {}
    lesson = {
        "schema_version": config.LESSON_SCHEMA_VERSION,
        "id": video_id,
        "video_id": video_id,
        "url": url,
        "language": language_key,
        "status": config.LESSON_STATUS_IMPORTING,
        "title": existing.get("title") or url,
        "uploader": existing.get("uploader", ""),
        "duration_seconds": existing.get("duration_seconds", 0.0),
        "imported_at": dt.datetime.now().isoformat(timespec="seconds"),
        "audio_filename": existing.get("audio_filename"),
        "subtitle_language": existing.get("subtitle_language"),
        "subtitle_kind": existing.get("subtitle_kind"),
        "sentences": existing.get("sentences", []),
        "error_message": None,
    }
    save_lesson(lesson)
    return lesson


def finish_import(
    lesson: Dict[str, Any], fetched: Any, sentences: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Store the downloaded lesson: metadata, sentences and the caption file."""
    directory = lesson_dir(lesson["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / config.LESSON_SUBTITLES_FILENAME).write_text(
        fetched.subtitles, encoding="utf-8"
    )
    lesson.update(
        {
            "status": config.LESSON_STATUS_READY,
            "url": fetched.url,
            "title": fetched.title,
            "uploader": fetched.uploader,
            "duration_seconds": round(float(fetched.duration_seconds or 0.0), 3),
            "audio_filename": fetched.audio_filename,
            "subtitle_language": fetched.subtitle_language,
            "subtitle_kind": fetched.subtitle_kind,
            "sentences": list(sentences),
            "error_message": None,
        }
    )
    save_lesson(lesson)
    return lesson


def fail_import(lesson: Dict[str, Any], message: str) -> Dict[str, Any]:
    lesson.update({"status": config.LESSON_STATUS_ERROR, "error_message": message})
    save_lesson(lesson)
    return lesson


def list_lessons() -> List[Dict[str, Any]]:
    """Every lesson, newest import first."""
    root = config.dictation_dir()
    if not root.is_dir():
        return []
    lessons = [
        lesson
        for lesson in (load_lesson(path.name) for path in sorted(root.iterdir()) if path.is_dir())
        if lesson is not None
    ]
    lessons.sort(key=lambda lesson: str(lesson.get("imported_at") or ""), reverse=True)
    return lessons


def delete_lesson(lesson_id: str) -> bool:
    """Remove a lesson with its audio and its results (an explicit choice)."""
    directory = lesson_dir(lesson_id)
    if not directory.is_dir():
        return False
    for path in sorted(directory.iterdir(), reverse=True):
        if path.is_file():
            path.unlink(missing_ok=True)
    directory.rmdir()
    return True


# ------------------------------------------------------------------ results
def append_result(lesson_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Log one dictated sentence. Callers pass an already graded record."""
    stored = {"v": RESULT_RECORD_VERSION, "ts": dt.datetime.now().isoformat(), **record}
    with _results_lock:
        utils.append_jsonl(_results_path(lesson_id), stored)
    return stored


def lesson_results(lesson_id: str) -> List[Dict[str, Any]]:
    return [
        record
        for record in utils.read_jsonl(_results_path(lesson_id))
        if isinstance(record.get("sentence"), int)
    ]


def all_results() -> List[Dict[str, Any]]:
    """Every lesson's results together, for the cross-lesson statistics."""
    records: List[Dict[str, Any]] = []
    for lesson in list_lessons():
        for record in lesson_results(lesson["id"]):
            records.append({**record, "lesson_id": lesson["id"], "title": lesson.get("title")})
    return records


# --------------------------------------------------------------- read model
def lesson_summary(lesson: Dict[str, Any]) -> Dict[str, Any]:
    """A lesson without its sentences: the list and the workout step."""
    sentences = lesson.get("sentences") or []
    progress = dictation.lesson_progress(len(sentences), lesson_results(lesson["id"]))
    return {
        "id": lesson["id"],
        "title": lesson.get("title"),
        "uploader": lesson.get("uploader", ""),
        "url": lesson.get("url"),
        "status": lesson.get("status"),
        "error_message": lesson.get("error_message"),
        "language": lesson.get("language"),
        "duration_seconds": lesson.get("duration_seconds", 0.0),
        "subtitle_kind": lesson.get("subtitle_kind"),
        "imported_at": lesson.get("imported_at"),
        "has_audio": audio_path(lesson) is not None,
        "progress": progress,
    }


def lesson_payload(lesson: Dict[str, Any]) -> Dict[str, Any]:
    """The lesson page's data: the summary plus every sentence, tokenised.

    The sentence text is sent along with its tokens so the browser never has
    to re-implement the splitting rules - it only hides the words.
    """
    sentences = []
    for sentence in lesson.get("sentences") or []:
        sentences.append({**sentence, "tokens": dictation.tokenize(sentence["text"])})
    results = lesson_results(lesson["id"])
    return {
        **lesson_summary(lesson),
        "sentences": sentences,
        "results": dictation.latest_by_sentence(results),
    }


def stats(limit: int = config.DICTATION_TRICKY_LIMIT) -> Dict[str, Any]:
    """Cross-lesson dictation statistics for «Прогресс»."""
    lessons = list_lessons()
    records = all_results()
    completed = [record for record in records if record.get("completed")]
    words = sum(int(record.get("total_words") or 0) for record in records)
    correct = sum(len(record.get("correct_words") or ()) for record in records)
    return {
        "lessons": len(lessons),
        "sentences": len(completed),
        "words": words,
        "accuracy": round(correct / words, 3) if words else None,
        "hints": sum(len(record.get("hint_words") or ()) for record in records),
        "tricky_words": dictation.tricky_words(records, limit=limit),
    }


def done_today(now: Optional[dt.datetime] = None) -> int:
    """Sentences finished today, across every lesson."""
    day = (now or dt.datetime.now()).date()
    return dictation.done_on(all_results(), day)


def active_days() -> set:
    """Days with at least one dictated sentence - they keep the streak alive."""
    return dictation.active_days(all_results())


def daily_counts() -> Dict[str, Dict[str, Any]]:
    """ISO date -> what was dictated that day, for «История»."""
    return dictation.daily_counts(all_results())


def next_lesson(now: Optional[dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """The lesson the daily workout points at: the one in progress, else the
    newest ready one that still has sentences left."""
    ready = [
        lesson for lesson in list_lessons() if lesson.get("status") == config.LESSON_STATUS_READY
    ]
    summaries = [lesson_summary(lesson) for lesson in ready]
    unfinished = [
        summary
        for summary in summaries
        if summary["progress"]["done"] < summary["progress"]["sentences"]
    ]
    if not unfinished:
        return summaries[0] if summaries else None
    started = [summary for summary in unfinished if summary["progress"]["started"]]
    if started:
        started.sort(key=lambda summary: str(summary["progress"]["last_at"] or ""), reverse=True)
        return started[0]
    return unfinished[0]
