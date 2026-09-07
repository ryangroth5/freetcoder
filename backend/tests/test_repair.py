"""Targeted repair tests. Entirely offline via FakeLLM.

Repair exists because regenerating throws away everything that was right to fix
one broken artifact. These tests pin the two properties that matter: a patch
touches only its target, and the gate stays outside the loop's reach.
"""

from __future__ import annotations

import json

import pytest

from freetcoder.generate.gate import validate_question
from freetcoder.generate.repair import (
    TARGET_FOR,
    PatchError,
    QuestionPatch,
    apply_patch,
    build_repair_prompt,
    repair_question,
)
from freetcoder.llm import FakeLLM, LLMError
from freetcoder.llm.fake import FIXTURE_DIR
from freetcoder.models import GateOutcome, GeneratedQuestion, Language

ALL = [Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT]

GOOD_JS = (
    "function two_sum(nums, target) {\n"
    "  const seen = new Map();\n"
    "  for (let i = 0; i < nums.length; i++) {\n"
    "    if (seen.has(target - nums[i])) return [seen.get(target - nums[i]), i];\n"
    "    seen.set(nums[i], i);\n"
    "  }\n  return [];\n}\nmodule.exports = { two_sum };\n"
)


def load(name: str) -> GeneratedQuestion:
    return GeneratedQuestion.model_validate_json(
        (FIXTURE_DIR / f"{name}.json").read_text()
    )


class TestPatchApplication:
    def test_a_reference_patch_touches_only_that_language(self) -> None:
        q = load("two_sum_multilang")
        python_before = q.signature_for(Language.PYTHON).reference_solution
        statement_before = q.statement_md

        patched = apply_patch(q, QuestionPatch(
            target="reference", language=Language.JAVASCRIPT, content=GOOD_JS))

        assert patched.signature_for(Language.JAVASCRIPT).reference_solution == GOOD_JS
        assert patched.signature_for(Language.PYTHON).reference_solution == python_before
        assert patched.statement_md == statement_before

    def test_the_original_is_never_mutated(self) -> None:
        """A failed round must leave the question we might still fix intact."""
        q = load("two_sum_multilang")
        before = q.model_dump_json()
        apply_patch(q, QuestionPatch(target="generator", content="print('x')"))
        assert q.model_dump_json() == before

    @pytest.mark.parametrize(
        "patch",
        [
            QuestionPatch(target="reference", content="x"),          # no language
            QuestionPatch(target="reference", language=Language.PYTHON),  # no content
            QuestionPatch(target="generator"),                       # no content
            QuestionPatch(target="brute_force"),                     # no content
            QuestionPatch(target="visible_tests"),                   # no cases
            QuestionPatch(target="constraints"),                     # changes nothing
            QuestionPatch(target="whole", content="x"),              # not a patch
        ],
    )
    def test_unusable_patches_are_rejected(self, patch: QuestionPatch) -> None:
        with pytest.raises(PatchError):
            apply_patch(load("two_sum_good"), patch)

    def test_patching_a_language_the_question_lacks_is_rejected(self) -> None:
        with pytest.raises(PatchError):
            apply_patch(load("two_sum_good"), QuestionPatch(
                target="reference", language=Language.GO, content="x"))


class TestOutcomeRouting:
    @pytest.mark.parametrize(
        ("outcome", "target"),
        [
            (GateOutcome.REFERENCE_FAILED, "reference"),
            (GateOutcome.GENERATOR_FAILED, "generator"),
            (GateOutcome.NO_HIDDEN_CASES, "generator"),
            (GateOutcome.BRUTE_FORCE_DISAGREES, "brute_force"),
            (GateOutcome.MISSING_BRUTE_FORCE, "brute_force"),
            (GateOutcome.CONSTRAINT_VIOLATION, "constraints"),
            (GateOutcome.VISIBLE_MISMATCH, "visible_tests"),
        ],
    )
    def test_each_rejection_routes_to_the_right_artifact(
        self, outcome: GateOutcome, target: str
    ) -> None:
        assert TARGET_FOR[outcome] == target

    def test_every_failing_outcome_has_a_route(self) -> None:
        for outcome in GateOutcome:
            if outcome is GateOutcome.ACCEPTED:
                continue
            assert outcome in TARGET_FOR, f"{outcome.value} has no repair target"


class TestRepairPrompt:
    def test_it_carries_the_gate_s_actual_complaint(self) -> None:
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)
        prompt = build_repair_prompt(q, report, "reference", Language.JAVASCRIPT)
        assert "not defined or not exported" in prompt
        assert q.statement_md[:40] in prompt

    def test_a_mismatch_prompt_shows_both_sides(self) -> None:
        """Either the examples or the reference is wrong; the model decides."""
        q = load("two_sum_wrong_oracle")
        report = validate_question(q)
        prompt = build_repair_prompt(q, report, "visible_tests", Language.PYTHON)
        assert "reference solution the harness ran" in prompt
        assert "Either the examples or the reference is wrong" in prompt


