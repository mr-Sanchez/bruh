"""FastAPI routes for the local web app: sessions, analysis, progress, topics.

Kept deliberately thin: this module wires HTTP to the existing per-session
flat-file model (app.utils) plus the two API integrations (app.transcriber,
app.analyzer). Transcriber/analyzer construction goes through small factory
dependencies so tests can substitute fakes via app.dependency_overrides,
mirroring the client_factory pattern already used inside those modules.
"""

from __future__ import annotations

import datetime as dt
import logging
import mimetypes
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import config, learner_store, progress_store, utils
from app.analyzer import (
    ANALYSIS_SCHEMA_VERSION,
    AnalysisError,
    ClaudeAnalyzer,
    MissingAnthropicApiKeyError,
)
from app.transcriber import DeepgramTranscriber, TranscriptionError
from app.utils import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

TranscriberFactory = Callable[[config.LanguageProfile], DeepgramTranscriber]
AnalyzerFactory = Callable[[], ClaudeAnalyzer]


# ------------------------------------------------------------- dependencies
def get_transcriber_factory() -> TranscriberFactory:
    def factory(profile: config.LanguageProfile) -> DeepgramTranscriber:
        return DeepgramTranscriber(
            config.get_api_key(), profile=profile, mip_opt_out=config.mip_opt_out()
        )

    return factory


def get_analyzer_factory() -> AnalyzerFactory:
    def factory() -> ClaudeAnalyzer:
        return ClaudeAnalyzer(config.get_anthropic_api_key())

    return factory


# ------------------------------------------------------------------ helpers
def _session_directory(session_id: str) -> Path:
    # session_id comes straight from the URL - guard against path traversal
    # before it is joined onto a filesystem path.
    if not session_id or "/" in session_id or "\\" in session_id or session_id in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid session id.")
    return config.recordings_dir() / session_id


def _load_session_or_404(session_id: str) -> Session:
    directory = _session_directory(session_id)
    session = utils.read_session_meta(directory) if directory.is_dir() else None
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session.")
    return session


def _read_analysis(session: Session) -> Optional[Dict[str, Any]]:
    if not session.analysis_path.is_file():
        return None
    import json

    try:
        return json.loads(session.analysis_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read analysis file at %s", session.analysis_path)
        return None


def _session_summary(session: Session) -> Dict[str, Any]:
    return {
        "id": session.id,
        "started_at": session.started_at_text,
        "duration_seconds": session.duration_seconds,
        "language": session.language_key,
        "status": session.status,
        "has_analysis": session.analysis_path.is_file(),
    }


def _transcribe_session(
    session: Session, profile: config.LanguageProfile, transcriber_factory: TranscriberFactory
) -> None:
    """Runs in a background thread; the session's own session.json is the result."""
    transcriber = transcriber_factory(profile)
    try:
        result = transcriber.transcribe(session.audio_path)
        utils.write_json(session.response_path, result.raw_response)
        utils.write_transcript(session, result.transcript, profile)
        session.transcript = result.transcript
        session.status = utils.STATUS_DONE
        # Deepgram bills by audio length; its own metadata.duration is the
        # figure it bills on (the app still never decodes audio itself).
        audio_seconds = result.audio_duration or session.duration_seconds
        learner_store.record_deepgram_usage(
            "transcription", profile.key, audio_seconds, session_id=session.id
        )
    except TranscriptionError as exc:
        logger.error("Transcription failed for %s: %s", session.id, exc)
        session.status = utils.STATUS_ERROR
        session.error_message = str(exc)
    except Exception as exc:  # pragma: no cover - unexpected
        logger.exception("Unexpected error transcribing %s", session.id)
        session.status = utils.STATUS_ERROR
        session.error_message = f"Unexpected error: {exc}"
    finally:
        utils.write_session_meta(session)


# ---------------------------------------------------------------- /config
@router.get("/config")
def get_config() -> Dict[str, Any]:
    return {
        "language_profiles": [
            {"key": profile.key, "label": profile.label, "note": profile.note}
            for profile in config.LANGUAGE_PROFILES
        ],
        "default_language": config.default_profile().key,
        "deepgram_configured": config.get_api_key() is not None,
        "anthropic_configured": config.get_anthropic_api_key() is not None,
    }


# --------------------------------------------------------------- /sessions
@router.post("/sessions", status_code=202)
async def create_session(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    language: str = Form(config.DEFAULT_LANGUAGE_KEY),
    client_duration_seconds: float = Form(0.0),
    mime_type: str = Form(""),
    transcriber_factory: TranscriberFactory = Depends(get_transcriber_factory),
) -> Dict[str, Any]:
    profile = config.profile_by_key(language)
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="The uploaded recording is empty.")

    try:
        session = utils.create_session()
    except OSError as exc:
        logger.exception("Could not create the session directory")
        raise HTTPException(status_code=500, detail=f"Could not create the recordings folder: {exc}")

    session.audio_filename = "audio" + config.extension_for_mime(mime_type or file.content_type)
    session.language_key = profile.key
    session.duration_seconds = client_duration_seconds

    try:
        session.audio_path.write_bytes(audio_bytes)
    except OSError as exc:
        logger.exception("Could not save the uploaded recording")
        raise HTTPException(status_code=500, detail=f"Could not save the recording: {exc}")

    if client_duration_seconds < config.MIN_RECORDING_SECONDS:
        session.status = utils.STATUS_ERROR
        session.error_message = "The recording is empty (nothing worth transcribing)."
        utils.write_session_meta(session)
        return {"session_id": session.id, "status": session.status, "detail": session.error_message}

    if not transcriber_factory(profile).has_api_key:
        session.status = utils.STATUS_ERROR
        session.error_message = config.MISSING_API_KEY_MESSAGE
        utils.write_session_meta(session)
        return {"session_id": session.id, "status": session.status, "detail": session.error_message}

    session.status = utils.STATUS_TRANSCRIBING
    utils.write_session_meta(session)
    background_tasks.add_task(_transcribe_session, session, profile, transcriber_factory)

    return {"session_id": session.id, "status": session.status}


