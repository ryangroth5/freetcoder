"""Staged generation: does splitting the call actually pay?

The monolithic prompt asks for prose and code together, and in our fixtures the
code outweighs the prose 3.4:1. Two of three models answered it with a statement
under 160 characters. These tests pin the properties staging is supposed to buy,
so the bench measures a mechanism that works rather than one that merely exists.
"""

from __future__ import annotations

import random

import pytest

from freetcoder.formats import resolve
from freetcoder.generate.gate import validate_question
from freetcoder.generate.staged import SCENARIOS, generate_staged
from freetcoder.llm import FakeLLM, LLMError
from freetcoder.models import Difficulty, GateOutcome, Language

STATEMENT = {
    "title": "Steady Tide Windows",
    "statement_md": (
        "A tide gauge writes a reading every minute. Given the readings "
        "`levels` and a tolerance `drift`, return the length of the longest "
        "run of consecutive readings whose highest and lowest values differ by "
        "no more than `drift`.\n\n"
        "`levels` holds the readings in order. `drift` is the widest spread a "
        "run may have. Return the count of readings as an integer. When "
        "`levels` is empty, return 0; a single reading is always steady.\n\n"
        "Example 1 - `levels = [4, 6, 5, 9]`, `drift = 2` returns `3`.\n"
        "Example 2 - `levels = [7]`, `drift = 0` returns `1`."
    ),
    "function_name": "steady_window",
    "parameter_names": ["levels", "drift"],
    "topics": ["sliding window"],
}

REFERENCE = {
    "scaffold": "def steady_window(levels, drift):\n    pass\n",
    "reference_solution": (
        "def steady_window(levels, drift):\n"
        "    best = 0\n"
        "    for i in range(len(levels)):\n"
        "        lo = hi = levels[i]\n"
        "        for j in range(i, len(levels)):\n"
        "            lo = min(lo, levels[j])\n"
        "            hi = max(hi, levels[j])\n"
        "            if hi - lo <= drift:\n"
        "                best = max(best, j - i + 1)\n"
        "    return best\n"
    ),
    "visible_tests": [
        {"args": {"levels": [4, 6, 5, 9], "drift": 2}, "expected": 3},
        {"args": {"levels": [7], "drift": 0}, "expected": 1},
    ],
}

CONSTRAINTS = {
    "constraints_md": (
        "- `0 <= levels.length <= 1000`\n"
        "- `-500 <= levels[i] <= 500`\n"
        "- `0 <= drift <= 1000`"
    ),
    "constraints": [
        {"name": "levels", "min_length": 0, "max_length": 1000,
         "element_min": -500, "element_max": 500},
        {"name": "drift", "min": 0, "max": 1000},
    ],
}

HARNESS = {
    "hidden_generator_py": (
        "import json, random\n"
        "random.seed(11)\n"
        "for _ in range(12):\n"
        "    n = random.randint(1, 30)\n"
        "    levels = [random.randint(-50, 50) for _ in range(n)]\n"
        "    print(json.dumps({'args': {'levels': levels,\n"
        "                               'drift': random.randint(0, 40)}}))\n"
    ),
    "brute_force_py": REFERENCE["reference_solution"],
}

ALL_STAGES = [STATEMENT, REFERENCE, CONSTRAINTS, HARNESS]


def config():
    return resolve("leetcode", topics=["sliding window"])


class TestItBuildsAWholeQuestion:
    async def test_four_stages_produce_a_gate_valid_question(self) -> None:
        llm = FakeLLM(ALL_STAGES)
        result = await generate_staged(
            llm, config(), difficulty=Difficulty.MEDIUM,
            rng=random.Random(0),
        )
        assert result.question is not None, result.failed_stage
        assert validate_question(result.question).outcome is GateOutcome.ACCEPTED

    async def test_it_generates_one_language_not_three(self) -> None:
        """Signatures were ~half the monolithic response."""
        llm = FakeLLM(ALL_STAGES)
        result = await generate_staged(
            llm, config(), language=Language.PYTHON, rng=random.Random(0)
        )
        assert result.question is not None
        assert [s.language for s in result.question.signatures] == [Language.PYTHON]

    async def test_the_scenario_reaches_the_statement_prompt(self) -> None:
        """The seed is the cheap attack on recall; it has to actually be sent."""
        llm = FakeLLM(ALL_STAGES)
        await generate_staged(
            llm, config(), scenario="a beekeeper weighing hives", rng=random.Random(0)
        )
        first_user = llm.calls[0][1]
        assert "a beekeeper weighing hives" in first_user

    async def test_scenarios_vary(self) -> None:
        picks = {
            random.Random(seed).choice(SCENARIOS) for seed in range(40)
        }
        assert len(picks) > 3


