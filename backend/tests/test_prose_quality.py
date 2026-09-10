"""The statement and constraints have to say something.

Written after a real generation shipped with its description missing: DeepSeek
recalled LeetCode 974, reproduced its examples verbatim, restated the title as
the statement, and left constraints blank. Every executable check passed,
because none of them read the prose.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from freetcoder.generate.gate import validate_question
from freetcoder.generate.sufficiency import build_solver_prompt
from freetcoder.models import GateOutcome, GeneratedQuestion, Language

FIXTURES = Path(__file__).parent / "fixtures" / "llm"


def raw_recalled() -> dict[str, Any]:
    return json.loads((FIXTURES / "recalled_no_statement.json").read_text())


def described(d: dict[str, Any]) -> dict[str, Any]:
    """The same question with prose a candidate could actually work from."""
    d = copy.deepcopy(d)
    d["constraints_md"] = "- `1 <= nums.length <= 1000`\n- `1 <= k <= 100`"
    d["statement_md"] = (
        "Given an integer array `nums` and an integer `k`, return the length of "
        "the longest contiguous subarray whose sum is divisible by `k`. Return "
        "0 when no such subarray exists."
    )
    d["constraints"] = [
        {"name": "nums", "min_length": 1, "max_length": 1000},
        {"name": "k", "min": 1, "max": 100},
    ]
    return d


class TestTheReportedQuestion:
    def test_blank_constraints_no_longer_validate(self) -> None:
        """It shipped because constraints_md had no validation whatsoever."""
        with pytest.raises(ValidationError, match="constraints_md"):
            GeneratedQuestion.model_validate(raw_recalled())

    def test_the_same_question_with_prose_is_fine(self) -> None:
        """The fix must reject thin prose, not this kind of question."""
        q = GeneratedQuestion.model_validate(described(raw_recalled()))
        assert validate_question(q).outcome is GateOutcome.ACCEPTED


class TestTheGateReadsTheProse:
    def test_a_statement_that_is_only_the_title_is_rejected(self) -> None:
        d = raw_recalled()
        d["constraints_md"] = "- `1 <= nums.length <= 1000`"
        report = validate_question(GeneratedQuestion.model_validate(d))
        assert report.outcome is GateOutcome.PROSE_TOO_THIN

    def test_a_parameter_the_statement_never_mentions_is_rejected(self) -> None:
        """Naming the inputs cannot be faked with filler; length can."""
        d = described(raw_recalled())
        d["statement_md"] = (
            "Return the length of the longest contiguous run whose total is "
            "divisible by the divisor. Return 0 when there is no such run."
        )
        report = validate_question(GeneratedQuestion.model_validate(d))
        assert report.outcome is GateOutcome.PROSE_TOO_THIN
        assert "nums" in (report.detail or "")

    def test_a_parameter_with_no_bounds_is_rejected(self) -> None:
        """An empty constraints list made _check_constraints pass vacuously."""
        d = described(raw_recalled())
        d["constraints"] = [{"name": "nums", "min_length": 1, "max_length": 1000}]
        report = validate_question(GeneratedQuestion.model_validate(d))
        assert report.outcome is GateOutcome.PROSE_TOO_THIN
        assert "'k'" in (report.detail or "")

    def test_it_is_repairable(self) -> None:
        """Rejection is only useful if the loop can act on it."""
        from freetcoder.generate.repair import TARGET_FOR

        assert TARGET_FOR[GateOutcome.PROSE_TOO_THIN] == "statement"


class TestTheSolverCannotSeeTheTitle:
    """A memorable title plus worked examples let the solver recall a published
    problem and pass a question whose statement said nothing."""

    def test_the_title_is_withheld(self) -> None:
        q = GeneratedQuestion.model_validate(described(raw_recalled()))
        prompt = build_solver_prompt(q, Language.PYTHON)
        assert q.title not in prompt
        assert "# Problem" in prompt

    def test_the_statement_is_still_there(self) -> None:
        q = GeneratedQuestion.model_validate(described(raw_recalled()))
        prompt = build_solver_prompt(q, Language.PYTHON)
        assert q.statement_md in prompt
        assert q.constraints_md in prompt
