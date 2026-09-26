"""FastAPI routes for the local web app: sessions, analysis, progress, topics.

A session is one activity's take: a monologue, or a picture description
(the picture is stored next to the audio). Either can be spoken (Deepgram
transcript) or, for a picture, typed - a typed text becomes transcript.txt
verbatim and never goes to Deepgram. Spoken drills («60 секунд», shadowing)
are sessions too, but are measured from Deepgram's word timings instead of
being analysed by Claude.

Kept deliberately thin: this module wires HTTP to the existing per-session
flat-file model (app.utils) plus the API integrations (app.transcriber,
app.analyzer, app.exercise_sets). Their construction goes through small factory
dependencies so tests can substitute fakes via app.dependency_overrides,
mirroring the client_factory pattern already used inside those modules.
"""

from __future__ import annotations

import datetime as dt
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import (
    config,
    dictation,
    dictation_store,
    exercise_sets,
    learner_model,
    learner_store,
    progress_store,
    speech_drills,
    utils,
)
from app.analyzer import (
    ANALYSIS_SCHEMA_VERSION,
    AnalysisError,
    ClaudeAnalyzer,
    ImageInput,
    MissingAnthropicApiKeyError,
)
from app.dictation_translation import LessonTranslator
from app.exercise_sets import ExerciseSetGenerator, TranslationAnswer
from app.transcriber import DeepgramTranscriber, TranscriptionError
from app.utils import Session
from app.youtube import (
    NoSubtitlesError,
    YouTubeError,
    YouTubeFetcher,
    create_fetcher,
    video_id_from_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

TranscriberFactory = Callable[[config.LanguageProfile], DeepgramTranscriber]
AnalyzerFactory = Callable[[], ClaudeAnalyzer]
GeneratorFactory = Callable[[], ExerciseSetGenerator]
FetcherFactory = Callable[[], YouTubeFetcher]
TranslatorFactory = Callable[[], LessonTranslator]


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


def get_generator_factory() -> GeneratorFactory:
    def factory() -> ExerciseSetGenerator:
        return ExerciseSetGenerator(config.get_anthropic_api_key())

    return factory


def get_fetcher_factory() -> FetcherFactory:
    """The YouTube import of a dictation lesson (yt-dlp; no API key needed)."""

    def factory() -> YouTubeFetcher:
        return create_fetcher()

    return factory


def get_translator_factory() -> TranslatorFactory:
    """Haiku for the dictation's translation task (splitting and reviewing)."""

    def factory() -> LessonTranslator:
        return LessonTranslator(config.get_anthropic_api_key())

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
        "kind": session.kind,
        "input_mode": session.input_mode,
    }


def _has_image(session: Session) -> bool:
    return session.image_path is not None and session.image_path.is_file()


async def _read_image_upload(image: Optional[UploadFile]) -> tuple:
    """The uploaded picture's bytes and sniffed media type, or a 400."""
    data = await image.read() if image is not None else b""
    if not data:
        raise HTTPException(status_code=400, detail="Добавьте картинку, которую нужно описать.")
    if len(data) > config.IMAGE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Картинка слишком большая (больше 5 МБ).")
    media_type = utils.sniff_image_type(data)
    if media_type is None:
        raise HTTPException(
            status_code=400,
            detail="Этот формат картинки не поддерживается (нужен JPEG, PNG, WebP или GIF).",
        )
    return data, media_type


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
            "speech_drill" if session.is_drill else "transcription",
            profile.key,
            audio_seconds,
            session_id=session.id,
        )
        if session.is_drill:
            # Logged before session.json says "done", so a screen that reloads
            # the moment polling ends already sees the attempt.
            try:
                learner_store.record_speech_drill(session)
            except Exception:  # the transcript is saved; the score is extra
                logger.exception("Could not log the drill result of %s", session.id)
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


