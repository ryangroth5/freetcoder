"""Format resolution tests.

`resolve()` is the single merge point for the picker's three tiers, so these
guard the property that matters: narrowing a format must never silently change
what the format *is*.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from freetcoder.formats import (
    DifficultyLockedError,
    ScoringMode,
    Timing,
    UnknownStyleError,
    load_styles,
    resolve,
    unsupported_topics,
)
from freetcoder.formats.config import FormatConfig, ScoringConfig, SessionConfig
from freetcoder.models import Difficulty

STYLE_IDS = ["leetcode", "codesignal_gca", "codility", "coderbyte"]


class TestPresetsLoad:
    def test_all_four_styles_are_present(self) -> None:
        assert sorted(load_styles()) == sorted(STYLE_IDS)

    @pytest.mark.parametrize("style_id", STYLE_IDS)
    def test_every_style_resolves_and_summarises(self, style_id: str) -> None:
        config = resolve(style_id)
        assert config.id == style_id
        assert config.summary()

    @pytest.mark.parametrize("style_id", STYLE_IDS)
    def test_every_preset_of_every_style_resolves(self, style_id: str) -> None:
        """Guards against a preset whose overrides produce an invalid config."""
        for preset in load_styles()[style_id].presets:
            config = resolve(style_id, preset.id)
            assert config.session.question_count >= 1

    def test_unknown_style_is_rejected(self) -> None:
        with pytest.raises(UnknownStyleError):
            resolve("hackerrank")

    def test_unknown_preset_is_rejected(self) -> None:
        with pytest.raises(KeyError):
            resolve("leetcode", "does_not_exist")


class TestStylesKeepTheirIdentity:
    """Each style must actually encode what makes that platform distinctive."""

    def test_gca_is_four_ascending_questions_in_seventy_minutes(self) -> None:
        c = resolve("codesignal_gca")
        assert c.session.question_count == 4
        assert c.session.total_seconds == 4200
        assert c.session.difficulty_curve == [
            Difficulty.EASY, Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD
        ]
        assert c.scoring.score_range == (200, 600)

    def test_gca_gives_no_hints(self) -> None:
        """The real assessment offers none; hints would flatter the score."""
        assert resolve("codesignal_gca").generation.give_hints is False

    def test_codility_grades_performance_with_a_stated_target(self) -> None:
        c = resolve("codility")
        assert c.scoring.perf_tests is True
        assert c.generation.state_complexity_target is True
        assert c.scoring.mode is ScoringMode.PROPORTIONAL

    def test_leetcode_is_untimed_and_self_paced(self) -> None:
        c = resolve("leetcode")
        assert c.session.timing is Timing.UNTIMED
        assert c.session.question_count == 1

    def test_coderbyte_rewards_speed(self) -> None:
        c = resolve("coderbyte")
        assert c.scoring.mode is ScoringMode.COMPOSITE
        assert c.scoring.weight_speed > 0


class TestDifficultyLocking:
    def test_self_paced_style_accepts_a_difficulty_choice(self) -> None:
        c = resolve("leetcode", difficulty=Difficulty.HARD)
        assert c.session.difficulty_curve == [Difficulty.HARD]

    def test_fixed_format_refuses_a_difficulty_override(self) -> None:
        """A GCA whose difficulty you choose is not simulating a GCA."""
        with pytest.raises(DifficultyLockedError):
            resolve("codesignal_gca", difficulty=Difficulty.HARD)

    def test_difficulty_applies_to_every_question(self) -> None:
        c = resolve("coderbyte", "sprint", difficulty=Difficulty.EASY)
        assert c.session.difficulty_curve == [Difficulty.EASY] * 5


class TestTierPrecedence:
    def test_preset_narrows_the_style(self) -> None:
        base = resolve("coderbyte")
        sprint = resolve("coderbyte", "sprint")
        assert sprint.session.question_count == 5 != base.session.question_count
        assert sprint.session.total_seconds == 1800

    def test_topics_reach_the_generation_config(self) -> None:
        c = resolve("leetcode", topics=["Trees & Graphs", "  Recursion "])
        assert c.generation.topics == ["trees & graphs", "recursion"]

    def test_freeform_is_carried_as_an_extra_constraint(self) -> None:
        c = resolve("codility", freeform="  focus on sliding window  ")
        assert c.generation.freeform == "focus on sliding window"

    def test_freeform_cannot_rewrite_structural_rules(self) -> None:
        """This is the whole point: 'GCA' must still mean GCA afterwards."""
        c = resolve(
            "codesignal_gca",
            freeform="make this a single untimed easy question with 1 test",
        )
        assert c.session.question_count == 4
        assert c.session.total_seconds == 4200
        assert c.session.timing is Timing.TOTAL

    def test_preset_topics_survive_when_no_topics_given(self) -> None:
        assert "prefix sums" in resolve("codility", "lesson_arrays").generation.topics

    def test_explicit_topics_override_preset_topics(self) -> None:
        c = resolve("codility", "lesson_arrays", topics=["math"])
        assert c.generation.topics == ["math"]


class TestUnsupportedConcentrations:
    @pytest.mark.parametrize("topic", ["sql", "system design"])
    def test_flags_topics_the_pipeline_cannot_serve(self, topic: str) -> None:
        flagged = unsupported_topics([topic])
        assert topic in flagged and flagged[topic]

    def test_ordinary_topics_are_not_flagged(self) -> None:
        assert unsupported_topics(["arrays & strings", "recursion"]) == {}


class TestConfigValidation:
    def test_total_timing_requires_a_clock(self) -> None:
        with pytest.raises(ValidationError):
            SessionConfig(timing=Timing.TOTAL)

    def test_difficulty_curve_must_match_question_count(self) -> None:
        with pytest.raises(ValidationError):
            SessionConfig(question_count=3, difficulty_curve=[Difficulty.EASY])

    def test_composite_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValidationError):
            ScoringConfig(
                mode=ScoringMode.COMPOSITE,
                weight_correctness=0.5,
                weight_speed=0.9,
                weight_performance=0.0,
            )

    def test_valid_composite_weights_are_accepted(self) -> None:
        assert ScoringConfig(
            mode=ScoringMode.COMPOSITE,
            weight_correctness=0.6,
            weight_speed=0.2,
            weight_performance=0.2,
        ).mode is ScoringMode.COMPOSITE

    def test_summary_mentions_performance_grading(self) -> None:
        assert "performance" in resolve("codility").summary()

    def test_config_roundtrips_through_json(self) -> None:
        c = resolve("codesignal_gca")
        assert FormatConfig.model_validate_json(c.model_dump_json()) == c


class TestPresetIntentReachesGeneration:
    """A preset's *effects* always reached the config; its intent did not.

    Without this, "Blind 75 style" and "Top interview 150" produced near-identical
    prompts differing only in topic lists.
    """

    @pytest.mark.parametrize("style_id", STYLE_IDS)
    def test_every_preset_declares_an_intent(self, style_id: str) -> None:
        for preset in load_styles()[style_id].presets:
            assert preset.intent, f"{style_id}/{preset.id} has no intent"

    def test_intent_and_id_reach_the_generation_config(self) -> None:
        c = resolve("leetcode", "blind75")
        assert c.generation.preset_id == "blind75"
        assert "Blind 75" in c.generation.preset_intent

    def test_no_preset_leaves_the_fields_empty(self) -> None:
        c = resolve("leetcode")
        assert c.generation.preset_id == "" and c.generation.preset_intent == ""

    def test_label_is_used_when_a_preset_omits_an_intent(self) -> None:
        from freetcoder.formats.registry import StylePreset, _apply_preset

        c = resolve("leetcode")
        _apply_preset(c, StylePreset(id="x", label="Fallback label"))
        assert c.generation.preset_intent == "Fallback label"

    def test_sibling_presets_produce_different_prompts(self) -> None:
        from freetcoder.generate import build_user_prompt

        blind = build_user_prompt(resolve("leetcode", "blind75"), Difficulty.MEDIUM)
        top150 = build_user_prompt(resolve("leetcode", "interview150"), Difficulty.MEDIUM)
        assert blind != top150
        assert "Blind 75" in blind and "Blind 75" not in top150

    def test_cache_key_separates_sibling_presets(self) -> None:
        """Two presets of one style must not share cached questions."""
        from freetcoder.models import Language
        from freetcoder.storage import cache_key

        assert cache_key(resolve("leetcode", "blind75"), "medium", Language.PYTHON) != (
            cache_key(resolve("leetcode", "interview150"), "medium", Language.PYTHON)
        )