class TestAFailingStageCostsOnlyItself:
    """This is the hypothesis staging exists to test: localised failure."""

    async def test_a_bad_reference_does_not_rerun_the_statement(self) -> None:
        llm = FakeLLM([
            STATEMENT,                       # stage 1 succeeds
            LLMError("provider hiccup"),     # stage 2 fails once
            REFERENCE,                       # stage 2 retried, succeeds
            CONSTRAINTS,
            HARNESS,
        ])
        result = await generate_staged(
            llm, config(), tries_per_stage=2, rng=random.Random(0)
        )
        assert result.question is not None, result.failed_stage

        by_name = {s.name: s for s in result.stages}
        assert by_name["statement"].attempts == 1, "the statement was re-asked"
        assert by_name["reference"].attempts == 2

    async def test_exhausting_a_stage_reports_which_one(self) -> None:
        llm = FakeLLM([
            STATEMENT,
            LLMError("down"), LLMError("down"),
        ])
        result = await generate_staged(
            llm, config(), tries_per_stage=2, rng=random.Random(0)
        )
        assert result.question is None
        assert result.failed_stage == "reference"

    async def test_later_stages_are_not_attempted_after_a_failure(self) -> None:
        """No point deriving constraints for a solution that does not exist."""
        llm = FakeLLM([STATEMENT, LLMError("down"), LLMError("down")])
        result = await generate_staged(
            llm, config(), tries_per_stage=2, rng=random.Random(0)
        )
        assert [s.name for s in result.stages] == ["statement", "reference"]


class TestTelemetry:
    async def test_each_stage_is_labelled(self) -> None:
        from freetcoder.llm import telemetry

        llm = FakeLLM(ALL_STAGES)
        with telemetry.collecting() as run:
            await generate_staged(llm, config(), rng=random.Random(0))

        # FakeLLM does not go through the real client, so no provider records
        # are produced -- the labels are what a live run would attribute time to.
        assert run.calls == []


@pytest.mark.parametrize("missing", ["statement", "reference", "constraints"])
async def test_every_stage_is_required(missing: str) -> None:
    """Each stage produces something the question cannot be assembled without."""
    order = ["statement", "reference", "constraints", "harness"]
    queue: list[object] = []
    for name, payload in zip(order, ALL_STAGES, strict=True):
        if name == missing:
            queue.extend([LLMError("down"), LLMError("down")])
            break
        queue.append(payload)

    result = await generate_staged(
        FakeLLM(queue), config(), tries_per_stage=2, rng=random.Random(0)
    )
    assert result.question is None
    assert result.failed_stage == missing


class TestTheReferenceStageChecksItself:
    """The mechanism staging exists for, and the one the first run lacked.

    Without it a wrong reference is only caught by the gate, after constraints
    and harness have already been generated against a solution that was never
    going to work. Both failures in the first measured run were exactly that.
    """

    async def test_a_reference_that_contradicts_its_examples_is_retried(self) -> None:
        wrong = dict(REFERENCE)
        wrong["reference_solution"] = (
            "def steady_window(levels, drift):\n    return 999\n"
        )
        llm = FakeLLM([STATEMENT, wrong, REFERENCE, CONSTRAINTS, HARNESS])

        result = await generate_staged(
            llm, config(), tries_per_stage=2, rng=random.Random(0)
        )
        assert result.question is not None, result.failed_stage
        by_name = {s.name: s for s in result.stages}
        assert by_name["reference"].attempts == 2
        # The stage caught it, so the later stages ran once against a good one.
        assert by_name["constraints"].attempts == 1

    async def test_the_model_is_told_what_was_wrong(self) -> None:
        """A bare retry corrects poorly; a concrete complaint corrects well."""
        wrong = dict(REFERENCE)
        wrong["reference_solution"] = (
            "def steady_window(levels, drift):\n    return 999\n"
        )
        llm = FakeLLM([STATEMENT, wrong, REFERENCE, CONSTRAINTS, HARNESS])
        await generate_staged(llm, config(), tries_per_stage=2, rng=random.Random(0))

        retry_prompt = llm.calls[2][1]
        assert "rejected" in retry_prompt
        assert "999" in retry_prompt, "the actual wrong value should be quoted"

    async def test_a_scaffold_that_does_not_parse_is_caught_here(self) -> None:
        """scaffold_invalid was a real failure of the first run, found late."""
        broken = dict(REFERENCE)
        broken["scaffold"] = "def steady_window(levels, drift:\n"
        llm = FakeLLM([STATEMENT, broken, REFERENCE, CONSTRAINTS, HARNESS])

        result = await generate_staged(
            llm, config(), tries_per_stage=2, rng=random.Random(0)
        )
        assert result.question is not None, result.failed_stage
        assert {s.name: s for s in result.stages}["reference"].attempts == 2

    async def test_an_unfixable_reference_stops_before_the_later_stages(self) -> None:
        wrong = dict(REFERENCE)
        wrong["reference_solution"] = (
            "def steady_window(levels, drift):\n    return 999\n"
        )
        llm = FakeLLM([STATEMENT, wrong, wrong, CONSTRAINTS, HARNESS])

        result = await generate_staged(
            llm, config(), tries_per_stage=2, rng=random.Random(0)
        )
        assert result.question is None
        assert result.failed_stage == "reference"
        # Constraints and harness were never asked for.
        assert [s.name for s in result.stages] == ["statement", "reference"]


