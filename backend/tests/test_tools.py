"""Tool-calling tests.

The property that matters most: tools run through the *same* sandbox as a
candidate's submission. Model-written code is exactly as untrusted as
candidate-written code, and a second execution path would quietly undo the
containment the runner provides.
"""

from __future__ import annotations

import pytest

from freetcoder.generate.gate import validate_question
from freetcoder.generate.repair import QuestionPatch, repair_question
from freetcoder.generate.tools import TOOL_SCHEMAS, dispatch, run_against_cases, run_code
from freetcoder.llm import FakeLLM, ToolCall
from freetcoder.llm.fake import FIXTURE_DIR
from freetcoder.models import GeneratedQuestion, Language

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


class TestToolsRunInTheSandbox:
    """No second execution path. These would fail if tools bypassed the runner."""

    def test_network_access_is_blocked(self) -> None:
        out = run_code(
            "python",
            "import socket\nsocket.create_connection(('1.1.1.1', 80), timeout=2)\n",
        )
        assert "runtime_error" in out
        assert "PermissionError" in out or "Operation not permitted" in out

    def test_an_infinite_loop_is_killed(self, quick_kill: None) -> None:
        assert "timeout" in run_code("python", "while True: pass")

    def test_writes_outside_the_workspace_are_denied(self) -> None:
        out = run_code("python", "open('/etc/passwd', 'w').write('x')")
        assert "PermissionError" in out


class TestRunCode:
    def test_it_reports_stdout(self) -> None:
        assert "42" in run_code("python", "print(6 * 7)")

    def test_it_reports_errors(self) -> None:
        out = run_code("python", "raise ValueError('boom')")
        assert "boom" in out

    def test_it_works_for_every_language(self) -> None:
        assert "42" in run_code("javascript", "console.log(6 * 7);")
        assert "42" in run_code("typescript", "const x: number = 42;\nconsole.log(x);")

    def test_an_unknown_language_is_reported_not_raised(self) -> None:
        assert "unknown language" in run_code("cobol", "x")

    def test_output_is_truncated(self) -> None:
        out = run_code("python", "print('A' * 500000)")
        assert len(out) < 20_000


class TestRunAgainstCases:
    def test_it_shows_what_each_case_returned(self) -> None:
        out = run_against_cases(
            "python", "def f(x):\n    return x * 2\n", "f",
            [{"args": {"x": 3}}, {"args": {"x": 5}}],
        )
        assert "-> 6" in out and "-> 10" in out

    def test_it_surfaces_a_raise_per_case(self) -> None:
        out = run_against_cases(
            "python", "def f(x):\n    raise ValueError('nope')\n", "f",
            [{"args": {"x": 1}}],
        )
        assert "RAISED" in out and "nope" in out

    def test_it_surfaces_printed_output(self) -> None:
        out = run_against_cases(
            "python", "def f(x):\n    print('probe', x)\n    return x\n", "f",
            [{"args": {"x": 7}}],
        )
        assert "printed: probe 7" in out

    def test_an_unexported_javascript_function_is_diagnosed(self) -> None:
        out = run_against_cases(
            "javascript", "function other(x) { return x; }", "f", [{"args": {"x": 1}}]
        )
        assert "not defined or not exported" in out or "no results" in out

    def test_no_cases_is_reported(self) -> None:
        assert "no cases" in run_against_cases("python", "def f(): pass", "f", [])


class TestDispatch:
    def test_unknown_tools_are_reported_not_raised(self) -> None:
        assert "unknown tool" in dispatch("rm_rf", {})

    def test_bad_arguments_are_reported_not_raised(self) -> None:
        assert "bad arguments" in dispatch("run_code", {"nonsense": 1})

    def test_the_schemas_match_the_dispatch_table(self) -> None:
        declared = {t["function"]["name"] for t in TOOL_SCHEMAS}
        assert declared == {"run_code", "run_against_cases"}


class TestRepairWithTools:
    async def test_the_model_can_test_its_fix_before_returning_it(self) -> None:
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)

        client = FakeLLM([
            ToolCall("run_against_cases", {
                "language": "javascript", "source": GOOD_JS,
                "function_name": "two_sum",
                "cases": [{"args": {"nums": [2, 7, 11, 15], "target": 9}}],
            }),
            QuestionPatch(target="reference", language=Language.JAVASCRIPT,
                          content=GOOD_JS),
        ])
        repaired, final, _ = await repair_question(client, q, report, languages=ALL)

        assert repaired is not None and final.accepted
        assert client.tool_calls[0][0] == "run_against_cases"
        # The tool really executed, so the model saw a genuine result.
        assert "-> [0, 1]" in client.tool_results[0]

    async def test_a_provider_without_tool_support_still_repairs(self) -> None:
        """Local models vary; the feedback loop must work regardless."""
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)

        client = FakeLLM([QuestionPatch(
            target="reference", language=Language.JAVASCRIPT, content=GOOD_JS)])
        client.supports_tools = False

        repaired, final, _ = await repair_question(client, q, report, languages=ALL)
        assert repaired is not None and final.accepted
        assert client.tool_calls == []

    async def test_the_tool_budget_is_enforced(self) -> None:
        q = load("two_sum_js_missing_function")
        report = validate_question(q, languages=ALL)
        probe = ToolCall("run_code", {"language": "python", "source": "print(1)"})
        client = FakeLLM([probe] * 20)

        repaired, _, _ = await repair_question(
            client, q, report, languages=ALL, rounds=1, tool_budget=3)
        assert repaired is None
        assert len(client.tool_calls) == 3, "the budget did not stop the loop"

    @pytest.mark.parametrize("language", ["python", "javascript", "typescript"])
    async def test_tools_are_available_for_every_language(self, language: str) -> None:
        assert "verdict" in dispatch(
            "run_code",
            {"language": language,
             "source": "console.log(1);" if language != "python" else "print(1)"},
        )


class TestErrorsAreReadable:
    """A traceback's value is at the end, so truncating the front is backwards."""

    def test_the_exception_message_survives_truncation(self) -> None:
        from freetcoder.generate.tools import _error_summary

        traceback = (
            "Traceback (most recent call last):\n"
            + "".join(f'  File "x.py", line {i}, in f\n    call()\n' for i in range(40))
            + "ValueError: the actual problem\n"
        )
        summary = _error_summary(traceback)
        assert "the actual problem" in summary
        assert len(summary) <= 300

    def test_an_empty_error_is_handled(self) -> None:
        from freetcoder.generate.tools import _error_summary

        assert _error_summary("   ") == "(no message)"