def _drill_meta(
    kind: str,
    prompt_index: Optional[int],
    series: Optional[str],
    source_session_id: Optional[str],
    passage: Optional[int],
) -> Dict[str, Any]:
    """What a drill take practises, checked before anything is saved."""
    if kind == config.KIND_TALK:
        if prompt_index is None or not 0 <= prompt_index < len(config.SPEAKING_PROMPTS):
            raise HTTPException(status_code=400, detail="Unknown speaking prompt.")
        if series is None:
            return {"prompt_index": prompt_index, "series": None, "round": 1}
        first = _load_session_or_404(series)
        if first.kind != config.KIND_TALK or (first.drill or {}).get("series") != first.id:
            raise HTTPException(status_code=400, detail="Not the first take of a talk series.")
        return {
            "prompt_index": (first.drill or {}).get("prompt_index", prompt_index),
            "series": first.id,
            "round": learner_store.talk_round(first.id),
        }
    if source_session_id is None or passage is None:
        raise HTTPException(status_code=400, detail="Name the recording and passage to read.")
    source = _load_session_or_404(source_session_id)
    found = next(
        (p for p in learner_store.shadowing_passages(source.id) if p["index"] == passage), None
    )
    if found is None:
        raise HTTPException(status_code=404, detail="Unknown passage.")
    return {"source_session_id": source.id, "passage": passage, "reference": found["text"]}


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
        "speaking_prompts": [
            {"index": index, "question": question, "hint": hint}
            for index, (question, hint) in enumerate(config.SPEAKING_PROMPTS)
        ],
        "image_max_side": config.IMAGE_MAX_SIDE_PX,
        "talk_seconds": config.TALK_SECONDS,
        "talk_rounds": config.TALK_ROUNDS,
    }


# --------------------------------------------------------------- /sessions
@router.post("/sessions", status_code=202)
async def create_session(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    kind: str = Form(config.KIND_MONOLOGUE),
    image: Optional[UploadFile] = File(None),
    language: str = Form(config.DEFAULT_LANGUAGE_KEY),
    client_duration_seconds: float = Form(0.0),
    mime_type: str = Form(""),
    prompt_index: Optional[int] = Form(None),
    series: Optional[str] = Form(None),
    source_session_id: Optional[str] = Form(None),
    passage: Optional[int] = Form(None),
    transcriber_factory: TranscriberFactory = Depends(get_transcriber_factory),
) -> Dict[str, Any]:
    """A new take: an audio `file` (transcribed in the background) or a typed
    `text` (done at once). A picture description also carries its `image`;
    a «60 секунд» take its `prompt_index` and, from round 2 on, its `series`;
    a shadowing take the `source_session_id` and `passage` it reads."""
    profile = config.profile_by_key(language)
    if kind not in config.SESSION_KINDS:
        raise HTTPException(status_code=400, detail="Unknown activity kind.")
    if (file is None) == (text is None):
        raise HTTPException(status_code=400, detail="Send either a recording or a text.")
    drill: Optional[Dict[str, Any]] = None
    if kind in config.DRILL_KINDS:
        if file is None:
            raise HTTPException(status_code=400, detail="A spoken drill needs a recording.")
        drill = _drill_meta(kind, prompt_index, series, source_session_id, passage)
        if kind == config.KIND_SHADOWING:
            # The passage is English whatever the source recording's language.
            profile = config.profile_by_key(config.DEFAULT_LANGUAGE_KEY)
    if text is not None:
        if not text.strip():
            raise HTTPException(status_code=400, detail="The text is empty.")
        if len(text) > config.TYPED_TEXT_MAX_CHARS:
            raise HTTPException(status_code=400, detail="The text is too long.")
    image_upload = None
    if kind == config.KIND_PICTURE:
        image_upload = await _read_image_upload(image)
    elif image is not None:
        raise HTTPException(status_code=400, detail="Only a picture description takes an image.")
    audio_bytes = await file.read() if file is not None else b""
    if file is not None and not audio_bytes:
        raise HTTPException(status_code=400, detail="The uploaded recording is empty.")

    try:
        session = utils.create_session()
    except OSError as exc:
        logger.exception("Could not create the session directory")
        raise HTTPException(status_code=500, detail=f"Could not create the recordings folder: {exc}")

    session.kind = kind
    session.language_key = profile.key
    session.drill = drill
    if drill is not None and kind == config.KIND_TALK and drill["series"] is None:
        # Round 1 of a talk starts its own series, named after itself.
        session.drill = {**drill, "series": session.id}
    try:
        if image_upload is not None:
            image_bytes, media_type = image_upload
            session.image_filename = (
                config.IMAGE_FILENAME_STEM + config.IMAGE_EXTENSIONS_BY_MEDIA_TYPE[media_type]
            )
            session.image_path.write_bytes(image_bytes)
        if text is not None:
            # Typed by hand: the text is the transcript, stored exactly as typed.
            session.input_mode = config.INPUT_TEXT
            utils.write_transcript(session, text, profile)
            session.transcript = text
            session.status = utils.STATUS_DONE
            utils.write_session_meta(session)
            return {"session_id": session.id, "status": session.status}
        session.audio_filename = "audio" + config.extension_for_mime(
            mime_type or file.content_type
        )
        session.duration_seconds = client_duration_seconds
        session.audio_path.write_bytes(audio_bytes)
    except OSError as exc:
        logger.exception("Could not save the upload")
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
        "image_url": f"/api/sessions/{session.id}/image" if _has_image(session) else None,
        "analysis": _read_analysis(session),
        "drill": session.drill,
        "speech": (
            learner_store.speech_report(session)
            if session.status == utils.STATUS_DONE
            else None
        ),
    }


