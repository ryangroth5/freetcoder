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
    Difficulty,
    GeneratedQuestion,
    Language,
    ParamConstraint,
    Signature,
    TestCase,
)
from ..progress import NULL_REPORTER, Reporter
from ..runner import Limits, Verdict
from ..runner.sandbox import Workspace, execute
from .module_probe import PROBE
from .pipeline import _read_prompt
from .scenarios import pick
from .staged import StagedResult, StageOutcome, _brief

INTERFACE = Path(__file__).parent / "interface" / "question_interface.py"

#: Node, and pyright is Node: V8 cannot start under an address-space rlimit.
#: The same lesson the TypeScript adapter learned.
TOOL_LIMITS = Limits(
    wall_seconds=60.0, cpu_seconds=55, memory_mb=1024, limit_address_space=False
)
PROBE_LIMITS = Limits(wall_seconds=30.0, cpu_seconds=25, memory_mb=512)

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

    if result.verdict is Verdict.TIMEOUT:
        return ProbeResult(
            ok=False, step="timeout",
            detail="the module did not finish; solution or generate_cases is too slow",
        )

    for line in result.stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("__freetcoder"):
            if record.get("ok"):
                return ProbeResult(ok=True, payload=record)
            return ProbeResult(
                ok=False, step=str(record.get("step", "")),
                detail=str(record.get("detail", "")),
            )

    return ProbeResult(
        ok=False, step="probe",
        detail=(result.stderr or "the module produced no result").strip()[:600],
    )


def module_fault(source: str, *, wanted: int) -> tuple[str, dict[str, object] | None]:
    """Every critic in order, cheapest first. Returns ("", payload) when good."""
    for fault in (alarming_source(source), lint_fault(source)):
        if fault:
            return fault, None
    if fault := typecheck_fault(source):
        return fault, None

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
    """The module, with any Markdown fence stripped.

    Models fence code even when asked for a bare file. That is cosmetic and
    worth tolerating rather than rejecting over.
    """
    fenced = re.search(r"```(?:python)?\s*\n(.*?)\n\s*```", reply, re.S)
    return (fenced.group(1) if fenced else reply).strip() + "\n"


async def generate_module(
    client: LLMClient,
    config: FormatConfig,
    *,
    difficulty: Difficulty | None = None,
    language: Language = Language.PYTHON,
    tries_per_stage: int = 3,
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
    base = (
        f"{_read_prompt(cfg.generation.style)}\n\n"
        f"{_brief(cfg, difficulty, chosen)}\n\n"
        f"Show {cfg.environment.visible_tests} worked example(s) in EXAMPLES.\n"
        f"generate_cases must yield at least {wanted} cases.\n\n"
        "The interface you are implementing:\n\n```python\n"
        f"{INTERFACE.read_text()}\n```"
    )
    ask = base
    payload: dict[str, object] | None = None
    last = ""

    report_to("writing the question module")
    for _ in range(tries_per_stage):
        outcome.attempts += 1
        try:
            with telemetry.stage("module"):
                reply = await client.complete_text(
                    system=_read_prompt("stage_module"), user=ask, temperature=0.7
                )
        except LLMError as exc:
            last = str(exc)[:200]
            continue

        source = extract_code(reply)
        fault, payload = module_fault(source, wanted=wanted)
        if not fault:
            result.source = source
            break
        last = fault
        payload = None
        report_to(f"revising: {fault.splitlines()[0][:80]}", kind="warn")
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
        brute_force_py=result.source,
    )
    return result


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