@router.get("/sessions")
def list_sessions() -> Dict[str, Any]:
    sessions = utils.list_sessions()
    return {"sessions": [_session_summary(session) for session in sessions]}


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> Dict[str, Any]:
    session = _load_session_or_404(session_id)
    return {
        **_session_summary(session),
        "error_message": session.error_message,
        "transcript": session.transcript,
        "audio_url": f"/api/sessions/{session.id}/audio" if session.audio_path.is_file() else None,
        "analysis": _read_analysis(session),
    }


@router.get("/sessions/{session_id}/audio")
def get_session_audio(session_id: str) -> FileResponse:
    session = _load_session_or_404(session_id)
    if not session.audio_path.is_file():
        raise HTTPException(status_code=404, detail="No audio recorded for this session.")
    media_type = mimetypes.guess_type(str(session.audio_path))[0] or "application/octet-stream"
    return FileResponse(session.audio_path, media_type=media_type)


class AnalyzeRequest(BaseModel):
    force: bool = False


@router.post("/sessions/{session_id}/analyze")
def analyze_session(
    session_id: str,
    body: AnalyzeRequest = AnalyzeRequest(),
    analyzer_factory: AnalyzerFactory = Depends(get_analyzer_factory),
) -> Dict[str, Any]:
    session = _load_session_or_404(session_id)
    if not session.transcript:
        raise HTTPException(status_code=400, detail="This session has no transcript yet.")

    cached = _read_analysis(session)
    if cached is not None and not body.force:
        return {"session_id": session.id, "analysis": cached, "progress_updated": False}

    analyzer = analyzer_factory()
    profile = config.profile_by_key(session.language_key)
    try:
        result = analyzer.analyze(session.transcript, profile, session.duration_seconds)
    except MissingAnthropicApiKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    usage_record = learner_store.record_claude_usage(
        "analysis", result.model, result.usage, session_id=session.id
    )
    analysis_payload: Dict[str, Any] = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "created_at": dt.datetime.now().isoformat(),
        "model": result.model,
        "effort": result.effort,
        "language": session.language_key,
        "summary": result.summary,
        "strengths": result.strengths,
        "issues": [issue.model_dump() for issue in result.issues],
        "vocabulary": [item.model_dump() for item in result.vocabulary],
        "improved_version": result.improved_version,
        "takeaways": [item.model_dump() for item in result.takeaways],
        "scores": result.scores.model_dump() if result.scores else None,
        "overall_score": result.overall_score,
        "topic_counts": result.topic_counts,
        "request_id": result.request_id,
        "usage": {**result.usage, "cost_usd": usage_record["cost_usd"]},
    }
    utils.write_json(session.analysis_path, analysis_payload)
    learner_store.refresh_after_analysis()

    return {"session_id": session.id, "analysis": analysis_payload, "progress_updated": True}