@router.get("/sessions/{session_id}/audio")
def get_session_audio(session_id: str) -> FileResponse:
    session = _load_session_or_404(session_id)
    if not session.audio_path.is_file():
        raise HTTPException(status_code=404, detail="No audio recorded for this session.")
    media_type = mimetypes.guess_type(str(session.audio_path))[0] or "application/octet-stream"
    return FileResponse(session.audio_path, media_type=media_type)


@router.get("/sessions/{session_id}/image")
def get_session_image(session_id: str) -> FileResponse:
    session = _load_session_or_404(session_id)
    if not _has_image(session):
        raise HTTPException(status_code=404, detail="No picture for this session.")
    media_type = mimetypes.guess_type(str(session.image_path))[0] or "application/octet-stream"
    return FileResponse(session.image_path, media_type=media_type)


class AnalyzeRequest(BaseModel):
    force: bool = False


@router.post("/sessions/{session_id}/analyze")
def analyze_session(
    session_id: str,
    body: AnalyzeRequest = AnalyzeRequest(),
    analyzer_factory: AnalyzerFactory = Depends(get_analyzer_factory),
) -> Dict[str, Any]:
    session = _load_session_or_404(session_id)
    if session.is_drill:
        raise HTTPException(status_code=400, detail="Речевые тренажёры не анализируются Claude.")
    if not session.transcript:
        raise HTTPException(status_code=400, detail="This session has no transcript yet.")

    cached = _read_analysis(session)
    if cached is not None and not body.force:
        return {"session_id": session.id, "analysis": cached, "progress_updated": False}

    picture = session.kind == config.KIND_PICTURE
    image = None
    if picture:
        if not _has_image(session):
            raise HTTPException(status_code=400, detail="The picture of this session is missing.")
        image_bytes = session.image_path.read_bytes()
        media_type = utils.sniff_image_type(image_bytes)
        if media_type is None:
            raise HTTPException(status_code=400, detail="The session's picture is unreadable.")
        image = ImageInput(data=image_bytes, media_type=media_type)
    typed = session.input_mode == config.INPUT_TEXT

    analyzer = analyzer_factory()
    profile = config.profile_by_key(session.language_key)
    try:
        result = analyzer.analyze(
            session.transcript, profile, session.duration_seconds, image=image, typed=typed
        )
    except MissingAnthropicApiKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # A separate purpose, so the real price of each activity shows in the usage log.
    usage_record = learner_store.record_claude_usage(
        "picture_analysis" if picture else "analysis",
        result.model,
        result.usage,
        session_id=session.id,
    )
    analysis_payload: Dict[str, Any] = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "created_at": dt.datetime.now().isoformat(),
        "model": result.model,
        "effort": result.effort,
        "language": session.language_key,
        "kind": session.kind,
        "input_mode": session.input_mode,
        "summary": result.summary,
        "strengths": result.strengths,
        "issues": [issue.model_dump() for issue in result.issues],
        "vocabulary": [item.model_dump() for item in result.vocabulary],
        "improved_version": result.improved_version,
        "takeaways": [item.model_dump() for item in result.takeaways],
        "scores": result.scores.model_dump() if result.scores else None,
        "overall_score": result.overall_score,
        **(
            {
                "not_mentioned": [item.model_dump() for item in result.not_mentioned],
                "scene_vocabulary": [item.model_dump() for item in result.scene_vocabulary],
            }
            if picture
            else {}
        ),
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
        if learner_model.is_retired(item):
            continue
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


