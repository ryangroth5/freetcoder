"""Generate a question by asking for a Python module.

Every serialisation failure measured over four days came from putting code
inside a data format: whole functions returned on one line because their
newlines had to be escaped, nested lists arriving with fields missing,
positional arguments where a mapping was demanded. None of it was a model
failing to author a coding question.

So this asks for a module implementing `interface/question_interface.py`, which
the model is handed verbatim. Code is code. Validation is procedural rather
than parsed: lint it, type-check it, import it, call it -- and hand back
whatever the failing step said, which is the loop these models are tuned for.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from ..formats import FormatConfig
from ..llm import LLMClient, LLMError, telemetry
from ..models import (
    Clarification,
    Difficulty,
    GateOutcome,
    GeneratedQuestion,
    Language,
    ParamConstraint,
    Signature,
    TestCase,
)
from ..progress import NULL_REPORTER, Reporter
from ..runner import Limits, Verdict
from ..runner.sandbox import Workspace, execute
from .delimited import ParseError, parse_sections
from .gate import validate_question
from .module_probe import PROBE
from .pipeline import GenerationAttempt, GenerationResult, _gated, _read_prompt
from .scenarios import pick
from .staged import StagedResult, StageOutcome, _brief, reference_fault

INTERFACE = Path(__file__).parent / "interface" / "question_interface.py"

#: Node, and pyright is Node: V8 cannot start under an address-space rlimit.
#: The same lesson the TypeScript adapter learned.
TOOL_LIMITS = Limits(
    wall_seconds=60.0, cpu_seconds=55, memory_mb=1024, limit_address_space=False
)
#: The probe's output is ours, not a candidate's, and it carries the statement
#: plus every generated case. The default 64KB cap truncated it mid-JSON, which
#: read as "the module produced no result" -- a true statement about a payload
#: that was in fact complete.
PROBE_LIMITS = Limits(
    wall_seconds=30.0, cpu_seconds=25, memory_mb=512, max_output_bytes=4_000_000
)

#: Obvious destructiveness. The container is disposable and the sandbox drops
#: privileges, blocks the network and caps everything, so this is a smoke alarm
#: rather than a lock -- it catches a model that has misunderstood the task,
#: not an attacker.
ALARMING = re.compile(
    r"\b(shutil\.rmtree|os\.remove|os\.unlink|os\.system|subprocess|socket|"
    r"urllib|requests\.|__import__\s*\(\s*['\"]os)"
)


@dataclass
class ProbeResult:
    """What the module turned out to contain, or why it did not work."""

    ok: bool
    step: str = ""
    detail: str = ""
    payload: dict[str, object] | None = None


def alarming_source(source: str) -> str:
    found = ALARMING.findall(source)
    if not found:
        return ""
    return (
        "the module reaches outside itself (" + ", ".join(sorted(set(found))[:3])
        + "). A question needs no filesystem, network or subprocesses."
    )


def _tool(argv: list[str], source: str) -> tuple[int, str]:
    """Run a checker over the module in a throwaway workspace."""
    with Workspace() as ws:
        ws.write("solution.py", source)
        result = execute([*argv, "solution.py"], ws.path, limits=TOOL_LIMITS)
    # exit_code is None when the sandbox killed it; treat that as a failure so
    # a checker that times out does not silently read as "clean".
    code = result.exit_code if result.exit_code is not None else 1
    return (code, (result.stdout or "") + (result.stderr or ""))


def lint_fault(source: str) -> str:
    """Real errors only -- undefined names, syntax, unused imports.

    Not style. `--isolated` because a throwaway workspace must not inherit the
    project's own ruleset, and E9,F because a question rejected for import
    ordering would be rejected for nothing.
    """
    code, out = _tool(["ruff", "check", "--isolated", "--select", "E9,F",
                       "--output-format", "json"], source)
    if code == 0:
        return ""
    try:
        issues = json.loads(out or "[]")
    except json.JSONDecodeError:
        return f"ruff could not read the file: {out.strip()[:300]}"
    if not issues:
        return ""
    lines = [
        f"line {i.get('location', {}).get('row', '?')}: "
        f"{i.get('code')} {i.get('message')}"
        for i in issues[:5]
    ]
    return "ruff found errors:\n" + "\n".join(lines)


def typecheck_fault(source: str) -> str:
    """pyright, for the mistakes that parse but cannot run."""
    code, out = _tool(["pyright", "--outputjson"], source)
    if code == 0:
        return ""
    try:
        report = json.loads(out[out.index("{"):]) if "{" in out else {}
    except (json.JSONDecodeError, ValueError):
        return ""    # pyright itself failed; not the question's fault
    errors = [
        d for d in report.get("generalDiagnostics", [])
        if d.get("severity") == "error"
    ]
    if not errors:
        return ""
    lines = [
        f"line {d.get('range', {}).get('start', {}).get('line', 0) + 1}: "
        f"{d.get('message', '').splitlines()[0]}"
        for d in errors[:5]
    ]
    return "pyright found type errors:\n" + "\n".join(lines)


def probe_module(source: str, *, wanted: int) -> ProbeResult:
    """Import the module in the sandbox and put it through its paces."""
    with Workspace() as ws:
        ws.write("solution.py", source)
        ws.write("_probe.py", PROBE)
        result = execute(
            ["python3", "-I", "_probe.py"], ws.path,
            limits=PROBE_LIMITS, env={"FTC_WANTED": str(wanted)},
        )
        # Read before the workspace is torn down.
        written = Path(ws.path) / "_result.json"
        record = None
        if written.exists():
            try:
                record = json.loads(written.read_text())
            except (json.JSONDecodeError, OSError):
                record = None

    if record is not None:
        if record.get("ok"):
            return ProbeResult(ok=True, payload=record)
        return ProbeResult(
            ok=False, step=str(record.get("step", "")),
            detail=str(record.get("detail", "")),
        )

    if result.verdict is Verdict.TIMEOUT:
        return ProbeResult(
            ok=False, step="timeout",
            detail="the module did not finish; solution or generate_cases is too slow",
        )

    # Say what it *did* produce. "no result" alone gives nothing to act on,
    # and the interesting case is a module that printed something instead.
    noise = (result.stdout or "").strip()
    detail = (result.stderr or "").strip() or (
        f"the module printed {noise[:200]!r} instead of a result"
        if noise else "the module produced no output at all"
    )
    return ProbeResult(ok=False, step="probe", detail=detail[:600])


def module_fault(
    source: str, *, wanted: int, report_to: Reporter = NULL_REPORTER
) -> tuple[str, dict[str, object] | None]:
    """Every critic in order, cheapest first. Returns ("", payload) when good.

    Each critic announces itself. They are seconds apart at best and tens of
    seconds apart at worst, and a log that says nothing between "writing the
    question module" and the next attempt reads as a hang.
    """
    if fault := alarming_source(source):
        return fault, None
    report_to("checking it with ruff")
    if fault := lint_fault(source):
        return fault, None
    report_to("checking types with pyright")
    if fault := typecheck_fault(source):
        return fault, None

    report_to("running the module against its own cases")
    probed = probe_module(source, wanted=wanted)
    if not probed.ok:
        return f"{probed.step}: {probed.detail}", None
    return "", probed.payload


def scaffold_from(function_name: str, parameters: list[str]) -> str:
    """Starter code, taken from the signature rather than asked for.

    One fewer artifact the model can get wrong, and it cannot disagree with the
    solution it was derived from.
    """
    return f"def {function_name}({', '.join(parameters)}):\n    pass\n"


def extract_code(reply: str) -> str:
    """Code, with any Markdown fence stripped.

    Models fence code even when asked for a bare file. That is cosmetic and
    worth tolerating rather than rejecting over.

    Any info string, not just `python`: a translation comes back fenced
    ```javascript, which an earlier version did not match, so the backticks
    were handed to Node and came back as a SyntaxError blamed on the model's
    translation. An unterminated fence is stripped too -- a model that opens
    one and runs out of budget should not cost a whole question.
    """
    body = reply.strip()
    fenced = re.search(r"```[^\n`]*\n(.*?)\n\s*```", body, re.S)
    if fenced:
        return fenced.group(1).strip() + "\n"
    # No closing fence. Drop a leading opener and any stray trailing one.
    body = re.sub(r"\A```[^\n`]*\n", "", body)
    body = re.sub(r"\n\s*```\s*\Z", "", body)
    return body.strip() + "\n"


async def generate_module(
    client: LLMClient,
    config: FormatConfig,
    *,
    difficulty: Difficulty | None = None,
    language: Language = Language.PYTHON,
    tries_per_stage: int = 3,
    question_number: int = 1,
    scenario: str | None = None,
    rng: random.Random | None = None,
    report_to: Reporter = NULL_REPORTER,
) -> StagedResult:
    """One call, then a critique loop until the module passes or the tries run out."""
    cfg = config
    difficulty = difficulty or cfg.session.difficulty_for(0)
    chosen = scenario or pick(rng)
    result = StagedResult(scenario=chosen)
    outcome = StageOutcome(name="module")
    result.stages.append(outcome)

    wanted = cfg.scoring.hidden_test_count
    gen = cfg.generation
    # The interface declares HINT, COMPLEXITY and CLARIFICATIONS unconditionally
    # so the contract the model reads never changes shape. What varies is
    # whether we ask for them -- an unasked-for name stays "" and is dropped.
    asks = [
        f"Show {cfg.environment.visible_tests} worked example(s) in EXAMPLES.",
        f"generate_cases must yield at least {wanted} cases.",
    ]
    wants_complexity = gen.state_complexity_target or cfg.scoring.perf_tests
    if wants_complexity:
        asks.append(
            "Set COMPLEXITY to the bound `solution` actually achieves, and make "
            "the generated cases large enough that a naive attempt cannot finish."
        )
    else:
        asks.append("Leave COMPLEXITY as \"\".")
    asks.append(
        "Write a short HINT that nudges without giving the answer."
        if gen.give_hints
        else "Leave HINT as \"\"; this format gives the candidate no support."
    )
    asks.append(
        "Answer 2-4 real ambiguities in CLARIFICATIONS, each with a probe."
    )
    joined = "\n".join(f"- {a}" for a in asks)
    base = (
        f"{_read_prompt(cfg.generation.style)}\n\n"
        f"{_brief(cfg, difficulty, chosen)}\n\n"
        f"{joined}\n\n"
        "The interface you are implementing:\n\n```python\n"
        f"{INTERFACE.read_text()}\n```"
    )
    ask = base
    payload: dict[str, object] | None = None
    last = ""

    for attempt in range(1, tries_per_stage + 1):
        outcome.attempts += 1
        report_to(
            f"writing question {question_number} \u2014 first try"
            if attempt == 1
            else f"writing question {question_number} \u2014 attempt "
                 f"{attempt} of {tries_per_stage}"
        )
        try:
            with telemetry.stage("module"):
                reply = await client.complete_text(
                    system=_read_prompt("stage_module"), user=ask, temperature=0.7
                )
        except LLMError as exc:
            last = str(exc)[:200]
            report_to(f"the model did not answer: {last[:80]}", kind="warn")
            continue

        source = extract_code(reply)
        fault, payload = module_fault(source, wanted=wanted, report_to=report_to)
        if not fault:
            result.source = source
            report_to("the question module passed every check", kind="ok")
            break
        last = fault
        payload = None
        report_to(
            f"the module didn\u2019t hold up: {fault.splitlines()[0][:80]}",
            kind="warn",
        )
        if attempt < tries_per_stage:
            report_to("asking for a revision")
        ask = (
            f"{base}\n\n## Your previous module was rejected\n\n"
            f"```python\n{source}\n```\n\n"
            f"{fault}\n\nFix exactly that and return the whole module again."
        )

    if payload is None:
        outcome.error = last or "no usable module"
        return result

    raw_params = payload.get("parameters")
    parameters = [str(p) for p in raw_params] if isinstance(raw_params, list) else []
    function_name = str(payload.get("function_name") or "solve")
    raw_examples = payload.get("examples")
    examples: list[dict[str, object]] = (
        [e for e in raw_examples if isinstance(e, dict)]
        if isinstance(raw_examples, list) else []
    )
    visible: list[TestCase] = []
    for ex in examples:
        args = ex.get("args")
        if not isinstance(args, dict):
            continue
        why = ex.get("why")
        visible.append(TestCase(
            args=dict(args),
            expected=ex.get("expected"),
            explanation=str(why) if isinstance(why, str) else None,
        ))
    raw_clar = payload.get("clarifications")
    clarifications = [
        Clarification(
            question=str(c.get("question") or ""),
            answer=str(c.get("answer") or ""),
            probe=dict(c["probe"]) if isinstance(c.get("probe"), dict) else {},
            expect=c.get("expect"),
        )
        for c in (raw_clar if isinstance(raw_clar, list) else [])
        if isinstance(c, dict) and c.get("question") and c.get("answer")
    ]
    hint = str(payload.get("hint") or "").strip()
    complexity = str(payload.get("complexity") or "").strip()
    raw_cases = payload.get("cases")
    cases: list[object] = list(raw_cases) if isinstance(raw_cases, list) else []
    result.question = GeneratedQuestion(
        title=str(payload.get("title") or "Untitled"),
        difficulty=difficulty,
        topics=list(cfg.generation.topics),
        statement_md=str(payload.get("statement") or ""),
        constraints_md=str(payload.get("constraints") or ""),
        # `is_valid` replaces declared bounds, so the structured constraints
        # exist only to satisfy the gate's coverage rule; the real check is the
        # predicate, which every generated case has already passed.
        constraints=[ParamConstraint(name=p) for p in parameters],
        clarifications=clarifications,
        hint_md=hint if (hint and gen.give_hints) else None,
        complexity_target=complexity if (complexity and wants_complexity) else None,
        signatures=[
            Signature(
                language=language,
                function_name=function_name,
                scaffold=scaffold_from(function_name, parameters),
                reference_solution=result.source,
            )
        ],
        visible_tests=visible,
        hidden_generator_py=_replay_generator(cases),
        brute_force_py=_brute_force_module(result.source, function_name),
    )

    # Every language the format offers needs a reference, or the gate rejects
    # the question and `execute_against` has nothing to run. The oracle stays
    # Python; the rest reproduce its answers.
    for other in cfg.environment.languages:
        if other is language:
            continue
        stage = StageOutcome(name=f"translate:{other.value}")
        result.stages.append(stage)
        report_to(f"translating the solution to {other.value}")
        stage.attempts = 1
        sig, why = await translate_signature(
            client,
            source=result.source,
            function_name=function_name,
            parameters=parameters,
            visible_tests=visible,
            language=other,
        )
        if sig is None:
            stage.error = why
            report_to(
                f"the {other.value} translation did not hold up: {why[:80]}",
                kind="warn",
            )
            result.question = None
            return result
        result.question.signatures.append(sig)

    return result


def _brute_force_module(source: str, function_name: str) -> str:
    """The module again, with the question's function name bound to the slow one.

    The gate runs `brute_force_py` and calls `sig.function_name` inside it.
    Handing it the module unchanged meant it called `solution` -- comparing the
    reference against itself and agreeing every time. A check that cannot fail
    is worse than no check, because it reads as evidence.

    The probe already compares the two internally; this makes the gate's own
    check mean what it says.
    """
    return (
        f"{source}\n\n"
        f"# The gate calls the question's function name here; point it at the\n"
        f"# slow implementation, which is the whole purpose of this module.\n"
        f"{function_name} = brute_force\n"
    )


def _replay_generator(cases: list[object]) -> str:
    """The cases the module already produced, as a script that reprints them.

    The gate runs a generator; the module yields values. Rather than teach the
    gate a second protocol, the cases we have already validated are replayed
    verbatim -- so what the gate sees is exactly what `is_valid` accepted.
    """
    return (
        "import json\n"
        f"for case in {json.dumps(cases)}:\n"
        "    print(json.dumps({'args': case}))\n"
    )


async def translate_signature(
    client: LLMClient,
    *,
    source: str,
    function_name: str,
    parameters: list[str],
    visible_tests: list[TestCase],
    language: Language,
    tries: int = 2,
) -> tuple[Signature | None, str]:
    """The reference in one more language, checked by running it.

    Only `solution` and the scaffold cross the language boundary.
    `generate_cases`, `is_valid` and `brute_force` stay Python, because the
    oracle is Python: another language's job is to reproduce its answers, not
    to have opinions of its own. That is the split `_check_other_languages`
    already assumes.

    Returns `(signature, "")` or `(None, why)`.
    """
    ask = (
        f"Translate this Python function into {language.value}.\n\n"
        f"```python\n{source}\n```\n\n"
        f"The function is `{function_name}` and its parameters, in order, are: "
        f"{', '.join(parameters)}.\n\n"
        "It must produce these exact answers:\n"
        + "\n".join(
            f"- {function_name}({t.args}) -> {t.expected!r}" for t in visible_tests
        )
    )
    last = "the model produced no usable translation"
    for _ in range(max(1, tries)):
        try:
            with telemetry.stage(f"translate:{language.value}"):
                reply = await client.complete_text(
                    system=_read_prompt("stage_translate"), user=ask, temperature=0.2
                )
        except LLMError as exc:
            last = str(exc)[:200]
            continue
        try:
            sections = parse_sections(reply).sections
        except ParseError as exc:
            last = str(exc)
            ask = f"{ask}\n\n## Your previous reply was rejected\n\n{last}"
            continue

        # Same fence tolerance as the module itself. The prompt asks for bare
        # code; asking is not the same as getting it.
        scaffold = extract_code(sections.get("scaffold") or "").strip()
        reference = extract_code(sections.get("solution") or "").strip()
        if not scaffold or not reference:
            last = "the reply is missing a SCAFFOLD or SOLUTION section"
            ask = f"{ask}\n\n## Your previous reply was rejected\n\n{last}"
            continue

        fault = reference_fault(
            scaffold=scaffold,
            reference_solution=reference,
            function_name=function_name,
            visible_tests=visible_tests,
            language=language,
        )
        if not fault:
            return Signature(
                language=language,
                function_name=function_name,
                scaffold=scaffold,
                reference_solution=reference,
            ), ""
        last = fault
        ask = (
            f"{ask}\n\n## Your previous translation was rejected\n\n"
            f"```\n{reference}\n```\n\n{fault}\n\nFix exactly that and "
            "return both sections again."
        )
    return None, last


async def generate_question_as_module(
    client: LLMClient,
    config: FormatConfig,
    *,
    difficulty: Difficulty | None = None,
    language: Language = Language.PYTHON,
    max_attempts: int = 4,
    question_number: int = 1,
    report_to: Reporter = NULL_REPORTER,
) -> GenerationResult:
    """`generate_module`, gated and packaged like the monolithic path.

    The strategy produces an *ungated* question; the gate is the arbiter for
    every strategy or none of them are comparable. This is the seam that makes
    the two interchangeable at the call site.
    """
    result = GenerationResult(question=None, attempts=[])
    for attempt in range(max(1, max_attempts)):
        report_to.checkpoint()
        staged = await generate_module(
            client, config,
            difficulty=difficulty,
            language=language,
            question_number=question_number,
            report_to=report_to,
        )
        if staged.question is None:
            bad = next((st for st in staged.stages if not st.ok), None)
            result.attempts.append(GenerationAttempt(
                outcome=GateOutcome.SCHEMA_INVALID,
                detail=(bad.error[:300] if bad else "no module"),
                title="",
            ))
            continue

        report_to(f"validating \u201c{staged.question.title}\u201d", kind="ok")
        report = validate_question(
            staged.question,
            language=language,
            languages=config.environment.languages,
            report_to=report_to,
        )
        result.attempts.append(GenerationAttempt(
            outcome=report.outcome,
            detail=report.detail[:300],
            title=staged.question.title,
        ))
        if report.outcome is GateOutcome.ACCEPTED:
            report_to("the question passed every check", kind="ok")
            result.question = _gated(staged.question, report, language, config)
            return result

        report_to(f"rejected: {report.detail}"[:300], kind="warn")
        if attempt + 1 < max(1, max_attempts):
            report_to("starting over with a fresh question")
    return result