class TestRepairLoop:
    async def test_it_fixes_the_failure_that_blocked_a_real_session(self) -> None:
        """A JavaScript reference that does not export its function."""
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)
        assert not report.accepted

        client = FakeLLM([QuestionPatch(
            target="reference", language=Language.JAVASCRIPT, content=GOOD_JS,
            reasoning="exported the function")])
        repaired, final, history = await repair_question(
            client, q, report, languages=ALL)

        assert repaired is not None, final.detail
        assert final.accepted
        assert history == [GateOutcome.ACCEPTED]

    async def test_repair_preserves_the_statement(self) -> None:
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)
        client = FakeLLM([QuestionPatch(
            target="reference", language=Language.JAVASCRIPT, content=GOOD_JS)])
        repaired, _, _ = await repair_question(client, q, report, languages=ALL)
        assert repaired is not None
        assert repaired.statement_md == q.statement_md
        assert repaired.title == q.title

    async def test_it_gives_up_at_the_round_budget(self) -> None:
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)
        useless = QuestionPatch(target="reference", language=Language.JAVASCRIPT,
                                content="function nope() {}\n")
        client = FakeLLM([useless] * 10)
        repaired, _, history = await repair_question(
            client, q, report, languages=ALL, rounds=2)
        assert repaired is None
        assert len(history) == 2

    async def test_an_unusable_patch_does_not_corrupt_the_question(self) -> None:
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)
        client = FakeLLM([
            QuestionPatch(target="reference"),  # missing language and content
            QuestionPatch(target="reference", language=Language.JAVASCRIPT,
                          content=GOOD_JS),
        ])
        repaired, final, history = await repair_question(
            client, q, report, languages=ALL, rounds=3)
        assert repaired is not None and final.accepted
        assert history[0] is GateOutcome.SCHEMA_INVALID

    async def test_a_provider_failure_ends_the_loop_cleanly(self) -> None:
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)
        client = FakeLLM([LLMError("provider down")])
        repaired, final, history = await repair_question(
            client, q, report, languages=ALL)
        assert repaired is None and history == []
        assert final.detail

    async def test_a_generator_fault_is_repaired(self) -> None:
        q = load("two_sum_bad_generator")
        report = validate_question(q)
        assert report.outcome is GateOutcome.NO_HIDDEN_CASES

        working = (
            "import json, random\nrandom.seed(5)\n"
            "for _ in range(12):\n"
            "    nums = [random.randint(-50, 50) for _ in range(8)]\n"
            "    print(json.dumps({'args': {'nums': nums, "
            "'target': nums[0] + nums[1]}}))\n"
        )
        client = FakeLLM([QuestionPatch(target="generator", content=working)])
        repaired, final, _ = await repair_question(client, q, report)
        assert repaired is not None and final.accepted


class TestTheGateIsNotRepairable:
    def test_no_repair_target_can_touch_validation(self) -> None:
        """The gate is the trust anchor.

        A loop that could weaken the validator could make anything pass, and the
        guarantee that questions are provably solvable would be worthless.
        """
        targets = set(TARGET_FOR.values())
        assert targets <= {
            "reference", "scaffold", "generator", "brute_force",
            "visible_tests", "constraints", "clarifications", "statement",
            "whole",
        }

        # Every target names part of the *question*, never part of the gate.
        # `reference` and `scaffold` live on a Signature; the rest are fields of
        # the question itself.
        from freetcoder.models import Signature

        signature_fields = set(Signature.model_fields)
        question_fields = set(GeneratedQuestion.model_fields)
        for target in targets - {"whole", "constraints"}:
            mapped = {
                "reference": "reference_solution",
                "scaffold": "scaffold",
                "generator": "hidden_generator_py",
                "brute_force": "brute_force_py",
                "visible_tests": "visible_tests",
                "clarifications": "clarifications",
                "statement": "statement_md",
            }[target]
            assert mapped in question_fields or mapped in signature_fields

    async def test_a_patch_cannot_alter_gate_behaviour(self) -> None:
        """A repaired question is re-judged by the same unmodified gate."""
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)
        client = FakeLLM([QuestionPatch(
            target="reference", language=Language.JAVASCRIPT,
            content="function two_sum() { return [0, 0]; }\n"
                    "module.exports = { two_sum };\n")])
        repaired, final, _ = await repair_question(
            client, q, report, languages=ALL, rounds=1)
        # A wrong solution stays rejected, however confidently it was patched.
        assert repaired is None
        assert not final.accepted


class TestPipelineIntegration:
    async def test_generation_repairs_before_regenerating(self) -> None:
        from freetcoder.formats import resolve
        from freetcoder.generate import generate_question

        broken = json.loads((FIXTURE_DIR / "two_sum_js_missing_function.json").read_text())
        client = FakeLLM([
            broken,
            QuestionPatch(target="reference", language=Language.JAVASCRIPT,
                          content=GOOD_JS),
        ])
        result = await generate_question(client, resolve("leetcode"))
        assert result.accepted
        assert any(a.repaired for a in result.attempts), (
            "the question should have been repaired, not regenerated"
        )


class TestRepairingClarifications:
    async def test_a_contradicting_clarification_is_repaired(self) -> None:
        """Either the clarification or the code is wrong; the model picks."""
        from freetcoder.models import Clarification

        q = load("most_frequent_word_wrong_clarification")
        report = validate_question(q, languages=ALL)
        assert report.outcome is GateOutcome.CLARIFICATION_WRONG

        corrected = [
            Clarification(question="Does capitalisation matter?",
                          answer="No. 'Hello' and 'hello' are the same word.",
                          probe={"message": "Hello hello"}, expect="hello"),
        ]
        client = FakeLLM([QuestionPatch(target="clarifications",
                                        clarifications=corrected)])
        repaired, final, _ = await repair_question(client, q, report, languages=ALL)

        assert repaired is not None, final.detail
        assert final.accepted
        # Only the clarifications changed.
        assert repaired.statement_md == q.statement_md

    async def test_a_clarifications_patch_without_entries_is_rejected(self) -> None:
        with pytest.raises(PatchError):
            apply_patch(load("most_frequent_word"),
                        QuestionPatch(target="clarifications"))
