"""Builds the driver program that executes a solution against test cases.

One harness process handles every case for a submission: starting a fresh
interpreter per case would dominate the runtime and make per-case timings
meaningless. Cases arrive on stdin as JSON lines and results leave on stdout as
JSON lines, so a crash on case 7 still leaves cases 1-6 readable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..models import TestCase

#: Marker on every harness record. A submission that reaches the real stdout
#: anyway (via sys.__stdout__, say) still cannot forge a result, because
#: decode_results accepts only objects carrying this key.
RECORD_MARKER = "__freetcoder"

#: Per-case cap on captured stdout, so a solution printing in a loop cannot
#: inflate the response.
MAX_CASE_STDOUT = 4096

PYTHON_HARNESS = '''\
import io, json, os, sys, time, traceback

# Results and the candidate's own print() must not share a stream. Grab the
# real stdout first, then rebind sys.stdout to a buffer so anything the
# solution prints is captured and reported rather than corrupting the protocol.
_OUT = sys.stdout
sys.stdout = _BUF = io.StringIO()

_MARKER = {marker!r}
_MAX = {max_stdout}

def _emit(rec):
    rec[_MARKER] = 1
    print(json.dumps(rec), file=_OUT, flush=True)

def _drain():
    text = _BUF.getvalue()
    _BUF.seek(0)
    _BUF.truncate(0)
    if len(text) > _MAX:
        text = text[:_MAX] + "\\n... output truncated"
    return text

# The interpreter runs with -I, which keeps the CWD off sys.path (so a
# submission cannot shadow a stdlib module). Add back exactly one directory:
# our own workspace, where solution.py lives.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Deliberately unguarded: a SyntaxError here must reach stderr so the adapter
# can classify it as a compile error.
import solution

# Anything printed at import time belongs to the first case rather than nowhere.
_pending = _drain()

_FN = getattr(solution, {function_name!r}, None)
if _FN is None:
    _emit({{"harness_error": "function {function_name!r} is not defined",
            "stdout": _pending}})
    sys.exit(1)

for _line in sys.stdin:
    _line = _line.strip()
    if not _line:
        continue
    _case = json.loads(_line)
    _t0 = time.perf_counter()
    try:
        _value = _FN(**_case["args"])
        _rec = {{"ok": True, "value": _value}}
    except Exception:
        _rec = {{"ok": False, "error": traceback.format_exc(limit=3)}}
    _rec["ms"] = round((time.perf_counter() - _t0) * 1000, 3)
    _rec["stdout"] = (_pending + _drain())[:_MAX]
    _pending = ""
    try:
        json.dumps(_rec)
    except (TypeError, ValueError):
        # A solution may return something unserialisable; that is a wrong
        # answer, not a harness failure.
        _rec = {{"ok": False, "error": "return value is not JSON-serialisable",
                 "ms": _rec["ms"], "stdout": _rec["stdout"]}}
    _emit(_rec)
'''


JS_HARNESS = '''\
// See docs/execution-protocol.md. The rules below are load-bearing.

// Rule 1: capture the real stdout writer BEFORE any user code can run, and
// redirect the normal output mechanism into a buffer. Emitting records with
// console.log would put them on the same stream as the candidate's own output,
// which silently misgrades any submission that logs a JSON object.
const _OUT = process.stdout.write.bind(process.stdout);
const _MARKER = {marker_json};
const _MAX = {max_stdout};

let _buf = '';
const _capture = (chunk) => {{ _buf += chunk; return true; }};
process.stdout.write = _capture;
console.log = (...args) => _capture(
  args.map((a) => (typeof a === 'string' ? a : _inspect(a))).join(' ') + '\\n');
console.info = console.log;
console.debug = console.log;

function _inspect(value) {{
  try {{
    return require('util').inspect(value, {{ depth: 4, breakLength: 120 }});
  }} catch (e) {{
    return String(value);
  }}
}}

function _emit(rec) {{
  rec[_MARKER] = 1;
  _OUT(JSON.stringify(rec) + '\\n');
}}

function _drain() {{
  let text = _buf;
  _buf = '';
  if (text.length > _MAX) text = text.slice(0, _MAX) + '\\n... output truncated';
  return text;
}}

// Rule 4: a SyntaxError here must reach stderr so the adapter can classify it
// as a compile error, so this require() is deliberately not guarded.
const _solution = require('./solution.js');

// Rule 3: output produced while the module loads belongs to the first case.
let _pending = _drain();

const _FN = (typeof _solution === 'function')
  ? _solution
  : (_solution && (_solution[{function_name_json}] || _solution.default));

if (typeof _FN !== 'function') {{
  _emit({{ harness_error: 'function ' + {function_name_json} + ' is not defined or not exported',
          stdout: _pending }});
  process.exit(1);
}}

// Argument order is recovered from the function signature, because cases arrive
// keyed by parameter name.
function _paramNames(fn) {{
  const src = fn.toString();
  const open = src.indexOf('(');
  let depth = 0, close = -1;
  for (let i = open; i < src.length; i++) {{
    if (src[i] === '(') depth++;
    else if (src[i] === ')') {{ depth--; if (depth === 0) {{ close = i; break; }} }}
  }}
  const inner = src.slice(open + 1, close);
  if (!inner.trim()) return [];
  return inner.split(',').map((p) => p.split('=')[0].trim().replace(/^\\.\\.\\./, ''))
              .filter(Boolean);
}}
const _PARAMS = _paramNames(_FN);

let _input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => {{ _input += d; }});
process.stdin.on('end', () => {{
  for (const line of _input.split('\\n')) {{
    const trimmed = line.trim();
    if (!trimmed) continue;
    const args = JSON.parse(trimmed).args || {{}};
    // Fall back to declared key order if the signature could not be parsed.
    const ordered = (_PARAMS.length ? _PARAMS : Object.keys(args)).map((n) => args[n]);

    const t0 = process.hrtime.bigint();
    let rec;
    try {{
      const value = _FN(...ordered);
      rec = {{ ok: true, value: value === undefined ? null : value }};
    }} catch (err) {{
      rec = {{ ok: false, error: (err && err.stack ? err.stack : String(err))
                              .split('\\n').slice(0, 3).join('\\n') }};
    }}
    rec.ms = Number(process.hrtime.bigint() - t0) / 1e6;
    rec.stdout = (_pending + _drain()).slice(0, _MAX);
    _pending = '';
    try {{
      JSON.stringify(rec);
    }} catch (e) {{
      // Rule 6: an unserialisable return is a wrong answer, not a crash.
      rec = {{ ok: false, error: 'return value is not JSON-serialisable',
              ms: rec.ms, stdout: rec.stdout }};
    }}
    _emit(rec);
  }}
}});
'''

@dataclass(frozen=True, slots=True)
class CaseResult:
    """Outcome of one case inside the harness."""

    ok: bool
    value: object = None
    error: str = ""
    ms: float = 0.0
    #: Whatever the submission printed while this case ran.
    stdout: str = ""


def build_python_harness(function_name: str) -> str:
    return PYTHON_HARNESS.format(
        function_name=function_name,
        marker=RECORD_MARKER,
        max_stdout=MAX_CASE_STDOUT,
    )


def build_js_harness(function_name: str) -> str:
    """Driver for JavaScript, and for TypeScript once tsc has emitted JS.

    Values are interpolated with json.dumps, not !r: Python's repr produces
    single-quoted literals that break inside JavaScript string context.
    """
    return JS_HARNESS.format(
        function_name_json=json.dumps(function_name),
        marker_json=json.dumps(RECORD_MARKER),
        max_stdout=MAX_CASE_STDOUT,
    )


def encode_cases(cases: list[TestCase]) -> str:
    """Serialise cases to the newline-delimited form the harness reads."""
    return "\n".join(json.dumps({"args": c.args}) for c in cases) + "\n"


def decode_results(stdout: str) -> list[CaseResult]:
    """Parse harness output, tolerating a truncated final line.

    Only objects carrying RECORD_MARKER are treated as results. A submission
    that reaches the real stdout could otherwise inject a line that parses as a
    result and shift every later case by one, silently misgrading correct
    answers -- a far worse outcome than the crash that first exposed this.
    """
    results: list[CaseResult] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # truncated by the output cap
        # A bare list or scalar is not a record; `rec.get` on one used to raise.
        if not isinstance(rec, dict) or RECORD_MARKER not in rec:
            continue
        if "harness_error" in rec:
            results.append(
                CaseResult(
                    ok=False,
                    error=str(rec["harness_error"]),
                    stdout=str(rec.get("stdout", "")),
                )
            )
            continue
        results.append(
            CaseResult(
                ok=bool(rec.get("ok")),
                value=rec.get("value"),
                error=str(rec.get("error", "")),
                ms=float(rec.get("ms", 0.0)),
                stdout=str(rec.get("stdout", "")),
            )
        )
    return results


def values_equal(a: object, b: object) -> bool:
    """Compare an expected value with a produced one.

    JSON round-tripping turns tuples into lists and int keys into strings, so a
    naive == would reject correct answers. Normalise both sides through JSON
    before comparing, and treat 1 == 1.0 as equal.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-9
    try:
        return bool(json.loads(json.dumps(a)) == json.loads(json.dumps(b)))
    except (TypeError, ValueError):
        return a == b