class CardCheckRequest(BaseModel):
    """The answer to one card sentence; `drill` is the index the card showed."""

    drill: int = Field(ge=0, le=50)
    answer: str = Field(default="", max_length=1000)


@router.post("/learner/cards/{item_id}/check")
def check_card(
    item_id: str,
    body: CardCheckRequest,
    generator_factory: GeneratorFactory = Depends(get_generator_factory),
) -> Dict[str, Any]:
    """Check a translation card's answer: Claude (Haiku) decides whether it
    says the sentence correctly with the construction the card trains.

    Only the check - the attempt is logged by POST /learner/attempts as for
    any card, so the learner can still overrule a verdict. An empty answer or
    one that matches the reference needs no call, and a verdict on the same
    answer to the same sentence is reused from the cache.
    """
    bank = learner_store.load_item_bank()
    item = bank["items"].get(item_id)
    if item is None or learner_model.is_retired(item):
        raise HTTPException(status_code=404, detail="Unknown card.")
    drills = (item.get("content") or {}).get("drills") or []
    if item.get("kind") != learner_model.KIND_FIX or body.drill >= len(drills):
        raise HTTPException(status_code=400, detail="This card has no such sentence.")
    drill = drills[body.drill]
    answer = body.answer.strip()
    if not answer:
        return {"correct": False, "comment": "Ответа нет.", "corrected": "", "graded_by": "empty"}
    if learner_model.answer_matches(answer, [drill["english"]]):
        return {"correct": True, "comment": "", "corrected": answer, "graded_by": "match"}
    cached = learner_store.cached_card_verdict(drill["russian"], answer)
    if cached is not None:
        return {**cached, "graded_by": "cache"}

    content = item.get("content") or {}
    pending = TranslationAnswer(
        exercise_id=item_id,
        russian=drill["russian"],
        reference=drill["english"],
        focus=content.get("focus") or f"the fix of: {content.get('correction', '')}",
        answer=answer,
    )
    topic = learner_store.topic_info(item.get("topic") or "other")
    try:
        graded = generator_factory().grade(topic, [pending])
    except MissingAnthropicApiKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    usage = learner_store.record_claude_usage("card_grading", graded.call.model, graded.call.usage)
    verdict = graded.verdicts.get(item_id)
    if verdict is None:
        raise HTTPException(status_code=502, detail="Claude не проверил ответ. Попробуйте ещё раз.")
    learner_store.store_card_verdict(
        item_id, drill["russian"], answer, verdict, model=graded.call.model, cost=usage["cost_usd"]
    )
    return {**verdict, "graded_by": "claude", "cost_usd": usage["cost_usd"]}


@router.get("/learner/today")
def get_learner_today() -> Dict[str, Any]:
    """The «Сегодня» workout: cards, a live activity, dictation, extras."""
    return learner_store.today_workout()


@router.get("/learner/history")
def get_learner_history() -> Dict[str, Any]:
    """Exercise results per day, newest first."""
    return {"days": learner_store.activity_history()}


@router.get("/learner/topics")
def get_learner_topics() -> Dict[str, Any]:
    return {"topics": learner_store.topic_mastery()}


@router.get("/usage")
def get_usage() -> Dict[str, Any]:
    return learner_store.usage_summary()


# ----------------------------------------------------------------- /speech
@router.get("/speech/passages")
def get_shadowing_passages() -> Dict[str, Any]:
    """Passages to read aloud, and which one to take next."""
    passages = learner_store.shadowing_passages()
    return {"passages": passages, "next": speech_drills.pick_passage(passages)}


@router.get("/speech/talks")
def get_talk_series() -> Dict[str, Any]:
    """«60 секунд» series with every round's measurements, newest first."""
    today = dt.date.today()
    return {
        "series": learner_store.talk_series(),
        "prompt_index": speech_drills.talk_prompt_index(today),
    }


# -------------------------------------------------------------- /dictation
# Listening dictation (Stage 7): a YouTube video becomes a lesson - its audio
# plus the sentences of its own subtitle track, typed word by word. Free by
# construction: the dictation itself calls neither Deepgram nor Claude, and a
# video without usable subtitles is refused rather than transcribed (decided
# 2026-09-20). Only the optional translation task after it costs Haiku calls
# (cut into parts, review a translation), each on an explicit click.
_LESSON_ID_PATTERN = r"^[A-Za-z0-9_-]{6,20}$"


