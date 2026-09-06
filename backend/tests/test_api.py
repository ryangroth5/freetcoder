"""API lifecycle tests, in-process over ASGI. No network, no API key."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from freetcoder.llm import FakeLLM

from .conftest import SOLUTION, WRONG_SOLUTION


async def start_session(client: AsyncClient, style: str = "leetcode", **kw) -> str:
    resp = await client.post("/api/sessions", json={"style": style, **kw})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestDiscovery:
    async def test_health(self, client: AsyncClient) -> None:
        assert (await client.get("/api/health")).json()["ok"] is True

    async def test_formats_lists_all_styles_with_presets(self, client: AsyncClient) -> None:
        data = (await client.get("/api/formats")).json()
        assert {f["id"] for f in data} == {
            "leetcode", "codesignal_gca", "codility", "coderbyte"
        }
        gca = next(f for f in data if f["id"] == "codesignal_gca")
        assert gca["difficulty_locked"] is True
        assert gca["presets"]

    async def test_topics_flags_the_unsupported_ones(self, client: AsyncClient) -> None:
        data = (await client.get("/api/topics")).json()
        assert "sql" in data["unsupported"]
        assert "system design" in data["unsupported"]

    async def test_preview_resolves_the_three_tiers(self, client: AsyncClient) -> None:
        resp = await client.post("/api/formats/preview", json={
            "style": "codility", "preset": "performance", "topics": ["arrays & strings"],
        })
        body = resp.json()
        assert body["config"]["scoring"]["perf_tests"] is True
        assert "performance" in body["summary"]

    async def test_preview_warns_about_unsupported_topics(self, client: AsyncClient) -> None:
        resp = await client.post("/api/formats/preview",
                                 json={"style": "leetcode", "topics": ["sql"]})
        assert resp.json()["warnings"]

    async def test_locked_difficulty_override_is_rejected(self, client: AsyncClient) -> None:
        resp = await client.post("/api/formats/preview",
                                 json={"style": "codesignal_gca", "difficulty": "hard"})
        assert resp.status_code == 409

    async def test_unknown_style_is_404(self, client: AsyncClient) -> None:
        resp = await client.post("/api/formats/preview", json={"style": "nope"})
        assert resp.status_code == 404


class TestSessionLifecycle:
    async def test_create_and_fetch_question(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        q = (await client.get(f"/api/sessions/{sid}/questions/0")).json()
        assert q["title"] == "Two Sum"
        assert q["scaffold"].startswith("def two_sum")
        assert len(q["visible_tests"]) == 2
        assert q["hidden_test_count"] >= 10

    async def test_hidden_cases_are_never_sent_to_the_client(
        self, client: AsyncClient
    ) -> None:
        """The whole point of a hidden set is that it stays hidden."""
        sid = await start_session(client)
        body = (await client.get(f"/api/sessions/{sid}/questions/0")).text
        assert "hidden_tests" not in body

    async def test_run_uses_visible_cases_only(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        resp = await client.post(f"/api/sessions/{sid}/questions/0/run",
                                 json={"source": SOLUTION})
        body = resp.json()
        assert body["verdict"] == "ok"
        assert body["total"] == 2
        assert all(c["args"] is not None for c in body["cases"])

    async def test_run_is_not_scored(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/run",
                                  json={"source": SOLUTION})).json()
        assert "score" not in body

    async def test_run_accepts_a_user_supplied_case(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": SOLUTION,
                  "extra_cases": [{"args": {"nums": [1, 2], "target": 3},
                                   "expected": [0, 1]}]},
        )).json()
        assert body["total"] == 3

    async def test_submit_runs_hidden_cases_and_scores(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": SOLUTION})).json()
        assert body["verdict"] == "ok"
        assert body["total"] > 10
        assert body["score"]["solved"] is True

    async def test_wrong_submission_reports_a_counterexample(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": WRONG_SOLUTION})).json()
        assert body["verdict"] == "wrong_answer"
        failure = body["first_failure"]
        assert failure and failure["args"] and failure["expected"] is not None

    async def test_submit_reveals_the_reference_solution(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": SOLUTION})).json()
        assert "def two_sum" in body["reference_solution"]

    async def test_hidden_case_inputs_stay_masked_after_submit(
        self, client: AsyncClient
    ) -> None:
        """Only the single failing case is revealed, as a counterexample."""
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": SOLUTION})).json()
        assert all(c["args"] is None for c in body["cases"])

    async def test_syntax_error_is_reported_as_compile_error(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/run",
                                  json={"source": "def two_sum(:"})).json()
        assert body["verdict"] == "compile_error"

    async def test_infinite_loop_submission_does_not_hang_the_api(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": "def two_sum(nums, target):\n    while True: pass\n"},
        )).json()
        assert body["verdict"] == "timeout"


class TestSkipAndResults:
    async def test_skip_is_allowed_and_reveals_the_solution(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/skip")).json()
        assert body["skipped"] and "def two_sum" in body["reference_solution"]

    async def test_results_use_the_native_scale(self, client: AsyncClient) -> None:
        sid = await start_session(client, style="codesignal_gca")
        await client.post(f"/api/sessions/{sid}/questions/0/submit",
                          json={"source": SOLUTION})
        body = (await client.get(f"/api/sessions/{sid}/results")).json()
        assert body["scale"] == "200-600"
        assert 200 <= body["score"] <= 600
        assert body["question_count"] == 4

    async def test_leetcode_results_are_a_percentage(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        await client.post(f"/api/sessions/{sid}/questions/0/submit",
                          json={"source": SOLUTION})
        body = (await client.get(f"/api/sessions/{sid}/results")).json()
        assert body["scale"] == "percent" and body["score"] == 100


class TestMultiQuestionSessions:
    async def test_gca_session_exposes_four_questions(self, client: AsyncClient) -> None:
        sid = await start_session(client, style="codesignal_gca")
        assert (await client.get(f"/api/sessions/{sid}")).json()["question_count"] == 4

    async def test_later_questions_generate_on_demand(self, client: AsyncClient) -> None:
        sid = await start_session(client, style="codesignal_gca")
        resp = await client.get(f"/api/sessions/{sid}/questions/2")
        assert resp.status_code == 200
        assert resp.json()["index"] == 2

    async def test_index_beyond_the_format_is_404(self, client: AsyncClient) -> None:
        sid = await start_session(client, style="leetcode")
        assert (await client.get(f"/api/sessions/{sid}/questions/5")).status_code == 404

    async def test_timed_session_reports_a_countdown(self, client: AsyncClient) -> None:
        sid = await start_session(client, style="codesignal_gca")
        remaining = (await client.get(f"/api/sessions/{sid}")).json()["remaining_seconds"]
        assert remaining is not None and 0 < remaining <= 4200

    async def test_untimed_session_has_no_countdown(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        assert (await client.get(f"/api/sessions/{sid}")).json()["remaining_seconds"] is None


class TestHintPolicy:
    async def test_leetcode_exposes_a_hint(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        assert (await client.get(f"/api/sessions/{sid}/questions/0")).json()["hint_md"]

    async def test_gca_withholds_the_hint_entirely(self, client: AsyncClient) -> None:
        """The fixture has one; the format must suppress it."""
        sid = await start_session(client, style="codesignal_gca")
        assert (await client.get(f"/api/sessions/{sid}/questions/0")).json()["hint_md"] is None


class TestErrors:
    async def test_unknown_session_is_404(self, client: AsyncClient) -> None:
        assert (await client.get("/api/sessions/deadbeef")).status_code == 404

    @pytest.mark.parametrize("path", ["run", "submit"])
    async def test_action_on_unknown_session_is_404(
        self, client: AsyncClient, path: str
    ) -> None:
        resp = await client.post(f"/api/sessions/nope/questions/0/{path}",
                                 json={"source": SOLUTION})
        assert resp.status_code == 404


class TestPrintDebugging:
    """Regression: `print()` in a submission used to return 500.

    The payloads here are the ones a candidate actually types when debugging.
    """

    async def test_print_returns_200_and_surfaces_the_output(
        self, client: AsyncClient
    ) -> None:
        """The exact body from the bug report."""
        sid = await start_session(client)
        resp = await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": "def two_sum(nums, target):\n    print(nums);\n    pass\n",
                  "extra_cases": []},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The solution returns None, so it is wrong -- but it must not crash.
        assert body["verdict"] == "wrong_answer"
        assert "[2, 7, 11, 15]" in body["cases"][0]["stdout"]

    async def test_printing_a_json_object_does_not_misgrade(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        source = (
            "import json\n"
            "def two_sum(nums, target):\n"
            "    print(json.dumps({'ok': True, 'value': [9, 9], 'ms': 0}))\n"
            "    seen = {}\n"
            "    for i, n in enumerate(nums):\n"
            "        if target - n in seen:\n"
            "            return [seen[target - n], i]\n"
            "        seen[n] = i\n"
            "    return []\n"
        )
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": source})).json()
        assert body["verdict"] == "ok", "a printed object was counted as a result"
        assert body["score"]["solved"] is True

    async def test_stdout_reaches_the_failing_case_detail(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": "def two_sum(nums, target):\n"
                            "    print('here', target)\n"
                            "    return [0, 0]\n"},
        )).json()
        assert "here 9" in body["first_failure"]["stdout"]

    async def test_output_flood_stays_bounded(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": "def two_sum(nums, target):\n"
                            "    print('X' * 500000)\n"
                            "    return [0, 1]\n"},
        )).json()
        assert len(body["cases"][0]["stdout"]) < 10_000


class TestMultiLanguage:
    """Every offered language must be runnable, and only the offered ones."""

    async def test_question_exposes_a_signature_per_offered_language(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        q = (await client.get(f"/api/sessions/{sid}/questions/0")).json()
        assert set(q["languages"]) == {"python", "javascript", "typescript"}
        assert {s["language"] for s in q["signatures"]} == set(q["languages"])
        for sig in q["signatures"]:
            assert sig["scaffold"] and sig["function_name"] == "two_sum"

    async def test_solves_in_javascript(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        source = (
            "function two_sum(nums, target) {\n"
            "  const seen = new Map();\n"
            "  for (let i = 0; i < nums.length; i++) {\n"
            "    if (seen.has(target - nums[i])) "
            "return [seen.get(target - nums[i]), i];\n"
            "    seen.set(nums[i], i);\n"
            "  }\n  return [];\n}\nmodule.exports = { two_sum };\n"
        )
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": source,
                                        "language": "javascript"})).json()
        assert body["verdict"] == "ok", str(body)[:300]
        assert body["score"]["solved"] is True

    async def test_solves_in_typescript(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        source = (
            "export function two_sum(nums: number[], target: number): number[] {\n"
            "  const seen = new Map<number, number>();\n"
            "  for (let i = 0; i < nums.length; i++) {\n"
            "    const need = target - nums[i];\n"
            "    if (seen.has(need)) return [seen.get(need) as number, i];\n"
            "    seen.set(nums[i], i);\n"
            "  }\n  return [];\n}\n"
        )
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": source,
                                        "language": "typescript"})).json()
        assert body["verdict"] == "ok", str(body)[:300]

    async def test_typescript_type_error_is_a_compile_error(
        self, client: AsyncClient
    ) -> None:
        """The reason to offer TypeScript at all: types are checked."""
        sid = await start_session(client)
        source = (
            "export function two_sum(nums: number[], target: number): number[] {\n"
            '  return "definitely not an array";\n}\n'
        )
        body = (await client.post(f"/api/sessions/{sid}/questions/0/run",
                                  json={"source": source,
                                        "language": "typescript"})).json()
        assert body["verdict"] == "compile_error"
        assert "not assignable" in body["stderr"]

    async def test_console_log_is_captured_in_javascript(
        self, client: AsyncClient
    ) -> None:
        """The print bug, in the language where it would have recurred."""
        sid = await start_session(client)
        source = (
            "function two_sum(nums, target) {\n"
            "  console.log(JSON.stringify(nums));\n"
            "  return [0, 1];\n}\nmodule.exports = { two_sum };\n"
        )
        body = (await client.post(f"/api/sessions/{sid}/questions/0/run",
                                  json={"source": source,
                                        "language": "javascript"})).json()
        assert body["verdict"] in ("ok", "wrong_answer")
        assert "[2,7,11,15]" in body["cases"][0]["stdout"]

    async def test_language_the_format_does_not_offer_is_409(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        resp = await client.post(f"/api/sessions/{sid}/questions/0/run",
                                 json={"source": "package main", "language": "go"})
        assert resp.status_code == 409
        assert "not offered" in resp.json()["detail"]


class TestQuestionCacheKey:
    async def test_cache_key_includes_the_offered_languages(self) -> None:
        """A question cached under fewer languages must not be replayed for more.

        Otherwise the UI offers a language whose signature the cached question
        does not carry.
        """
        from freetcoder.formats import resolve
        from freetcoder.models import Language
        from freetcoder.storage import cache_key

        config = resolve("leetcode")
        few = config.model_copy(deep=True)
        few.environment.languages = [Language.PYTHON]
        assert cache_key(config, "easy", Language.PYTHON) != cache_key(
            few, "easy", Language.PYTHON
        )


class TestEditableTestCases:
    """The Testcase tab is an editor: unlimited cases, expected optional.

    A case with no expected value is run and reported but never judged -- and
    must never be able to influence a score.
    """

    async def test_replacing_the_case_list_is_honoured(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": SOLUTION, "cases": [
                {"args": {"nums": [1, 2], "target": 3}, "expected": [0, 1],
                 "assert_expected": True},
            ]},
        )).json()
        assert body["total"] == 1
        assert body["verdict"] == "ok"

    async def test_unlimited_cases(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        cases = [
            {"args": {"nums": [i, 1, i + 1], "target": 2 * i + 1},
             "expected": [0, 2], "assert_expected": True}
            for i in range(1, 26)
        ]
        body = (await client.post(f"/api/sessions/{sid}/questions/0/run",
                                  json={"source": SOLUTION, "cases": cases})).json()
        assert body["total"] == 25

    async def test_case_without_expected_is_run_but_not_judged(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": SOLUTION, "cases": [
                {"args": {"nums": [3, 3], "target": 6}, "assert_expected": False},
            ]},
        )).json()
        case = body["cases"][0]
        assert case["judged"] is False
        assert case["actual"] == [0, 1], "the output must be shown"
        assert body["verdict"] == "ok", "an unjudged case cannot be a wrong answer"
        assert body["first_failure"] is None

    async def test_expected_null_is_distinct_from_no_expected(
        self, client: AsyncClient
    ) -> None:
        """`expected: null` is a real assertion; omitting it is not."""
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": SOLUTION, "cases": [
                {"args": {"nums": [3, 3], "target": 6}, "expected": None,
                 "assert_expected": True},
            ]},
        )).json()
        assert body["cases"][0]["judged"] is True
        assert body["verdict"] == "wrong_answer"

    async def test_an_unjudged_case_that_crashes_is_still_reported(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": "def two_sum(nums, target):\n    raise ValueError('x')\n",
                  "cases": [{"args": {"nums": [1, 2], "target": 3},
                             "assert_expected": False}]},
        )).json()
        assert body["cases"][0]["passed"] is False
        assert body["verdict"] == "wrong_answer"

    async def test_edited_examples_replace_the_originals(
        self, client: AsyncClient
    ) -> None:
        """Editing a provided example changes what runs, not just what is shown."""
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": SOLUTION, "cases": [
                {"args": {"nums": [5, 5], "target": 10}, "expected": [9, 9],
                 "assert_expected": True},
            ]},
        )).json()
        assert body["verdict"] == "wrong_answer"
        assert body["first_failure"]["args"]["nums"] == [5, 5]

    async def test_custom_cases_never_affect_the_score(
        self, client: AsyncClient
    ) -> None:
        """Run is unscored, and Submit ignores the candidate's own cases."""
        sid = await start_session(client)
        await client.post(f"/api/sessions/{sid}/questions/0/run",
                          json={"source": WRONG_SOLUTION, "cases": [
                              {"args": {"nums": [1, 2], "target": 3},
                               "expected": [0, 0], "assert_expected": True}]})
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": SOLUTION})).json()
        assert body["score"]["solved"] is True

    async def test_hidden_outputs_are_never_revealed_on_submit(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": SOLUTION})).json()
        assert all(c["actual"] is None for c in body["cases"])

    async def test_legacy_extra_cases_still_work(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        body = (await client.post(
            f"/api/sessions/{sid}/questions/0/run",
            json={"source": SOLUTION,
                  "extra_cases": [{"args": {"nums": [1, 2], "target": 3},
                                   "expected": [0, 1]}]},
        )).json()
        assert body["total"] == 3
        assert body["cases"][2]["judged"] is True


class TestLibraryResilience:
    """The library must never be able to break local practice.

    These matter more than the happy path: an external dependency that can take
    down offline practice would be a bad trade.
    """

    @pytest.fixture(autouse=True)
    def _dead_library(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from freetcoder.settings import get_settings

        monkeypatch.setenv("FREETCODER_LIBRARY_URL", "http://127.0.0.1:9")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    async def test_sessions_start_with_an_unreachable_library(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        assert (await client.get(f"/api/sessions/{sid}/questions/0")).status_code == 200

    async def test_run_and_submit_still_work(self, client: AsyncClient) -> None:
        sid = await start_session(client)
        run = await client.post(f"/api/sessions/{sid}/questions/0/run",
                                json={"source": SOLUTION})
        submit = await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                   json={"source": SOLUTION})
        assert run.json()["verdict"] == "ok"
        assert submit.json()["score"]["solved"] is True

    async def test_submit_reports_no_percentile_rather_than_failing(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": SOLUTION})).json()
        assert "percentile" not in body
        assert body["ratio"] is not None, "local timing does not need the library"

    async def test_browsing_reads_as_empty(self, client: AsyncClient) -> None:
        body = (await client.get("/api/library/questions")).json()
        assert body == {"questions": [], "total": 0}

    async def test_publishing_reports_a_clear_failure(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        resp = await client.post(f"/api/sessions/{sid}/questions/0/publish")
        assert resp.status_code == 503
        assert "unreachable" in resp.json()["detail"]

    async def test_status_reports_unreachable(self, client: AsyncClient) -> None:
        body = (await client.get("/api/library/status")).json()
        assert body["configured"] is True and body["reachable"] is False


class TestLibraryNotConfigured:
    async def test_status_when_no_url_is_set(self, client: AsyncClient) -> None:
        body = (await client.get("/api/library/status")).json()
        assert body["configured"] is False


class TestRelativePerformance:
    async def test_a_correct_submission_reports_a_ratio(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": SOLUTION})).json()
        assert body["ratio"] is not None and body["ratio"] > 0
        assert body["total_ms"] >= 0

    async def test_a_wrong_submission_is_not_ranked(
        self, client: AsyncClient
    ) -> None:
        """Timing a failure would reward failing fast."""
        sid = await start_session(client)
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": WRONG_SOLUTION})).json()
        assert body["ratio"] is None

    async def test_a_slow_solution_scores_a_worse_ratio_than_a_fast_one(
        self, client: AsyncClient
    ) -> None:
        """The ratio must reflect the code, not the machine."""
        slow = (
            "def two_sum(nums, target):\n"
            "    for i in range(len(nums)):\n"
            "        for j in range(i + 1, len(nums)):\n"
            "            for _ in range(60):\n"
            "                pass\n"
            "            if nums[i] + nums[j] == target:\n"
            "                return [i, j]\n"
            "    return []\n"
        )
        sid = await start_session(client)
        fast = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": SOLUTION})).json()
        slow_body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                       json={"source": slow})).json()
        assert slow_body["ratio"] > fast["ratio"]


class TestQuestionVariety:
    """Variety is the product.

    An earlier version always preferred the cache, so a given selection
    generated one question and replayed it forever. The cache is a fallback for
    when generation is impossible, not the default source.
    """

    async def test_each_session_generates_a_new_question(
        self, client: AsyncClient, fake_llm: FakeLLM
    ) -> None:
        before = len(fake_llm.calls)
        for _ in range(3):
            await start_session(client)
        assert len(fake_llm.calls) >= before + 3, (
            "sessions were served from the cache instead of generating"
        )

    async def test_falls_back_to_the_cache_when_generation_fails(
        self, client: AsyncClient, fake_llm: FakeLLM
    ) -> None:
        """A dead provider must not stop practice: replay beats nothing."""
        sid = await start_session(client)
        first = (await client.get(f"/api/sessions/{sid}/questions/0")).json()

        # Every subsequent generation attempt now fails.
        from freetcoder.llm import LLMError

        fake_llm.queue(*[LLMError("provider down") for _ in range(8)])
        while not fake_llm.exhausted and len(fake_llm.calls) < 40:
            resp = await client.post("/api/sessions", json={"style": "leetcode"})
            if resp.status_code == 200:
                sid2 = resp.json()["id"]
                cached = (await client.get(
                    f"/api/sessions/{sid2}/questions/0")).json()
                assert cached["title"] == first["title"]
                return
        raise AssertionError("the cache fallback never produced a question")

    async def test_no_llm_and_no_cache_is_a_clear_error(
        self, client: AsyncClient, fake_llm: FakeLLM
    ) -> None:
        fake_llm._queue.clear()  # noqa: SLF001 - simulating "nothing configured"
        resp = await client.post("/api/sessions", json={"style": "leetcode"})
        assert resp.status_code in (428, 502)


class TestCaseLimits:
    async def test_too_many_cases_is_rejected_rather_than_executed(
        self, client: AsyncClient
    ) -> None:
        """Every case is executed, so an unbounded list ties up the runner."""
        sid = await start_session(client)
        cases = [{"args": {"nums": [1, 2], "target": 3}, "assert_expected": False}
                 for _ in range(500)]
        resp = await client.post(f"/api/sessions/{sid}/questions/0/run",
                                 json={"source": SOLUTION, "cases": cases})
        assert resp.status_code == 422

    async def test_the_cap_is_generous_enough_for_real_use(
        self, client: AsyncClient
    ) -> None:
        sid = await start_session(client)
        cases = [{"args": {"nums": [1, 2], "target": 3}, "expected": [0, 1],
                  "assert_expected": True} for _ in range(50)]
        resp = await client.post(f"/api/sessions/{sid}/questions/0/run",
                                 json={"source": SOLUTION, "cases": cases})
        assert resp.status_code == 200
        assert resp.json()["total"] == 50


class TestPerLanguagePerformanceBudget:
    async def test_javascript_is_budgeted_against_a_javascript_reference(
        self, client: AsyncClient
    ) -> None:
        """Judging JS by a Python-derived number measures the runtime, not the code."""
        sid = await start_session(client, style="codility")
        q = (await client.get(f"/api/sessions/{sid}/questions/0")).json()
        assert q["title"]

        source = (
            "function two_sum(nums, target) {\n"
            "  const seen = new Map();\n"
            "  for (let i = 0; i < nums.length; i++) {\n"
            "    if (seen.has(target - nums[i])) "
            "return [seen.get(target - nums[i]), i];\n"
            "    seen.set(nums[i], i);\n"
            "  }\n  return [];\n}\nmodule.exports = { two_sum };\n"
        )
        body = (await client.post(f"/api/sessions/{sid}/questions/0/submit",
                                  json={"source": source,
                                        "language": "javascript"})).json()
        assert body["verdict"] == "ok", str(body)[:300]
        # A correct JS solution must not be flagged slow purely for being JS.
        assert not any(c["over_budget"] for c in body["cases"]), (
            "a correct JavaScript solution was judged over budget"
        )
