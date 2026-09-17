"""Runtime configuration, all overridable by environment variable."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

#: Fields the settings page may write, and which are persisted.
#:
#: Everything absent from this set is refused over HTTP. `db_path` and
#: `static_dir` would be a file-disclosure primitive on an unauthenticated API;
#: `fake_llm` would let a caller silently swap the real model for canned
#: fixtures; `llm_api_key` is environment-only on purpose -- see the note on
#: the field itself.
SETTABLE: frozenset[str] = frozenset({
    "llm_base_url",
    "llm_model",
    "llm_timeout_s",
    "llm_max_retries",
    "llm_first_token_s",
    "llm_idle_s",
    "llm_fallback_model",
    "llm_reasoning_effort",
    "generation_attempts",
    "repair_rounds",
    "tool_call_budget",
    "check_statement_sufficiency",
    "generation_strategy",
    "tutor_tool_budget",
    "tutor_message_cap",
    "library_url",
})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FREETCODER_", extra="ignore")

    #: Any OpenAI-compatible endpoint: OpenRouter, Ollama, vLLM, LM Studio.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    #: Environment-only, never persisted and never returned by the API.
    #:
    #: Put it in a .env file, which compose already substitutes. Keeping the
    #: credential out of the database means the settings API cannot be turned
    #: into an exfiltrator by repointing llm_base_url at a hostile host, and
    #: nothing lands in plaintext on a mounted volume.
    llm_api_key: str = ""
    llm_model: str = "anthropic/claude-sonnet-4.5"
    #: Per *request*, not per question. Measured, a module call that works
    #: takes about 100s, so this is roughly a 3x ceiling rather than a guess;
    #: OpenRouter's own per-endpoint `latency_last_30m` and
    #: `throughput_last_30m` give a per-model figure if a tighter one is
    #: wanted. A call that overruns is no longer re-sent by the client, only
    #: by the loop in `generate_module`, which reports what it is doing.
    llm_timeout_s: float = 300.0
    #: Retries for a provider that was momentarily unable -- a 429, a 5xx, a
    #: reply with no choices in it. **Not** for a timeout: re-sending a request
    #: that just spent a full deadline is the least promising use of the next
    #: one, and doing it silently is what made a single step sit at 677
    #: seconds. Note this multiplies with `generation_attempts`, `repair_rounds`
    #: and the module strategy's own per-stage tries, which are separate
    #: budgets that no one layer can see.
    llm_max_retries: int = 3

    #: Generation calls stream, so a stuck model is visible early: no content
    #: or reasoning token within this many seconds and the call is abandoned
    #: rather than waited on for the full `llm_timeout_s`.
    llm_first_token_s: float = 30.0
    #: Silence allowed *between* tokens once a reply has started.
    llm_idle_s: float = 60.0
    #: A second model on the same endpoint, tried once when the first stalls,
    #: times out or keeps failing. Empty disables it.
    llm_fallback_model: str = ""
    #: How hard the model thinks before answering. "default" sends nothing and
    #: leaves it to the model. Honoured unevenly: kimi-k2.5 goes from ~600
    #: reasoning tokens to none; mimo-v2.5 produced *more* when told "none".
    llm_reasoning_effort: Literal["default", "none", "low", "medium", "high"] = "default"

    #: Empty path means in-memory: the container has no persistent filesystem
    #: unless the user opts into a volume.
    db_path: str = ""
    static_dir: Path | None = None
    dev: bool = False

    #: How a question is asked for.
    #:
    #: "module" asks for a Python module implementing a fixed interface and
    #: validates it by lint, type-check and execution. "monolithic" asks for a
    #: 14-field JSON payload. Measured over four days, monolithic was accepted
    #: 0 times in 3 and module 4 in 6 -- every failure we fixed was a
    #: serialisation failure rather than a reasoning one, because code inside a
    #: data format has to be escaped and models escape it badly.
    #:
    #: Monolithic stays reachable because it is the only path that can adapt
    #: supplied prose (`import_text`), and as an escape hatch.
    generation_strategy: Literal["module", "monolithic"] = "module"

    #: How many times to regenerate before giving up on a question slot.
    generation_attempts: int = 4

    #: Patch-and-re-gate rounds per generated question, before regenerating.
    #: Bounded so a confused model cannot spend unlimited tokens or time.
    #:
    #: Both strategies use it, differently: the monolithic path patches the
    #: broken artifact, the module path hands the gate's complaint back with
    #: the module that earned it and asks for the file again.
    repair_rounds: int = 3

    #: How many times the model may execute code while repairing one question.
    #: Monolithic strategy only -- the module path's critics (ruff, pyright,
    #: the probe) already run the code, so there is nothing to grant.
    tool_call_budget: int = 6

    #: Have a second model solve each question from its statement alone and
    #: check the result against the oracle. Roughly doubles generation cost;
    #: it is the only check that validates what the candidate actually reads.
    check_statement_sufficiency: bool = True

    #: How many times the tutor may probe the reference in one reply.
    tutor_tool_budget: int = 4
    #: Questions per session, so a runaway client cannot spend without bound.
    tutor_message_cap: int = 60

    #: The question library service. Empty disables publishing and browsing;
    #: unreachable degrades the same way. Practice never depends on it.
    library_url: str = ""

    #: Serve recorded fixture questions instead of calling a provider. Exists so
    #: the UI and the end-to-end tests can run with no API key and no network.
    #: Deliberately explicit -- an unset key must fail loudly, not silently
    #: fall back to canned questions.
    fake_llm: bool = False

    #: Set when a key arrives through POST /api/setup rather than the
    #: environment. It lets such a key override fake_llm, so a container
    #: started with FREETCODER_FAKE_LLM=1 can still be pointed at a real
    #: provider from the UI. Not settable over the settings API -- it is a fact
    #: about where the key came from, not a preference.
    key_from_session: bool = False

    @property
    def configured(self) -> bool:
        """False until the user supplies a key on the setup screen."""
        return bool(self.llm_api_key and self.llm_base_url)

    def apply_saved(self, raw: dict[str, str]) -> set[str]:
        """Overlay JSON-encoded saved values; returns the fields that took.

        A saved value beats the environment. The reverse rule is defensible in
        general -- environment is the operator's channel -- but not here:
        docker-compose sets FREETCODER_LLM_BASE_URL and FREETCODER_LLM_MODEL
        unconditionally, so env-wins would lock the two fields this page most
        exists to edit in every Docker deployment.

        A value that no longer coerces (a renamed field, a type changed between
        versions) is skipped with a log line. Refusing to boot because an old
        row is unreadable would be a worse failure than ignoring it.
        """
        applied: set[str] = set()
        for name, encoded in raw.items():
            if name not in SETTABLE:
                log.warning("ignoring saved setting %r: not settable", name)
                continue
            try:
                value: Any = TypeAdapter(
                    type(self).model_fields[name].annotation
                ).validate_python(json.loads(encoded))
            except (ValidationError, ValueError, KeyError) as err:
                log.warning("ignoring saved setting %r: %s", name, err)
                continue
            setattr(self, name, value)
            applied.add(name)
        return applied


def env_name(field: str) -> str:
    """The environment variable a field reads from."""
    return f"FREETCODER_{field.upper()}"


def from_environment(field: str) -> bool:
    """Whether this field has a non-empty value in the environment.

    Non-empty, not merely present: compose writes `${VAR:-}` for several
    fields, so a presence check would report every one of them as set.
    """
    return bool(os.environ.get(env_name(field), "").strip())


def environment_defaults() -> dict[str, Any]:
    """A Settings built from the environment alone, ignoring anything saved.

    Used to answer "what would this field be if I forgot the saved value?",
    which is what the settings page's Reset offers.
    """
    return Settings().model_dump()


@lru_cache
def get_settings() -> Settings:
    return Settings()
