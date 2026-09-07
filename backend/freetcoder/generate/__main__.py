"""Acceptance-rate harness.

    python -m freetcoder.generate --style codility --preset performance -n 10

Runs the full generate-and-gate loop with no UI and no server. The acceptance
rate this reports is the project's real quality metric: if it is low, the prompts
need work, and that is far cheaper to learn here than through the app.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import sys
from pathlib import Path

from ..formats import DifficultyLockedError, load_styles, resolve
from ..llm import FakeLLM, LLMClient, build_client
from ..models import Difficulty, GatedQuestion, GateOutcome
from ..settings import get_settings
from .pipeline import generate_question


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m freetcoder.generate")
    p.add_argument("--style", default="leetcode", choices=sorted(load_styles()))
    p.add_argument("--preset", default=None)
    p.add_argument("--topics", default="", help="comma-separated concentrations")
    p.add_argument("--freeform", default="")
    p.add_argument("--import-text", default="",
                   help="prose describing a question to adapt")
    p.add_argument("--import-file", type=Path, default=None,
                   help="read the prose from a file instead")
    p.add_argument("--difficulty", default=None, choices=[d.value for d in Difficulty])
    p.add_argument("-n", "--count", type=int, default=1)
    p.add_argument("--attempts", type=int, default=4)
    p.add_argument("--out", type=Path, default=None, help="write accepted questions here")
    p.add_argument("--fixture", default=None,
                   help="replay a recorded fixture instead of calling an LLM")
    return p


async def _run(args: argparse.Namespace) -> int:
    try:
        import_text = args.import_text
        if args.import_file:
            import_text = args.import_file.read_text(encoding="utf-8")
        config = resolve(
            args.style,
            args.preset,
            [t.strip() for t in args.topics.split(",") if t.strip()],
            args.freeform,
            difficulty=Difficulty(args.difficulty) if args.difficulty else None,
            import_text=import_text,
        )
    except DifficultyLockedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    client: LLMClient
    if args.fixture:
        client = FakeLLM.from_fixtures(*([args.fixture] * args.attempts * args.count))
    else:
        client = build_client()
        if isinstance(client, FakeLLM):
            print("error: no LLM configured. Set FREETCODER_LLM_API_KEY, or pass "
                  "--fixture to replay a recorded response.", file=sys.stderr)
            return 2

    print(f"format : {config.label}  ({config.summary()})")
    if config.generation.source == "imported":
        preview = config.generation.import_text.replace("\n", " ")[:70]
        print(f"source : imported -- {preview}...")
    print(f"asking : {args.count} question(s), up to {args.attempts} attempts each\n")

    reasons: collections.Counter[GateOutcome] = collections.Counter()
    accepted: list[GatedQuestion] = []
    for i in range(args.count):
        difficulty = config.session.difficulty_for(min(i, config.session.question_count - 1))
        result = await generate_question(
            client, config, difficulty=difficulty,
            max_attempts=args.attempts,
            exclude_titles=[q.title for q in accepted],
        )
        for a in result.attempts:
            reasons[a.outcome] += 1
        if result.accepted and result.question is not None:
            accepted.append(result.question)
            print(f"  [{i + 1}/{args.count}] ACCEPTED  {result.question.title} "
                  f"({len(result.attempts)} attempt(s), "
                  f"{len(result.question.hidden_tests)} hidden cases)")
        else:
            last = result.attempts[-1] if result.attempts else None
            print(f"  [{i + 1}/{args.count}] gave up   "
                  f"{last.outcome.value if last else 'no attempts'}: "
                  f"{last.detail[:110] if last else ''}")

    total = sum(reasons.values())
    print(f"\nacceptance: {len(accepted)}/{args.count} questions "
          f"from {total} attempt(s)")
    for outcome, n in reasons.most_common():
        print(f"  {outcome.value:<26} {n:>3}  ({n / total:.0%})")

    if args.out and accepted:
        args.out.write_text(
            json.dumps([q.model_dump(mode="json") for q in accepted], indent=2)
        )
        print(f"\nwrote {len(accepted)} question(s) to {args.out}")

    return 0 if accepted else 1


def main() -> int:
    args = _build_parser().parse_args()
    get_settings()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
