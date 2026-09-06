"""Scoring tests, including the platform-native scales."""

from __future__ import annotations

import pytest

from freetcoder.formats import resolve
from freetcoder.scoring import (
    CaseOutcome,
    score_question,
    score_session,
    speed_factor,
)


def cases(passed: int, failed: int = 0, *, over_budget: int = 0) -> list[CaseOutcome]:
    out: list[CaseOutcome] = []
    i = 0
    for _ in range(passed):
        out.append(CaseOutcome(index=i, passed=True, hidden=True,
                               over_budget=over_budget > i))
        i += 1
    for _ in range(failed):
        out.append(CaseOutcome(index=i, passed=False, hidden=True))
        i += 1
    return out


class TestBinaryMode:
    def test_all_cases_passing_scores_full(self) -> None:
        s = score_question(cases(10), resolve("leetcode"))
        assert s.total == 1.0 and s.solved

    def test_one_failure_scores_zero(self) -> None:
        """LeetCode has no partial credit: 9/10 is not accepted."""
        s = score_question(cases(9, 1), resolve("leetcode"))
        assert s.total == 0.0 and not s.solved
        assert s.correctness == pytest.approx(0.9)


class TestProportionalMode:
    def test_partial_credit_is_the_pass_fraction(self) -> None:
        s = score_question(cases(12, 4), resolve("codility", "correctness"))
        assert s.total == pytest.approx(0.75)

    def test_performance_failures_reduce_the_score(self) -> None:
        config = resolve("codility", "performance")
        fast = score_question(cases(10), config)
        slow = score_question(cases(10, over_budget=5), config)
        assert slow.total < fast.total
        assert slow.performance == pytest.approx(0.5)

    def test_performance_is_measured_only_over_correct_cases(self) -> None:
        """A wrong answer must not be penalised twice."""
        s = score_question(cases(4, 6), resolve("codility", "performance"))
        assert s.performance == 1.0
        assert s.correctness == pytest.approx(0.4)


class TestCompositeMode:
    def test_weights_are_applied(self) -> None:
        config = resolve("codesignal_gca")
        s = score_question(cases(10), config, elapsed_s=0, allowed_s=4200)
        assert s.total == pytest.approx(1.0)

    def test_speed_contributes_where_the_platform_rewards_it(self) -> None:
        config = resolve("coderbyte")
        quick = score_question(cases(10), config, elapsed_s=60, allowed_s=3600)
        slow = score_question(cases(10), config, elapsed_s=3600, allowed_s=3600)
        assert quick.total > slow.total

    def test_correct_but_slow_still_earns_most_of_the_credit(self) -> None:
        config = resolve("coderbyte")
        slow = score_question(cases(10), config, elapsed_s=3600, allowed_s=3600)
        assert slow.total > 0.8


class TestSpeedFactor:
    @pytest.mark.parametrize(("used", "expected"), [(0.0, 1.0), (0.25, 1.0), (1.0, 0.4)])
    def test_curve_endpoints(self, used: float, expected: float) -> None:
        assert speed_factor(used * 100, 100) == pytest.approx(expected)

    def test_untimed_formats_get_full_credit(self) -> None:
        assert speed_factor(9999, None) == 1.0

    def test_overrunning_does_not_go_below_the_floor(self) -> None:
        assert speed_factor(10_000, 100) == pytest.approx(0.4)


class TestSessionAggregate:
    def test_gca_reports_on_its_native_200_600_scale(self) -> None:
        config = resolve("codesignal_gca")
        perfect = [score_question(cases(10), config, elapsed_s=0, allowed_s=4200)] * 4
        assert score_session(perfect, config).native == 600

    def test_empty_submission_floors_at_the_bottom_of_the_band(self) -> None:
        config = resolve("codesignal_gca")
        nothing = [score_question(cases(0, 10), config, elapsed_s=4200, allowed_s=4200)] * 4
        result = score_session(nothing, config)
        assert result.native == pytest.approx(200 + 0.2 * 0.4 * 400, abs=2)

    def test_formats_without_a_band_report_percent(self) -> None:
        config = resolve("leetcode")
        result = score_session([score_question(cases(10), config)], config)
        assert result.native == 100 and result.scale_label == "percent"

    def test_solved_count_is_tracked(self) -> None:
        config = resolve("codility")
        scores = [score_question(cases(10), config), score_question(cases(5, 5), config)]
        assert score_session(scores, config).solved_count == 1