FLAT = {
    "title": "Steady Tide Windows",
    "statement_md": STATEMENT["statement_md"],
    "function_name": "steady_window",
    "scaffold": REFERENCE["scaffold"],
    "reference_solution": REFERENCE["reference_solution"],
    "visible_tests": REFERENCE["visible_tests"],
    "hidden_generator_py": HARNESS["hidden_generator_py"],
    "brute_force_py": HARNESS["brute_force_py"],
    "topics": ["sliding window"],
}


class TestTheFlatStrategy:
    """One wide call with no nested signature, then constraints on their own.

    Every shape failure measured against a live provider landed in a nested
    list -- `signatures`, `constraints[].name`, `clarifications[].answer`. This
    removes the lists rather than the breadth.
    """

    async def test_two_calls_produce_a_gate_valid_question(self) -> None:
        from freetcoder.generate.staged import generate_flat

        llm = FakeLLM([FLAT, CONSTRAINTS])
        result = await generate_flat(llm, config(), rng=random.Random(0))
        assert result.question is not None, result.failed_stage
        assert validate_question(result.question).outcome is GateOutcome.ACCEPTED

    async def test_it_costs_two_calls_not_four(self) -> None:
        from freetcoder.generate.staged import generate_flat

        llm = FakeLLM([FLAT, CONSTRAINTS])
        result = await generate_flat(llm, config(), rng=random.Random(0))
        assert result.question is not None
        assert [s.name for s in result.stages] == ["question", "constraints"]
        assert len(llm.calls) == 2

    async def test_the_main_call_validates_its_own_reference(self) -> None:
        """The flat draft still checks itself before constraints are derived."""
        from freetcoder.generate.staged import generate_flat

        wrong = dict(FLAT)
        wrong["reference_solution"] = (
            "def steady_window(levels, drift):\n    return 999\n"
        )
        llm = FakeLLM([wrong, FLAT, CONSTRAINTS])
        result = await generate_flat(
            llm, config(), tries_per_stage=2, rng=random.Random(0)
        )
        assert result.question is not None, result.failed_stage
        by_name = {s.name: s for s in result.stages}
        assert by_name["question"].attempts == 2
        assert by_name["constraints"].attempts == 1

    async def test_the_scenario_reaches_the_prompt(self) -> None:
        from freetcoder.generate.staged import generate_flat

        llm = FakeLLM([FLAT, CONSTRAINTS])
        await generate_flat(
            llm, config(), scenario="a locksmith recording key cuttings",
            rng=random.Random(0),
        )
        assert "a locksmith recording key cuttings" in llm.calls[0][1]


class TestTheProseIsCheckedInStage:
    """prose_too_thin was the dominant rejection in every measured run.

    By the time the gate says so, the reference, generator and brute force have
    all been written for a question that was never going to be served. The
    check is deterministic and free, so it belongs where it can be fixed for
    one small call.
    """

    async def test_a_statement_that_omits_a_parameter_is_retried(self) -> None:
        from freetcoder.generate.staged import generate_flat

        vague = dict(FLAT)
        # Long enough to pass the schema, so the prose check is what rejects
        # it: it describes the task without ever naming what it is handed.
        vague["statement_md"] = (
            "Return the length of the longest steady run of readings taken in "
            "order. A run counts as steady when the difference between its "
            "highest and its lowest reading is no greater than the tolerance "
            "allowed. Return the number of readings in the longest such run. "
            "When there are no readings at all, return 0. A single reading is "
            "always steady, whatever tolerance applies to it."
        )
        llm = FakeLLM([vague, FLAT, CONSTRAINTS])
        result = await generate_flat(
            llm, config(), tries_per_stage=2, rng=random.Random(0)
        )
        assert result.question is not None, result.failed_stage
        assert {s.name: s for s in result.stages}["question"].attempts == 2

    async def test_the_missing_parameter_is_named_on_the_retry(self) -> None:
        from freetcoder.generate.staged import generate_flat

        vague = dict(FLAT)
        # Long enough to pass the schema, so the prose check is what rejects
        # it: it describes the task without ever naming what it is handed.
        vague["statement_md"] = (
            "Return the length of the longest steady run of readings taken in "
            "order. A run counts as steady when the difference between its "
            "highest and its lowest reading is no greater than the tolerance "
            "allowed. Return the number of readings in the longest such run. "
            "When there are no readings at all, return 0. A single reading is "
            "always steady, whatever tolerance applies to it."
        )
        llm = FakeLLM([vague, FLAT, CONSTRAINTS])
        await generate_flat(llm, config(), tries_per_stage=2, rng=random.Random(0))

        retry = llm.calls[1][1]
        assert "levels" in retry and "drift" in retry

    async def test_prose_is_checked_before_the_reference_is_run(self) -> None:
        """The free check should not wait behind a sandboxed execution."""
        from freetcoder.generate.staged import prose_fault

        assert prose_fault("mentions levels and drift", ["levels", "drift"]) == ""
        fault = prose_fault("mentions levels only", ["levels", "drift"])
        assert "drift" in fault