class ImportLessonRequest(BaseModel):
    url: str = Field(min_length=6, max_length=500)
    language: str = Field(default=config.DEFAULT_LANGUAGE_KEY, max_length=10)


class SentenceResultRequest(BaseModel):
    """One dictated sentence as the browser finished it.

    The answers are graded again here, so what is stored never depends on the
    client; `error_chars` is taken as given because a mistyped character can
    only be seen while it is typed.
    """

    sentence: int = Field(ge=0, le=10_000)
    answers: List[str] = Field(default_factory=list, max_length=200)
    hints: List[int] = Field(default_factory=list, max_length=200)
    error_chars: int = Field(default=0, ge=0, le=100_000)
    seconds: float = Field(default=0.0, ge=0.0, le=36_000.0)


def _lesson_or_404(lesson_id: str) -> Dict[str, Any]:
    # The id is a path segment and becomes a directory name: only YouTube's
    # own id shape is accepted (see the session id guard above).
    if not re.match(_LESSON_ID_PATTERN, lesson_id or ""):
        raise HTTPException(status_code=400, detail="Invalid lesson id.")
    lesson = dictation_store.load_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Unknown lesson.")
    return lesson


def _import_lesson(
    lesson: Dict[str, Any], url: str, languages: List[str], fetcher_factory: FetcherFactory
) -> None:
    """Runs in a background thread; lesson.json is the result, like a session."""
    try:
        fetched = fetcher_factory().fetch(url, dictation_store.lesson_dir(lesson["id"]), languages)
        sentences = dictation.lesson_sentences(fetched.subtitles)
        if not sentences:
            raise NoSubtitlesError("Субтитры пустые — по этому видео не собрать диктант.")
        dictation_store.finish_import(lesson, fetched, sentences)
        logger.info("Dictation lesson %s ready: %d sentences", lesson["id"], len(sentences))
    except YouTubeError as exc:
        logger.error("Dictation import failed for %s: %s", lesson["id"], exc)
        dictation_store.fail_import(lesson, str(exc))
    except Exception as exc:  # pragma: no cover - unexpected
        logger.exception("Unexpected error importing %s", lesson["id"])
        dictation_store.fail_import(lesson, f"Непредвиденная ошибка: {exc}")


@router.post("/dictation/lessons", status_code=202)
def import_lesson(
    body: ImportLessonRequest,
    background_tasks: BackgroundTasks,
    fetcher_factory: FetcherFactory = Depends(get_fetcher_factory),
) -> Dict[str, Any]:
    """Start importing a YouTube video; poll the lesson until it is ready."""
    try:
        video_id = video_id_from_url(body.url)
    except YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    profile = config.profile_by_key(body.language)
    languages = list(
        config.DICTATION_SUBTITLE_LANGUAGES.get(profile.key)
        or config.DICTATION_SUBTITLE_LANGUAGES[config.DEFAULT_LANGUAGE_KEY]
    )
    existing = dictation_store.load_lesson(video_id)
    if existing is not None and existing.get("status") == config.LESSON_STATUS_READY:
        # Already imported: hand it back instead of downloading it twice.
        return {"lesson_id": video_id, "status": existing["status"]}
    try:
        lesson = dictation_store.start_import(video_id, body.url, profile.key)
    except OSError as exc:
        logger.exception("Could not create the lesson directory")
        raise HTTPException(status_code=500, detail=f"Could not create the lesson folder: {exc}")
    background_tasks.add_task(_import_lesson, lesson, body.url, languages, fetcher_factory)
    return {"lesson_id": lesson["id"], "status": lesson["status"]}


@router.get("/dictation/lessons")
def list_lessons() -> Dict[str, Any]:
    """Every lesson with its progress, newest import first."""
    return {
        "lessons": [dictation_store.lesson_summary(l) for l in dictation_store.list_lessons()],
        "max_minutes": config.DICTATION_MAX_SECONDS // 60,
        "daily_target": config.DICTATION_DAILY_SENTENCES,
        "done_today": dictation_store.done_today(),
    }


