"""HTTP routes. Thin: orchestration lives in service.py."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .formats import (
    TOPIC_VOCABULARY,
    DifficultyLockedError,
    FormatConfig,
    UnknownStyleError,
    load_styles,
    resolve,
    unsupported_topics,
)
from .library import QuestionLibrary, build_library
from .llm import FakeLLM, LLMAccountError, llm_status, telemetry
from .models import Difficulty, GatedQuestion, Language, TestCase
from .progress import GenerationCancelled, Reporter, Run, registry
from .scoring import QuestionScore, score_session
from .service import (
    ExecutionReport,
    execute_against,
    grade_submission,
    obtain_question,
    remaining_seconds,
)
from .settings import (
    SETTABLE,
    environment_defaults,
    from_environment,
    get_settings,
)
from .storage import Storage
from .tutor import (
    SYSTEM_PROMPT,
    build_context,
    is_available,
    probe_reference,
    probe_tool_schema,
    run_reference,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ schemas
class SetupState(BaseModel):
    configured: bool
    base_url: str
    model: str
    has_key: bool


class SettingsPatch(BaseModel):
    """A partial update. Every field optional; only what is sent is changed.

    The bounds matter more than they look. Settings itself has none, and this
    API is unauthenticated -- without them a single PUT of
    generation_attempts=100000 is a way to spend someone else's money.
    """

    model_config = {"extra": "forbid"}

    llm_base_url: str | None = Field(default=None, min_length=1, max_length=500)
    llm_model: str | None = Field(default=None, min_length=1, max_length=200)
    llm_timeout_s: float | None = Field(default=None, gt=0, le=600)
    llm_max_retries: int | None = Field(default=None, ge=0, le=10)
    llm_first_token_s: float | None = Field(default=None, gt=0, le=600)
    llm_idle_s: float | None = Field(default=None, gt=0, le=600)
    # Empty is meaningful: it switches the fallback off.
    llm_fallback_model: str | None = Field(default=None, max_length=200)
    llm_reasoning_effort: Literal["default", "none", "low", "medium", "high"] | None = None
    generation_strategy: Literal["module", "monolithic"] | None = None
    generation_attempts: int | None = Field(default=None, ge=1, le=10)
    repair_rounds: int | None = Field(default=None, ge=0, le=10)
    tool_call_budget: int | None = Field(default=None, ge=0, le=32)
    check_statement_sufficiency: bool | None = None
    tutor_tool_budget: int | None = Field(default=None, ge=0, le=32)
    tutor_message_cap: int | None = Field(default=None, ge=1, le=500)
    library_url: str | None = Field(default=None, max_length=500)


class SettingsState(BaseModel):
    """Everything the settings page needs, and nothing secret.

    `values` carries the effective setting; `sources` says whether each came
    from the environment or was saved here, so "Reset to environment" can be
    offered only where it would do something.
    """

    values: dict[str, Any]
    sources: dict[str, str]          # field -> 'environment' | 'saved' | 'default'
    #: False when the database is in-memory, so saves will not survive a restart.
    persistent: bool
    db_path: str
    #: The key is never returned; these describe it without revealing it.
    has_key: bool
    key_hint: str                    # last four characters, or ''
    #: True when the key came from the setup form rather than the environment,
    #: so the UI can say it will not survive a restart.
    key_from_session: bool
    configured: bool
    #: What the app is actually talking to. `configured` and `has_key` can both
    #: be true while every question comes from a fixture, which is precisely
    #: the confusion this exists to end.
    llm_status: str                  # 'live' | 'offline' | 'unconfigured'
    llm_reason: str


class StyleInfo(BaseModel):
    id: str
    label: str
    description: str
    difficulty_locked: bool
    summary: str
    presets: list[dict[str, Any]]


#: A paste, not an essay. Long enough for a real question with examples.
MAX_IMPORT_CHARS = 8000


class CreateSessionRequest(BaseModel):
    style: str
    preset: str | None = None
    topics: list[str] = Field(default_factory=list)
    freeform: str = ""
    difficulty: Difficulty | None = None
    language: Language = Language.PYTHON
    #: Prose describing a question to adapt. Distinct from `freeform`, which
    #: steers the topic within a generated question.
    import_text: str = Field(default="", max_length=MAX_IMPORT_CHARS)
    #: A client-minted id for watching this request's progress while it runs.
    progress_id: str | None = Field(default=None, max_length=64)


class CaseInput(BaseModel):
    """One test case as the candidate edited it.

    `assert_expected` distinguishes "I left the expected value blank, just show
    me what my code returns" from "I expect literally null" -- the two are
    indistinguishable if you only look at `expected`.
    """

    args: dict[str, Any] = Field(default_factory=dict)
    expected: Any = None
    assert_expected: bool = False


#: Every case is executed, so an unbounded list is a way to tie up the runner.
#: Well above anything a person edits by hand.
MAX_CASES = 50


class ChatRequest(BaseModel):
    """One message to the tutor.

    Deliberately carries only the message and the editor state. Everything the
    tutor knows about the question is assembled server-side, so a request cannot
    name a different question or inject its own instructions.
    """

    message: str = Field(min_length=1, max_length=4000)
    source: str = ""
    language: Language = Language.PYTHON


class ComputeRequest(BaseModel):
    """Ask what the intended solution returns for one set of arguments."""

    args: dict[str, Any] = Field(default_factory=dict)
    language: Language = Language.PYTHON


class RunRequest(BaseModel):
    source: str
    language: Language = Language.PYTHON
    #: The full case list from the Testcase tab, replacing the question's
    #: examples. Omitted means "use the question's examples as authored".
    cases: list[CaseInput] | None = Field(default=None, max_length=MAX_CASES)
    #: Retained for older clients: appended after `cases`.
    extra_cases: list[dict[str, Any]] = Field(
        default_factory=list, max_length=MAX_CASES
    )


# ------------------------------------------------------------------- setup
@router.get("/setup", response_model=SetupState)
async def get_setup() -> SetupState:
    s = get_settings()
    return SetupState(
        configured=s.configured, base_url=s.llm_base_url,
        model=s.llm_model, has_key=bool(s.llm_api_key),
    )


@router.post("/setup", response_model=SetupState)
async def post_setup(
    request: Request, payload: dict[str, str] = Body(...)  # noqa: B008
) -> SetupState:
    """Accept an endpoint and key from the setup screen.

    Held in process memory only -- the key is never written to the database, so
    it cannot leak onto a mounted volume.
    """
    s = get_settings()
    if base_url := payload.get("base_url", "").strip():
        s.llm_base_url = base_url
    if model := payload.get("model", "").strip():
        s.llm_model = model
    if (key := payload.get("api_key", "").strip()):
        s.llm_api_key = key
        # Records where it came from, which is what lets it beat
        # FREETCODER_FAKE_LLM. See llm.build_client.
        s.key_from_session = True
    elif "api_key" in payload:
        # An explicit empty key clears the session override and falls back to
        # whatever the environment provides.
        s.llm_api_key = os.environ.get("FREETCODER_LLM_API_KEY", "")
        s.key_from_session = False
    request.app.state.llm = None  # force a rebuild with the new settings
    return await get_setup()


# ---------------------------------------------------------------- settings
def _settings_state(request: Request) -> SettingsState:
    s = get_settings()
    saved: set[str] = getattr(request.app.state, "saved_settings", set())
    key = s.llm_api_key
    status, reason = llm_status(s)
    return SettingsState(
        values={name: getattr(s, name) for name in sorted(SETTABLE)},
        sources={
            name: "saved" if name in saved
            else "environment" if from_environment(name)
            else "default"
            for name in sorted(SETTABLE)
        },
        persistent=bool(s.db_path),
        db_path=s.db_path,
        has_key=bool(key),
        key_hint=key[-4:] if len(key) >= 4 else "",
        key_from_session=s.key_from_session,
        configured=s.configured,
        llm_status=status,
        llm_reason=reason,
    )


@router.get("/settings", response_model=SettingsState)
async def get_app_settings(request: Request) -> SettingsState:
    return _settings_state(request)


@router.put("/settings", response_model=SettingsState)
async def put_app_settings(
    request: Request, patch: SettingsPatch
) -> SettingsState:
    """Apply and persist a partial settings update.

    Saving still works with an in-memory database -- the change applies for the
    process lifetime, which is genuinely useful. Silently discarding what
    someone typed would be worse; the response says `persistent: false` and the
    page tells them.
    """
    s = get_settings()
    changes = patch.model_dump(exclude_none=True)
    if not changes:
        return _settings_state(request)

    library_changed = (
        "library_url" in changes and changes["library_url"] != s.library_url
    )
    for name, value in changes.items():
        setattr(s, name, value)

    store = request.app.state.store
    if s.db_path:
        await store.save_settings(
            {k: json.dumps(v) for k, v in changes.items()}
        )
    saved: set[str] = getattr(request.app.state, "saved_settings", set())
    request.app.state.saved_settings = saved | set(changes)

    # Two clients read these, not one. The LLM client is rebuilt lazily via
    # this null; the library client is otherwise only ever built in lifespan,
    # so without this a library_url change is inert until restart.
    request.app.state.llm = None
    if library_changed:
        request.app.state.library = build_library(s)
    return _settings_state(request)


@router.delete("/settings/{field}", response_model=SettingsState)
async def reset_app_setting(request: Request, field: str) -> SettingsState:
    """Forget a saved value so the environment (or the default) applies again."""
    if field not in SETTABLE:
        raise HTTPException(404, f"unknown setting {field!r}")
    s = get_settings()
    await request.app.state.store.delete_setting(field)
    setattr(s, field, environment_defaults()[field])
    saved: set[str] = getattr(request.app.state, "saved_settings", set())
    request.app.state.saved_settings = saved - {field}
    request.app.state.llm = None
    if field == "library_url":
        request.app.state.library = build_library(s)
    return _settings_state(request)


# ----------------------------------------------------------------- formats
@router.get("/formats", response_model=list[StyleInfo])
async def list_formats() -> list[StyleInfo]:
    out: list[StyleInfo] = []
    for sid, style in load_styles().items():
        out.append(
            StyleInfo(
                id=sid,
                label=style.config.label,
                description=style.config.description.strip(),
                difficulty_locked=style.config.difficulty_locked,
                summary=style.config.summary(),
                presets=[p.model_dump(mode="json") for p in style.presets],
            )
        )
    return out


@router.get("/topics")
async def list_topics() -> dict[str, Any]:
    return {
        "topics": list(TOPIC_VOCABULARY),
        "unsupported": {t: r for t, r in
                        unsupported_topics(list(TOPIC_VOCABULARY)).items()},
    }


@router.post("/formats/preview")
async def preview_format(payload: CreateSessionRequest) -> dict[str, Any]:
    """Resolve the picker's three tiers so the UI can show the summary card."""
    config = _resolve_or_400(payload)
    return {
        "config": config.model_dump(mode="json"),
        "summary": config.summary(),
        "warnings": list(unsupported_topics(payload.topics).values()),
    }


