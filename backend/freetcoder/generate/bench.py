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
from ..llm import build_client
from ..models import Difficulty, GateOutcome
from ..settings import get_settings
from .pipeline import generate_question
from .quality import Scorecard, score_question

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


async def run_variant(
    variant: str, count: int, *, attempts: int, repairs: int, sufficiency: bool
) -> Scorecard:
    settings = get_settings()
    client = build_client(settings)
    extra, exemplars = variant_prompt(variant)
    card = Scorecard(variant=variant)
    titles: list[str] = []

    for i in range(count):
        style, topics = MATRIX[i % len(MATRIX)]
        config = resolve(style, topics=topics)
        started = time.monotonic()
        result = await generate_question(
            client,
            config,
            difficulty=Difficulty.MEDIUM,
            max_attempts=attempts,
            repair_rounds=repairs,
            exclude_titles=titles,
            system_extra=extra,
            check_sufficiency=sufficiency,
        )
        elapsed = time.monotonic() - started

        card.attempted += 1
        if result.question is None:
            outcomes = [a.outcome.value for a in result.attempts]
            card.failures.extend(outcomes)
            print(f"  {variant} {i + 1}/{count}  {elapsed:5.1f}s  REJECTED  {outcomes}")
            continue

        q = result.question.question
        titles.append(q.title)
        report = score_question(q, exemplars=exemplars)
        report.outcome = GateOutcome.ACCEPTED.value
        # Every attempt beyond the first, and every repair, is a miss the
        # first-pass number is meant to expose.
        report.repairs_used = max(0, len(result.attempts) - 1)
        report.accepted_first_pass = report.repairs_used == 0
        card.reports.append(report)

        flags = []
        if not report.prose_complete:
            flags.append("thin")
        if report.looks_recalled:
            flags.append(f"recalled:{report.recalled_example or report.recalled_title}")
        if report.exemplar_overlap > 0.5:
            flags.append(f"copied:{report.exemplar_overlap:.2f}")
        print(
            f"  {variant} {i + 1}/{count}  {elapsed:5.1f}s  "
            f"repairs={report.repairs_used}  {q.title[:44]:<44} "
            f"{' '.join(flags)}"
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
    parser.add_argument("--out", type=Path, default=Path("bench-results.json"))
    args = parser.parse_args()

    chosen = [v.strip().upper() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in chosen if v not in VARIANTS]
    if unknown:
        parser.error(f"unknown variant(s) {unknown}; have {sorted(VARIANTS)}")

    settings = get_settings()
    print(f"model: {settings.llm_model}   variants: {chosen}   n={args.count}\n")

    cards = []
    for variant in chosen:
        print(f"--- variant {variant}: {VARIANTS[variant] or ['baseline']}")
        cards.append(await run_variant(
            variant, args.count, attempts=args.attempts,
            repairs=args.repairs, sufficiency=args.sufficiency,
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