# --------------------------------------------------------------- /progress
@router.get("/progress")
def get_progress() -> Dict[str, Any]:
    data = progress_store.load_progress()
    session_ids = {sid for entry in data["topics"].values() for sid in entry.get("session_ids", [])}
    return {
        "sessions_analyzed": len(session_ids),
        "updated_at": data.get("updated_at"),
        "topics": progress_store.top_weak_topics(n=20),
        "score_history": data.get("score_history", []),
    }


@router.get("/topics")
def get_topics() -> Dict[str, Any]:
    return {
        "topics": [
            {
                "key": key,
                "label": info["label"],
                "description": info["description"],
                "resources": [
                    {"title": title, "url": url}
                    for title, url in config.TOPIC_RESOURCES.get(key, ())
                ],
                "has_cloze": key in config.CLOZE_WORDS,
            }
            for key, info in progress_store.TOPIC_TAXONOMY.items()
        ]
    }


# ---------------------------------------------------------------- /learner
_SLUG_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"


_SESSION_ID_PATTERN = r"^[0-9A-Za-z_-]{1,64}$"


class AttemptRequest(BaseModel):
    """A card attempt names item_id and says whether it was right; a topic
    drill names topic and gives its score - "correct" is derived from that."""

    item_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    topic: Optional[str] = Field(default=None, pattern=_SLUG_PATTERN)
    exercise: str = Field(pattern=_SLUG_PATTERN)
    correct: Optional[bool] = None
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    session_id: Optional[str] = Field(default=None, pattern=_SESSION_ID_PATTERN)
    answer: Optional[str] = Field(default=None, max_length=4000)
    context: Optional[str] = Field(default=None, pattern=_SLUG_PATTERN)


@router.get("/learner/items")
def get_learner_items(
    due_only: bool = False, topic: Optional[str] = None, kind: Optional[str] = None
) -> Dict[str, Any]:
    """Bank items with their Leitner state; due_only gives today's review queue."""
    today = dt.date.today()
    bank = learner_store.load_item_bank()
    states = learner_store.item_states(bank)
    items = []
    for item_id, item in bank["items"].items():
        state = states[item_id]
        if due_only and not state.is_due(today):
            continue
        if topic is not None and item.get("topic") != topic:
            continue
        if kind is not None and item.get("kind") != kind:
            continue
        items.append(learner_store.card(item, state, today))
    items.sort(key=lambda entry: (entry["state"]["due"] or "9999", entry["id"]))
    return {"today": today.isoformat(), "count": len(items), "items": items}


@router.get("/learner/queue")
def get_learner_queue() -> Dict[str, Any]:
    """Today's cards: all due reviews plus the day's allowance of new ones."""
    return learner_store.daily_queue()


@router.post("/learner/attempts", status_code=201)
def post_learner_attempt(body: AttemptRequest) -> Dict[str, Any]:
    if (body.item_id is None) == (body.topic is None):
        raise HTTPException(status_code=400, detail="Give exactly one of item_id or topic.")
    if body.topic is not None:
        if body.topic not in progress_store.TOPIC_TAXONOMY:
            raise HTTPException(status_code=404, detail="Unknown topic.")
        if body.score is None:
            raise HTTPException(status_code=400, detail="A topic drill needs a score.")
        attempt = learner_store.append_attempt(
            None,
            body.exercise,
            body.score >= config.DRILL_PASS_SCORE,
            topic=body.topic,
            score=body.score,
            session_id=body.session_id,
            answer=body.answer,
            context=body.context,
        )
        return {"attempt": attempt}

    if body.correct is None:
        raise HTTPException(status_code=400, detail="A card attempt needs correct.")
    bank = learner_store.load_item_bank()
    if body.item_id not in bank["items"]:
        raise HTTPException(status_code=404, detail="Unknown item.")
    attempt = learner_store.append_attempt(
        body.item_id, body.exercise, body.correct, answer=body.answer, context=body.context
    )
    state = learner_store.item_states(bank)[body.item_id]
    return {
        "attempt": attempt,
        "state": {**state.to_dict(), "is_due": state.is_due(dt.date.today())},
    }


@router.get("/learner/texts")
def get_learner_texts(topic: Optional[str] = None) -> Dict[str, Any]:
    """improved_version texts for cloze drills; with topic, their gaps too."""
    if topic is not None and topic not in config.CLOZE_WORDS:
        raise HTTPException(status_code=404, detail="No cloze drill for this topic.")
    return {"topic": topic, "texts": learner_store.practice_texts(topic)}


@router.get("/learner/topics")
def get_learner_topics() -> Dict[str, Any]:
    return {"topics": learner_store.topic_mastery()}


@router.get("/usage")
def get_usage() -> Dict[str, Any]:
    return learner_store.usage_summary()