def _resolve_or_400(payload: CreateSessionRequest) -> FormatConfig:
    try:
        return resolve(
            payload.style, payload.preset, payload.topics,
            payload.freeform, difficulty=payload.difficulty,
            import_text=payload.import_text,
        )
    except DifficultyLockedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except UnknownStyleError as exc:
        raise HTTPException(404, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- sessions

def _spent(reporter: Reporter, provider_seconds: float) -> str:
    """The last line of the log: where the time actually went.

    Measured, our own share -- lint, type-check, the probe, the gate running
    the reference and the brute force -- is about two seconds. Saying so turns
    "this is slow" from a complaint about the app into a fact about the
    provider, or occasionally the other way round, which is the point.
    """
    total = reporter.elapsed
    ours = max(0.0, total - provider_seconds)
    return (
        f"took {_clock(total)}: {_clock(provider_seconds)} waiting on the model, "
        f"{_clock(ours)} checking it"
    )


def _clock(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"


@router.post("/sessions")
async def create_session(request: Request, payload: CreateSessionRequest) -> dict[str, Any]:
    config = _resolve_or_400(payload)
    store = request.app.state.store
    client = request.app.state.get_llm()

    if isinstance(client, FakeLLM) and client.exhausted:
        raise HTTPException(
            428, "No LLM endpoint configured. Set a base URL and API key first."
        )

    # Questions are generated lazily; only the first is needed to start.
    reporter = Reporter(registry, payload.progress_id)
    if payload.progress_id:
        registry.start(payload.progress_id)

    # Time the provider separately from ourselves. Without this the only
    # honest answer to "why did that take eighteen minutes?" was to read
    # container logs with a stopwatch, which is what it took to find a single
    # call that ran for 857 seconds against a timeout that could not expire.
    with telemetry.collecting() as calls:
        registry.attach(payload.progress_id, calls)
        try:
            obtained = await obtain_question(
                store, client, config, 0,
                language=payload.language, report_to=reporter,
            )
        except GenerationCancelled:
            registry.finish(
                payload.progress_id, "cancelled", provider_seconds=calls.seconds
            )
            raise HTTPException(409, "Generation cancelled.") from None
        except LLMAccountError as exc:
            # Say what is actually wrong. This used to come back as "the model
            # could not produce a question", which sends people debugging
            # prompts when the fix is on the provider's billing page.
            registry.finish(
                payload.progress_id, "failed", provider_seconds=calls.seconds
            )
            raise HTTPException(402, str(exc)) from None

        if obtained is None:
            reporter(_spent(reporter, calls.seconds), kind="warn")
            registry.finish(
                payload.progress_id, "failed", provider_seconds=calls.seconds
            )
            raise HTTPException(
                502, "The model could not produce a question that passed validation. "
                     "Try again, or pick a different concentration.",
            )
        reporter(_spent(reporter, calls.seconds), kind="ok")
    registry.finish(payload.progress_id, "accepted", provider_seconds=calls.seconds)
    qid, _ = obtained
    sid = await store.create_session(config, [qid])
    return await get_session(request, sid)


@router.get("/sessions/{sid}")
async def get_session(request: Request, sid: str) -> dict[str, Any]:
    store = request.app.state.store
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(404, "no such session")
    config = session["config"]
    return {
        "id": sid,
        "config": config.model_dump(mode="json"),
        "summary": config.summary(),
        "question_count": config.session.question_count,
        "current_index": session["current_index"],
        "answered": len(session["question_ids"]),
        "remaining_seconds": remaining_seconds(session, config),
        "finished": session["finished_at"] is not None,
    }


@router.get("/sessions/{sid}/questions/{index}")
async def get_question(request: Request, sid: str, index: int) -> dict[str, Any]:
    """Fetch (generating on demand) the question at `index`."""
    store = request.app.state.store
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(404, "no such session")
    config = session["config"]
    if not 0 <= index < config.session.question_count:
        raise HTTPException(404, "question index out of range")

    ids: list[str] = session["question_ids"]
    if index >= len(ids):
        # Fill every gap up to `index`: a candidate may jump straight to Q3
        # without opening Q2, and the list is positional.
        while len(ids) <= index:
            obtained = await obtain_question(
                store, request.app.state.get_llm(), config, len(ids), exclude_ids=ids
            )
            if obtained is None:
                raise HTTPException(502, "could not generate a valid question")
            ids.append(obtained[0])
        await store.db.execute(
            "UPDATE sessions SET question_ids = ? WHERE id = ?",
            (json.dumps(ids), sid),
        )
        await store.db.commit()

    gated = await store.get_question(ids[index])
    if gated is None:
        raise HTTPException(404, "question missing from store")

    sig = gated.question.signature_for(gated.language)
    q = gated.question
    return {
        "index": index,
        "title": q.title,
        "difficulty": q.difficulty.value,
        "topics": q.topics,
        "statement_md": q.statement_md,
        "constraints_md": q.constraints_md,
        # Hints are withheld entirely on formats that give no support.
        "hint_md": q.hint_md if config.generation.give_hints else None,
        "complexity_target": q.complexity_target,
        "language": gated.language.value,
        "languages": [lang.value for lang in config.environment.languages],
        "signatures": [
            {
                "language": s.language.value,
                "function_name": s.function_name,
                "scaffold": s.scaffold,
            }
            for s in q.signatures
            if s.language in config.environment.languages
        ],
        # Retained for the default language so existing clients keep working.
        "scaffold": sig.scaffold if sig else "",
        "function_name": sig.function_name if sig else "",
        "visible_tests": [t.model_dump(mode="json") for t in q.visible_tests],
        "hidden_test_count": len(gated.hidden_tests),
        "source": gated.source,
        "import_notes": q.import_notes,
        # The answers to what the prose leaves open. Each one's `probe` was
        # executed against the reference before the question was accepted, so
        # these are checked facts rather than the model's assurances -- and the
        # candidate is the person who needs them. They were reaching the tutor
        # and nobody else.
        "clarifications": [
            {"question": c.question, "answer": c.answer}
            for c in q.clarifications
        ],
        "remaining_seconds": remaining_seconds(session, config),
    }


@router.post("/sessions/{sid}/questions/{index}/run")
async def run_visible(
    request: Request, sid: str, index: int, payload: RunRequest
) -> dict[str, Any]:
    """Run against the visible examples only. Never scored."""
    store = request.app.state.store
    session, gated = await _load(store, sid, index)

    if payload.cases is None:
        cases = list(gated.question.visible_tests)
        judged = [True] * len(cases)
    else:
        cases = [TestCase(args=c.args, expected=c.expected) for c in payload.cases]
        judged = [c.assert_expected for c in payload.cases]

    for extra in payload.extra_cases:
        cases.append(
            TestCase(args=extra.get("args", {}), expected=extra.get("expected"))
        )
        judged.append("expected" in extra)

    _require_offered(session["config"], payload.language)
    try:
        report = execute_against(
            payload.source, gated, cases, hidden=False,
            config=session["config"], language=payload.language,
            judged=judged,
        )
    except Exception as exc:  # noqa: BLE001 - a harness fault must not 500
        log.exception("run failed for session %s question %d", sid, index)
        return _error_payload(exc)

    await store.record_attempt(
        session_id=sid, question_index=index, language=payload.language,
        source=payload.source, kind="run", verdict=report.verdict.value,
    )
    return _report_payload(report, cases, scored=False)


@router.post("/sessions/{sid}/questions/{index}/submit")
async def submit(
    request: Request, sid: str, index: int, payload: RunRequest
) -> dict[str, Any]:
    """Run against visible + hidden cases and score the attempt."""
    store = request.app.state.store
    session, gated = await _load(store, sid, index)
    config = session["config"]

    _require_offered(config, payload.language)
    elapsed = time.time() - session["started_at"]
    try:
        report = grade_submission(
            payload.source, gated, config, elapsed_s=elapsed, language=payload.language
        )
    except Exception as exc:  # noqa: BLE001 - a harness fault must not 500
        log.exception("submit failed for session %s question %d", sid, index)
        return _error_payload(exc)

    await store.record_attempt(
        session_id=sid, question_index=index, language=payload.language,
        source=payload.source, kind="submit", verdict=report.verdict.value,
        score=report.score.total if report.score else None,
    )
    cases = list(gated.question.visible_tests) + list(gated.hidden_tests)
    payload_out = _report_payload(report, cases, scored=True)
    payload_out["reference_solution"] = _reference(gated)
    payload_out["total_ms"] = round(report.total_ms, 2)
    payload_out["ratio"] = report.ratio
    # Say what the reference *is*: "1.3x the reference" alone means little.
    payload_out["reference_growth"] = gated.measured_growth
    payload_out["complexity_target"] = gated.question.complexity_target or ""

    # Rank against everyone else, when this question is in the library and the
    # solution actually worked. A failure here is silent by design.
    library_id = await store.library_id_for(ids_index(session, index))
    if report.ratio is not None and library_id:
        library: QuestionLibrary = request.app.state.library
        standing = await library.record_timing(
            library_id, payload.language.value, report.ratio
        )
        if standing is not None:
            payload_out["percentile"] = standing.percentile
            payload_out["samples"] = standing.samples
            payload_out["enough_samples"] = standing.enough_samples
    return payload_out


@router.post("/sessions/{sid}/questions/{index}/skip")
async def skip(request: Request, sid: str, index: int) -> dict[str, Any]:
    store = request.app.state.store
    session, gated = await _load(store, sid, index)
    if not session["config"].session.allow_skip:
        raise HTTPException(409, "this format does not allow skipping")
    await store.record_attempt(
        session_id=sid, question_index=index, language=gated.language,
        source="", kind="skip", verdict="skipped", score=0.0,
    )
    return {"skipped": True, "reference_solution": _reference(gated)}


@router.post("/sessions/{sid}/questions/{index}/compute")
async def compute_expected(
    request: Request, sid: str, index: int, payload: ComputeRequest
) -> dict[str, Any]:
    """What the intended solution returns for these arguments.

    Deliberately on demand rather than automatic: filling every case in would
    turn any question into "type an input, read the answer", while a button
    keeps probing one awkward edge case a single click away.

    Returns the value only. This is the same boundary the tutor's probe uses,
    and it goes through the same function.
    """
    store = request.app.state.store
    session, gated = await _load(store, sid, index)
    _require_offered(session["config"], payload.language)

    # Refuse arguments the question says cannot occur: computing an answer for
    # impossible input would teach the wrong thing.
    for constraint in gated.question.constraints:
        value = payload.args.get(constraint.name)
        if value is None:
            continue
        if (why := _violates(value, constraint)) is not None:
            raise HTTPException(
                422, f"{constraint.name} {why}, which the question's constraints rule out"
            )

    ok, value, error = run_reference(gated, payload.args, payload.language)
    if not ok:
        raise HTTPException(
            422, f"the intended solution could not run on those arguments: {error}"
        )
    return {"expected": value}


def _violates(value: Any, constraint: Any) -> str | None:
    from .generate.gate import _violation

    return _violation(value, constraint)


# ------------------------------------------------------------------- tutor
@router.get("/sessions/{sid}/questions/{index}/chat")
async def chat_state(request: Request, sid: str, index: int) -> dict[str, Any]:
    """Whether the tutor is available here, and what has been said so far."""
    store = request.app.state.store
    session, _ = await _load(store, sid, index)
    attempts = await store.attempts_for(sid, index)
    attempted = any(a["kind"] in ("submit", "skip") for a in attempts)

    available, reason = is_available(session["config"], attempted=attempted)
    return {
        "available": available,
        "reason": reason,
        "messages": await store.chat_history(sid, index),
    }


@router.post("/sessions/{sid}/questions/{index}/chat")
async def chat(
    request: Request, sid: str, index: int, payload: ChatRequest
) -> StreamingResponse:
    """Ask the tutor. Replies stream, because a silent pause reads as broken."""
    store = request.app.state.store
    session, gated = await _load(store, sid, index)
    settings = get_settings()

    attempts = await store.attempts_for(sid, index)
    attempted = any(a["kind"] in ("submit", "skip") for a in attempts)
    available, reason = is_available(session["config"], attempted=attempted)
    if not available:
        raise HTTPException(409, reason)

    if await store.chat_message_count(sid) >= settings.tutor_message_cap:
        raise HTTPException(429, "This session has reached its tutor message limit.")

    last_report = _last_report(attempts)
    context = build_context(
        gated, session["config"],
        source=payload.source, language=payload.language,
        last_report=last_report, attempts=attempts,
    )
    history = await store.chat_history(sid, index)
    await store.add_chat_message(sid, index, "user", payload.message)

    messages = [
        {"role": "user", "content": f"{context}\n\n---\n\nThey ask: {payload.message}"}
        if not history else {"role": "user", "content": payload.message}
    ]
    if history:
        messages = [
            {"role": "user", "content": context},
            *[{"role": m["role"], "content": m["content"]} for m in history],
            {"role": "user", "content": payload.message},
        ]

    client = request.app.state.get_llm()

    async def events() -> AsyncIterator[str]:
        reply: list[str] = []
        try:
            async for event in client.stream_chat(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=[probe_tool_schema()],
                dispatch=lambda name, args: (
                    probe_reference(gated, args.get("args", {}))
                    if name == "probe_reference" else f"unknown tool {name!r}"
                ),
                tool_budget=settings.tutor_tool_budget,
            ):
                if event.get("type") == "token":
                    reply.append(str(event.get("text", "")))
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001 - a broken stream must not 500
            log.exception("tutor stream failed for %s", sid)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)[:200]})}\n\n"
        finally:
            if reply:
                await store.add_chat_message(sid, index, "assistant", "".join(reply))
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def _last_report(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The most recent run or submit, as the tutor should see it."""
    for attempt in reversed(attempts):
        if attempt["kind"] in ("run", "submit"):
            return {"verdict": attempt["verdict"], "stderr": attempt.get("detail") or "",
                    "cases": [], "first_failure": None}
    return None


# ---------------------------------------------------------------- progress
@router.get("/progress/{run_id}", response_model=Run)
async def get_progress(run_id: str) -> Run:
    """What a generation in flight is doing.

    Polled rather than streamed: the payload is tiny, it survives a reload, and
    a second of latency is irrelevant against an operation measured in tens of
    seconds.
    """
    run = registry.inspect(run_id)
    if run is None:
        raise HTTPException(404, "no such run")
    return run


@router.post("/progress/{run_id}/cancel")
async def cancel_progress(run_id: str) -> dict[str, bool]:
    """Ask a generation to stop.

    Cooperative: the flag is checked between steps, so a request already in
    flight to the model finishes first. The UI says so rather than appearing to
    hang.
    """
    return {"cancelled": registry.cancel(run_id)}


# ----------------------------------------------------------------- library
@router.get("/library/status")
async def library_status(request: Request) -> dict[str, Any]:
    library: QuestionLibrary = request.app.state.library
    return {"configured": library.configured, "reachable": await library.health()}


@router.get("/library/questions")
async def library_questions(
    request: Request,
    style: str | None = None,
    difficulty: str | None = None,
    language: str | None = None,
    topic: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Browse saved questions. An unreachable library reads as an empty one."""
    library: QuestionLibrary = request.app.state.library
    questions, total = await library.search(
        style=style, difficulty=difficulty, language=language,
        topic=topic, limit=limit,
    )
    return {"questions": questions, "total": total}


@router.post("/sessions/{sid}/questions/{index}/publish")
async def publish_question(
    request: Request, sid: str, index: int, allow_import: bool = False
) -> dict[str, Any]:
    """Save a question to the shared library.

    `allow_import` is required for a question adapted from supplied text, so
    republishing someone else's question is deliberate rather than incidental.
    """
    store = request.app.state.store
    session, gated = await _load(store, sid, index)
    library: QuestionLibrary = request.app.state.library

    qid, error = await library.publish(
        gated,
        style=session["config"].generation.style,
        allow_import_publish=allow_import,
    )
    if error is not None:
        # A refusal on provenance grounds is a decision the candidate can
        # override; an unreachable library is not. Different codes, because the
        # UI offers different affordances.
        status = 409 if gated.source == "imported" and not allow_import else 503
        raise HTTPException(status, error.message)
    await store.set_library_id(ids_index(session, index), qid)
    return {"id": qid, "title": gated.question.title}


@router.post("/sessions/from-library/{qid}")
async def session_from_library(request: Request, qid: str) -> dict[str, Any]:
    """Start a session from a saved question, skipping generation entirely."""
    store = request.app.state.store
    library: QuestionLibrary = request.app.state.library

    gated, error = await library.fetch(qid)
    if gated is None:
        raise HTTPException(404, error.message if error else "no such question")

    config = resolve("leetcode")
    config.environment.languages = [s.language for s in gated.question.signatures]
    local_id = await store.cache_question(f"library:{qid}", gated)
    await store.set_library_id(local_id, qid)
    sid = await store.create_session(config, [local_id])
    return await get_session(request, sid)


@router.get("/sessions/{sid}/results")
async def results(request: Request, sid: str) -> dict[str, Any]:
    store = request.app.state.store
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(404, "no such session")
    config = session["config"]

    best: dict[int, float] = {}
    for attempt in await store.attempts_for(sid):
        if attempt["kind"] in ("submit", "skip") and attempt["score"] is not None:
            idx = attempt["question_index"]
            best[idx] = max(best.get(idx, 0.0), attempt["score"])

    scores = [
        QuestionScore(correctness=best.get(i, 0.0), performance=0.0, speed=0.0,
                      total=best.get(i, 0.0), solved=best.get(i, 0.0) >= 1.0)
        for i in range(config.session.question_count)
    ]
    session_score = score_session(scores, config)
    await store.finish_session(sid)
    return {
        "score": session_score.native,
        "scale": session_score.scale_label,
        "fraction": session_score.fraction,
        "solved": session_score.solved_count,
        "question_count": config.session.question_count,
        "per_question": [s.model_dump(mode="json") for s in scores],
    }


# ------------------------------------------------------------------ shared
async def _load(
    store: Storage, sid: str, index: int
) -> tuple[dict[str, Any], GatedQuestion]:
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(404, "no such session")
    ids = session["question_ids"]
    if index >= len(ids):
        raise HTTPException(404, "question not started yet")
    gated = await store.get_question(ids[index])
    if gated is None:
        raise HTTPException(404, "question missing from store")
    return session, gated


def _require_offered(config: FormatConfig, language: Language) -> None:
    """Reject a language this format does not offer, rather than failing oddly later."""
    if language not in config.environment.languages:
        raise HTTPException(
            409,
            f"{language.value} is not offered by this format; available: "
            f"{[lang.value for lang in config.environment.languages]}",
        )


def ids_index(session: dict[str, Any], index: int) -> str:
    """The local question id at `index` of this session."""
    return str(session["question_ids"][index])


def _error_payload(exc: Exception) -> dict[str, Any]:
    """Report an unexpected fault in the results pane instead of as a bare 500.

    The candidate loses nothing this way: their code is still in the editor and
    they can see what went wrong.
    """
    return {
        "verdict": "internal_error",
        "passed": 0,
        "total": 0,
        "stderr": f"{type(exc).__name__}: {exc}",
        "first_failure": None,
        "cases": [],
    }


def _reference(gated: GatedQuestion) -> str:
    sig = gated.question.signature_for(gated.language)
    return sig.reference_solution if sig else ""


def _report_payload(
    report: ExecutionReport, cases: list[TestCase], *, scored: bool
) -> dict[str, Any]:
    visible_limit = 0 if scored else len(cases)
    out: dict[str, Any] = {
        "verdict": report.verdict.value,
        "passed": report.passed,
        "total": len(report.outcomes),
        "stderr": report.stderr[:4000],
        "first_failure": report.first_failure,
        "cases": [
            {
                "index": o.index,
                "passed": o.passed,
                "hidden": o.hidden,
                "ms": o.ms,
                "over_budget": o.over_budget,
                "stdout": o.stdout,
                "judged": o.judged,
                "actual": o.actual,
                # Hidden inputs stay hidden; only the first failure is revealed,
                # and only as a counterexample.
                "args": cases[o.index].args if o.index < visible_limit else None,
                "expected": cases[o.index].expected if o.index < visible_limit else None,
            }
            for o in report.outcomes
        ],
    }
    if scored and report.score is not None:
        out["score"] = report.score.model_dump(mode="json")
    return out