@router.get("/dictation/lessons/{lesson_id}")
def get_lesson(lesson_id: str) -> Dict[str, Any]:
    """The lesson page: every sentence with its tokens, timings and last result."""
    return dictation_store.lesson_payload(_lesson_or_404(lesson_id))


@router.get("/dictation/lessons/{lesson_id}/audio")
def get_lesson_audio(lesson_id: str) -> FileResponse:
    lesson = _lesson_or_404(lesson_id)
    path = dictation_store.audio_path(lesson)
    if path is None:
        raise HTTPException(status_code=404, detail="No audio for this lesson.")
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    # FileResponse answers Range requests, which is what seeking to a
    # sentence needs.
    return FileResponse(path, media_type=media_type)


@router.post("/dictation/lessons/{lesson_id}/results", status_code=201)
def post_lesson_result(lesson_id: str, body: SentenceResultRequest) -> Dict[str, Any]:
    """Log one dictated sentence (append-only) and hand back its grading."""
    lesson = _lesson_or_404(lesson_id)
    sentences = lesson.get("sentences") or []
    if body.sentence >= len(sentences):
        raise HTTPException(status_code=404, detail="Unknown sentence.")
    sentence = sentences[body.sentence]
    graded = dictation.grade_sentence(sentence["text"], body.answers, body.hints)
    record = dictation_store.append_result(
        lesson_id,
        {
            "sentence": body.sentence,
            "error_chars": body.error_chars,
            "seconds": round(body.seconds, 1),
            **graded,
        },
    )
    results = dictation_store.lesson_results(lesson_id)
    return {"result": record, "progress": dictation.lesson_progress(len(sentences), results)}


class TranslationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)


@router.post("/dictation/lessons/{lesson_id}/parts")
def split_lesson(
    lesson_id: str,
    force: bool = False,
    translator_factory: TranslatorFactory = Depends(get_translator_factory),
) -> Dict[str, Any]:
    """Cut a lesson into translation parts (one Haiku call, on a click).

    Cached like an analysis: a lesson that is already cut - or short enough to
    be one part - is returned as is unless `force` asks for a new cut.
    """
    lesson = _lesson_or_404(lesson_id)
    if not force and dictation_store.resolve_parts(lesson) is not None:
        return dictation_store.translation_payload(lesson)
    sentences = lesson.get("sentences") or []
    try:
        result = translator_factory().split([s["text"] for s in sentences])
    except MissingAnthropicApiKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    learner_store.record_claude_usage(
        "dictation_split", result.call.model, result.call.usage, session_id=lesson["id"]
    )
    parts = dictation.plan_parts(len(sentences), result.starts)
    dictation_store.save_parts(lesson, parts, model=result.call.model, starts=result.starts)
    logger.info("Lesson %s cut into %d parts", lesson["id"], len(parts))
    return dictation_store.translation_payload(lesson)


@router.post("/dictation/lessons/{lesson_id}/parts/{part}/translation", status_code=201)
def post_part_translation(
    lesson_id: str,
    part: int,
    body: TranslationRequest,
    translator_factory: TranslatorFactory = Depends(get_translator_factory),
) -> Dict[str, Any]:
    """Store the learner's translation of one part and have Haiku review it.

    The text is saved before Claude is asked, so a missing key or a failed
    call never loses it; the same text sent again reuses the stored review.
    """
    lesson = _lesson_or_404(lesson_id)
    parts = dictation_store.resolve_parts(lesson)
    if parts is None:
        raise HTTPException(status_code=409, detail="Сначала разбейте урок на части.")
    if not 0 <= part < len(parts):
        raise HTTPException(status_code=404, detail="Unknown part.")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Перевод пустой.")

    previous = dictation_store.latest_translations(lesson["id"]).get(part)
    if previous and previous.get("text") == text and previous.get("review"):
        return {"translation": previous, "cached": True}

    try:
        result = translator_factory().review(
            dictation_store.part_source(lesson, parts[part]),
            text,
            subtitle_language=lesson.get("subtitle_language"),
        )
    except (MissingAnthropicApiKeyError, AnalysisError) as exc:
        dictation_store.append_translation(
            lesson["id"], {"part": part, "text": text, "review": None}
        )
        status = 400 if isinstance(exc, MissingAnthropicApiKeyError) else 502
        raise HTTPException(status_code=status, detail=str(exc))
    usage = learner_store.record_claude_usage(
        "dictation_translation", result.call.model, result.call.usage, session_id=lesson["id"]
    )
    record = dictation_store.append_translation(
        lesson["id"],
        {
            "part": part,
            "text": text,
            "review": result.review,
            "model": result.call.model,
            "cost_usd": usage["cost_usd"],
        },
    )
    return {"translation": record, "cached": False}


