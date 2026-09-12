"""Compare prompt variants on real generations.

Spends money, so it is never part of `make check`. The point is to choose a
prompt on evidence: every variant runs the same matrix of style and topic, and
every question it produces is scored by the same rules.

    python -m freetcoder.generate.bench --variants A,B,C -n 3

The headline number is **first-pass** acceptance -- accepted with zero repair
rounds. Acceptance after repair hides exactly the weakness being measured, since
the repair loop is good at patching prose the model should not have skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from ..formats import resolve
from ..llm import LLMClient, build_client, telemetry
from ..llm.client import OpenAICompatibleClient
from ..models import Difficulty, GateOutcome, Language
from ..settings import get_settings
from .gate import validate_question
from .pipeline import generate_question
from .quality import Scorecard, score_question
from .scenarios import pick as pick_scenario
from .staged import generate_staged

PROMPTS = Path(__file__).parent / "prompts"

#: A variant is the system prompt plus zero or more appended sections.
VARIANTS: dict[str, list[str]] = {
    "A": [],                                    # control: today's prompt
    "B": ["variants/B_statement"],              # specify the statement
    "C": ["variants/B_statement", "exemplars/tide_gauge"],
    "D": [
        "variants/B_statement",
        "exemplars/tide_gauge",
        "exemplars/warehouse_picks",
        "exemplars/shift_ledger",
    ],
    "E": ["variants/B_statement", "variants/E_selfcheck"],
    "F": ["variants/B_statement", "variants/F_original"],
    "G": ["variants/B_statement", "variants/F_original", "exemplars/tide_gauge"],
}

#: The same work for every variant, so differences are the prompt's doing.
MATRIX: list[tuple[str, list[str]]] = [
    ("leetcode", ["hash maps"]),
    ("leetcode", ["two pointers"]),
    ("codility", ["arrays & strings"]),
    ("coderbyte", ["sorting & searching"]),
    ("leetcode", ["dynamic programming"]),
    ("codility", ["greedy"]),
]


def variant_prompt(variant: str) -> tuple[str, list[str]]:
    """The appended system text, and the exemplar bodies for copy-detection."""
    parts, exemplars = [], []
    for name in VARIANTS[variant]:
        body = (PROMPTS / f"{name}.md").read_text()
        parts.append(body)
        if name.startswith("exemplars/"):
            exemplars.append(body)
    return ("\n\n" + "\n\n".join(parts) if parts else ""), exemplars


def _client(model: str | None) -> LLMClient:
    settings = get_settings()
    if not model:
        return build_client(settings)
    return OpenAICompatibleClient(
        base_url=settings.llm_base_url, api_key=settings.llm_api_key, model=model,
        timeout_s=settings.llm_timeout_s, max_retries=settings.llm_max_retries,
    )


async def run_variant(
    variant: str, count: int, *, attempts: int, repairs: int, sufficiency: bool,
    strategy: str = "monolithic", model: str | None = None, seed: bool = False,
) -> Scorecard:
    settings = get_settings()
    client = _client(model)
    extra, exemplars = variant_prompt(variant)
    label = f"{strategy[:4]}/{variant}"
    card = Scorecard(
        variant=f"{model or settings.llm_model}|{strategy}"
        f"{'+seed' if seed else ''}|{variant}"
    )
    titles: list[str] = []

    for i in range(count):
        style, topics = MATRIX[i % len(MATRIX)]
        config = resolve(style, topics=topics)
        # The same seed feeds either strategy, so the anti-recall lever is
        # measured independently of how the question is assembled.
        scenario = pick_scenario() if seed else ""
        started = time.monotonic()
        card.attempted += 1
        with telemetry.collecting() as calls:
            if strategy == "staged":
                staged = await generate_staged(
                    client, config, difficulty=Difficulty.MEDIUM,
                    language=Language.PYTHON, tries_per_stage=max(1, attempts),
                    scenario=scenario or None,
                )
                question = staged.question
                # The staged path returns an unvalidated question; the gate is
                # the same arbiter for both strategies or the comparison means
                # nothing.
                outcome = (
                    validate_question(question).outcome
                    if question is not None
                    else GateOutcome.SCHEMA_INVALID
                )
                failures = (
                    [staged.failed_stage or outcome.value]
                    if question is None or outcome is not GateOutcome.ACCEPTED
                    else []
                )
                repairs_used = sum(max(0, s.attempts - 1) for s in staged.stages)
            else:
                result = await generate_question(
                    client, config, difficulty=Difficulty.MEDIUM,
                    max_attempts=attempts, repair_rounds=repairs,
                    exclude_titles=titles, system_extra=extra,
                    scenario=scenario, check_sufficiency=sufficiency,
                )
                question = result.question.question if result.question else None
                outcome = (
                    GateOutcome.ACCEPTED if result.question
                    else GateOutcome.SCHEMA_INVALID
                )
                failures = [a.outcome.value for a in result.attempts] if not result.question else []
                repairs_used = max(0, len(result.attempts) - 1)
        elapsed = time.monotonic() - started

        if question is None or outcome is not GateOutcome.ACCEPTED:
            card.failures.extend(failures or [outcome.value])
            print(f"  {label} {i + 1}/{count}  {elapsed:5.1f}s  REJECTED  {failures}")
            continue

        q = question
        titles.append(q.title)
        report = score_question(q, exemplars=exemplars)
        report.outcome = outcome.value
        report.repairs_used = repairs_used
        report.accepted_first_pass = repairs_used == 0
        report.seconds = round(elapsed, 1)
        report.provider_seconds = round(calls.seconds, 1)
        report.completion_tokens = calls.completion_tokens
        card.reports.append(report)

        flags = []
        if not report.prose_complete:
            flags.append("thin")
        if report.looks_recalled:
            flags.append(f"recalled:{report.recalled_example or report.recalled_title}")
        if report.exemplar_overlap > 0.5:
            flags.append(f"copied:{report.exemplar_overlap:.2f}")
        print(
            f"  {label} {i + 1}/{count}  {elapsed:5.1f}s  "
            f"llm={report.provider_seconds:5.1f}s  tok={report.completion_tokens:>5}  "
            f"{q.title[:38]:<38} {' '.join(flags)}"
        )
    return card


def print_table(cards: list[Scorecard]) -> None:
    rows = [c.summary() for c in cards]
    if not rows:
        return
    keys = list(rows[0])
    widths = {k: max(len(str(k)), *(len(str(r[k])) for r in rows)) for k in keys}
    print("\n" + "  ".join(str(k).ljust(widths[k]) for k in keys))
    print("  ".join("-" * widths[k] for k in keys))
    for r in rows:
        print("  ".join(str(r[k]).ljust(widths[k]) for k in keys))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", default="A,B",
                        help=f"comma-separated, from {sorted(VARIANTS)}")
    parser.add_argument("-n", "--count", type=int, default=3)
    parser.add_argument("--attempts", type=int, default=2)
    # Each repair is another provider round-trip, ~50s. One is enough to see
    # whether a variant needs rescuing; the first-pass number is the metric.
    parser.add_argument("--repairs", type=int, default=1)
    parser.add_argument("--sufficiency", action="store_true",
                        help="also run the second-model check (doubles the time)")
    parser.add_argument("--strategies", default="monolithic",
                        help="comma-separated: monolithic,staged")
    parser.add_argument("--models", default="",
                        help="comma-separated model slugs; blank uses the configured one")
    parser.add_argument("--seed-scenario", action="store_true",
                        help="give each question a concrete setting (anti-recall)")
    parser.add_argument("--out", type=Path, default=Path("bench-results.json"))
    args = parser.parse_args()

    chosen = [v.strip().upper() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in chosen if v not in VARIANTS]
    if unknown:
        parser.error(f"unknown variant(s) {unknown}; have {sorted(VARIANTS)}")

    settings = get_settings()
    print(f"model: {settings.llm_model}   variants: {chosen}   n={args.count}\n")

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()] or [None]

    cards = []
    for model in models:
        for strategy in strategies:
            for variant in chosen:
                print(f"--- {model or settings.llm_model} | {strategy} | "
                      f"variant {variant}: {VARIANTS[variant] or ['baseline']}")
                cards.append(await run_variant(
                    variant, args.count, attempts=args.attempts,
                    repairs=args.repairs, sufficiency=args.sufficiency,
                    strategy=strategy, model=model, seed=args.seed_scenario,
                ))

    print_table(cards)
    args.out.write_text(json.dumps(
        {
            "model": settings.llm_model,
            "count": args.count,
            "runs": [
                {"variant": c.variant,
                 "summary": c.summary(),
                 "questions": [asdict(r) for r in c.reports]}
                for c in cards
            ],
        },
        indent=2,
    ))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
