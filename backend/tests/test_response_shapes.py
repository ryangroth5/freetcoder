"""Accept the shape models actually emit.

Every payload here was observed from a real provider (glm-4.6 via OpenRouter)
being rejected by our schema. With `strict: False` the schema is a hint, and
the model returns a perfectly good question in the obvious shape rather than
ours. Rejecting those was a bug in us.
"""

from __future__ import annotations

from typing import Any

from freetcoder.models import Difficulty, GeneratedQuestion, Language

#: The shape glm-4.6 returns unprompted: signature flattened to top level,
#: capitalised difficulty, bounds as pairs, the example's note as "description".
OBSERVED: dict[str, Any] = {
    "title": "Steady Hours",
    "difficulty": "Medium",
    "topic": "sliding window",
    "question_id": "q-0001",
    "tags": ["arrays"],
    "statement_md": (
        "Given `hours` and a `target`, return how many contiguous stretches of "
        "`hours` sum to `target`. Return 0 when none do. The list may be empty."
    ),
    "constraints_md": "- `1 <= hours.length <= 100000`\n- `-10^9 <= target <= 10^9`",
    "constraints": [
        {"name": "hours", "type": "array", "size": [1, 100000],
         "value_range": [-10000, 10000]},
        {"name": "target", "type": "integer", "value_range": [-1000000000, 1000000000]},
    ],
    "language": "python",
    "function_name": "steady_hours",
    "scaffold": "def steady_hours(hours, target):\n    pass\n",
    "reference_solution": (
        "def steady_hours(hours, target):\n"
        "    count = 0\n"
        "    for i in range(len(hours)):\n"
        "        total = 0\n"
        "        for j in range(i, len(hours)):\n"
        "            total += hours[j]\n"
        "            if total == target:\n"
        "                count += 1\n"
        "    return count\n"
    ),
    "visible_tests": [
        {"description": "Example 1", "args": {"hours": [1, 2, 3, 2, 1], "target": 5},
         "expected": 2},
    ],
    "hidden_generator_py": (
        "import json\n"
        "print(json.dumps({'args': {'hours': [1], 'target': 1}}))\n"
    ),
}


class TestTheObservedShapeIsAccepted:
    def test_it_validates_at_all(self) -> None:
        """This exact payload was a rejection before the normaliser."""
        GeneratedQuestion.model_validate(OBSERVED)

    def test_the_capitalised_enum_is_taken(self) -> None:
        q = GeneratedQuestion.model_validate(OBSERVED)
        assert q.difficulty is Difficulty.MEDIUM

    def test_a_flattened_signature_becomes_one(self) -> None:
        """Models flatten it when only one language was asked for."""
        q = GeneratedQuestion.model_validate(OBSERVED)
        assert len(q.signatures) == 1
        sig = q.signatures[0]
        assert sig.language is Language.PYTHON
        assert sig.function_name == "steady_hours"
        assert "count += 1" in sig.reference_solution

    def test_a_collection_range_bounds_its_elements(self) -> None:
        """`value_range` on an array means elements, not the array itself.

        Reading it as the value would silently mis-bound every generated case,
        which is worse than rejecting the question.
        """
        q = GeneratedQuestion.model_validate(OBSERVED)
        hours = next(c for c in q.constraints if c.name == "hours")
        assert (hours.min_length, hours.max_length) == (1, 100000)
        assert (hours.element_min, hours.element_max) == (-10000, 10000)

    def test_a_scalar_range_bounds_the_value(self) -> None:
        q = GeneratedQuestion.model_validate(OBSERVED)
        target = next(c for c in q.constraints if c.name == "target")
        assert (target.min, target.max) == (-1e9, 1e9)
        assert target.min_length is None

    def test_an_examples_description_is_its_explanation(self) -> None:
        q = GeneratedQuestion.model_validate(OBSERVED)
        assert q.visible_tests[0].explanation == "Example 1"

    def test_invented_keys_are_ignored_not_fatal(self) -> None:
        """question_id, topic and tags are the model's own invention."""
        q = GeneratedQuestion.model_validate(OBSERVED)
        assert q.title == "Steady Hours"


class TestOurOwnShapeStillWorks:
    """The normaliser must not break the shape we actually ask for."""

    def test_explicit_signatures_are_left_alone(self) -> None:
        payload = dict(OBSERVED)
        payload.pop("reference_solution")
        payload["signatures"] = [{
            "language": "typescript",
            "function_name": "steadyHours",
            "scaffold": "export function steadyHours(): number { return 0 }",
            "reference_solution": "export function steadyHours(): number { return 0 }",
        }]
        q = GeneratedQuestion.model_validate(payload)
        assert q.signatures[0].language is Language.TYPESCRIPT

    def test_our_own_constraint_fields_survive(self) -> None:
        payload = dict(OBSERVED)
        payload["constraints"] = [
            {"name": "hours", "min_length": 2, "max_length": 9,
             "element_min": -1, "element_max": 1},
        ]
        q = GeneratedQuestion.model_validate(payload)
        c = q.constraints[0]
        assert (c.min_length, c.max_length, c.element_min, c.element_max) == (2, 9, -1, 1)

    def test_a_lowercase_difficulty_is_untouched(self) -> None:
        payload = dict(OBSERVED) | {"difficulty": "hard"}
        assert GeneratedQuestion.model_validate(payload).difficulty is Difficulty.HARD