@router.delete("/dictation/lessons/{lesson_id}")
def delete_lesson(lesson_id: str) -> Dict[str, Any]:
    """Delete a lesson with its audio and its results - always a deliberate click."""
    lesson = _lesson_or_404(lesson_id)
    dictation_store.delete_lesson(lesson["id"])
    return {"deleted": lesson["id"]}


@router.get("/dictation/stats")
def get_dictation_stats() -> Dict[str, Any]:
    """Cross-lesson numbers and the «сложные слова» list for «Прогресс»."""
    return dictation_store.stats()


# ------------------------------------------------------ /practice/sets
# AI exercise sets (Stage 5): generated only on an explicit click, stored in
# data/practice/, redone for free. Gaps and fixes are checked in the browser;
# free translations are graded by Claude in one call when the set is handed in.
_SET_ID_PATTERN = r"^set-[0-9]{8}-[0-9]{6}(-[0-9]{1,3})?$"


def _set_or_404(set_id: str) -> Dict[str, Any]:
    # The id becomes a file name: only the exact generated shape is accepted.
    if not re.fullmatch(_SET_ID_PATTERN, set_id):
        raise HTTPException(status_code=400, detail="Invalid set id.")
    exercise_set = learner_store.load_set(set_id)
    if exercise_set is None:
        raise HTTPException(status_code=404, detail="Unknown exercise set.")
    return exercise_set


def _check_set_topic(topic: str) -> None:
    if topic not in progress_store.TOPIC_TAXONOMY:
        raise HTTPException(status_code=404, detail="Unknown topic.")
    if not learner_model.set_topic_allowed(topic):
        raise HTTPException(
            status_code=400, detail="По этой теме наборы упражнений не составляются."
        )


def _set_payload(exercise_set: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    return {
        "set": {k: v for k, v in exercise_set.items() if k != "verdicts"},
        "summary": learner_store.set_summary(exercise_set),
        **extra,
    }


class CreateSetRequest(BaseModel):
    topic: str = Field(pattern=_SLUG_PATTERN)
    force: bool = False


@router.get("/practice/sets")
def list_exercise_sets(topic: Optional[str] = None) -> Dict[str, Any]:
    """Sets (newest first) plus what one more would cost, for the button."""
    if topic is not None:
        _check_set_topic(topic)
    return {
        "topic": topic,
        "sets": [learner_store.set_summary(s) for s in learner_store.list_sets(topic)],
        "cost_estimate_usd": learner_store.set_cost_estimate(),
        "anthropic_configured": config.get_anthropic_api_key() is not None,
    }


@router.post("/practice/sets")
def create_exercise_set(
    body: CreateSetRequest,
    generator_factory: GeneratorFactory = Depends(get_generator_factory),
) -> Dict[str, Any]:
    """Generate a set on a topic (a paid call). An unstarted set on the topic
    is handed back instead unless `force` - a second click costs nothing."""
    _check_set_topic(body.topic)
    if not body.force:
        waiting = learner_store.unstarted_set(body.topic)
        if waiting is not None:
            return _set_payload(waiting, reused=True)

    topic = learner_store.topic_info(body.topic)
    generator = generator_factory()
    try:
        result = generator.generate(
            topic,
            learner_store.set_seeds(body.topic),
            learner_store.set_avoid_sentences(body.topic),
        )
    except MissingAnthropicApiKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    set_id = learner_store.new_set_id()
    usage = learner_store.record_claude_usage(
        "exercise_set", result.call.model, result.call.usage, set_id=set_id
    )
    exercise_set = {
        "schema_version": learner_store.SET_SCHEMA_VERSION,
        "id": set_id,
        "topic": body.topic,
        "language": "en",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "intro": result.intro,
        "exercises": result.exercises,
        "generation": {
            "model": result.call.model,
            "effort": result.call.effort,
            "request_id": result.call.request_id,
            "usage": {**result.call.usage, "cost_usd": usage["cost_usd"]},
        },
        "runs": [],
    }
    learner_store.save_set(exercise_set)
    return _set_payload(exercise_set, reused=False)


@router.get("/practice/sets/{set_id}")
def get_exercise_set(set_id: str) -> Dict[str, Any]:
    return _set_payload(_set_or_404(set_id))


class SetAnswer(BaseModel):
    """One answer. `correct` is the browser's verdict for a gap or a fix (the
    learner may overrule an exact-match miss) or the learner's own grade of a
    translation when Claude could not grade it; left out, the server decides."""

    exercise_id: str = Field(pattern=r"^ex[0-9]{1,3}$")
    answer: str = Field(default="", max_length=1000)
    correct: Optional[bool] = None


class SubmitSetRequest(BaseModel):
    answers: List[SetAnswer] = Field(max_length=50)
    context: Optional[str] = Field(default=None, pattern=_SLUG_PATTERN)


@router.post("/practice/sets/{set_id}/submit")
def submit_exercise_set(
    set_id: str,
    body: SubmitSetRequest,
    generator_factory: GeneratorFactory = Depends(get_generator_factory),
) -> Dict[str, Any]:
    """Hand in a whole set: grade what is left, log the run, make cards."""
    exercise_set = _set_or_404(set_id)
    exercises = {e["id"]: e for e in exercise_set.get("exercises") or []}
    answers = {a.exercise_id: a for a in body.answers}
    if len(answers) != len(body.answers) or set(answers) != set(exercises):
        raise HTTPException(status_code=400, detail="Answer every exercise of the set once.")

    results: List[Dict[str, Any]] = []
    pending: List[TranslationAnswer] = []
    for exercise_id, exercise in exercises.items():
        given = answers[exercise_id]
        text = given.answer.strip()
        result: Dict[str, Any] = {
            "exercise_id": exercise_id,
            "type": exercise["type"],
            "answer": text,
        }
        if exercise["type"] != exercise_sets.TYPE_TRANSLATE:
            matched = learner_model.answer_matches(text, exercise.get("accept") or [])
            result["correct"] = matched if given.correct is None else given.correct
            result["graded_by"] = "browser" if given.correct is not None else "match"
        elif given.correct is not None:
            result.update(correct=given.correct, graded_by="self")
        elif not text:
            result.update(
                correct=False,
                graded_by="empty",
                comment="Ответа нет.",
                corrected=exercise["reference"],
            )
        elif learner_model.answer_matches(text, [exercise["reference"]]):
            result.update(correct=True, graded_by="match", comment="", corrected=text)
        else:
            cached = learner_store.cached_verdict(exercise_set, exercise_id, text)
            if cached is not None:
                result.update(cached, graded_by="cache")
            else:
                pending.append(
                    TranslationAnswer(
                        exercise_id=exercise_id,
                        russian=exercise["russian"],
                        reference=exercise["reference"],
                        focus=exercise.get("focus", ""),
                        answer=text,
                    )
                )
        results.append(result)

    grading: Optional[Dict[str, Any]] = None
    if pending:
        generator = generator_factory()
        try:
            graded = generator.grade(learner_store.topic_info(exercise_set["topic"]), pending)
        except MissingAnthropicApiKeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except AnalysisError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        usage = learner_store.record_claude_usage(
            "exercise_grading", graded.call.model, graded.call.usage, set_id=set_id
        )
        grading = {
            "model": graded.call.model,
            "request_id": graded.call.request_id,
            "usage": {**graded.call.usage, "cost_usd": usage["cost_usd"]},
        }
        missing = [p.exercise_id for p in pending if p.exercise_id not in graded.verdicts]
        if missing:
            raise HTTPException(
                status_code=502, detail="Claude проверил не все ответы. Попробуйте ещё раз."
            )
        for result in results:
            verdict = graded.verdicts.get(result["exercise_id"])
            if verdict is not None and "graded_by" not in result:
                result.update(verdict, graded_by="claude")

    recorded = learner_store.record_set_run(
        set_id, results, context=body.context, grading=grading
    )
    return {
        "set_id": set_id,
        "run": recorded["run"],
        "new_cards": recorded["new_cards"],
        "summary": recorded["set"],
    }
