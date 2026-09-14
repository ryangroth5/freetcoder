"""The probe that imports a generated question module and reports on it.

One sandboxed process does everything: import, introspect, run the solution
over the examples, generate the hidden cases, validate them, and cross-check
the brute force. Doing it in one run rather than six keeps the cost of a
rejection to a single process, which matters because most drafts are rejected.

It speaks the existing result protocol -- one JSON object per line on a stdout
handle saved before the module is imported, stamped with the marker -- for the
reason `docs/execution-protocol.md` gives: a question's own `print` must never
be mistaken for a result.
"""

from __future__ import annotations

PROBE = '''
import io, json, os, sys, random, inspect, traceback

sys.stdout = io.StringIO()          # the module's own output goes nowhere useful
_RESULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_result.json")

def emit(rec):
    """Write the result to a file, not to stdout.

    stdout is shared with whatever the module decides to print, and a module
    that writes to the real handle -- sys.__stdout__, os.write(1, ...) -- can
    interleave with the record and make valid JSON unparseable. A file has no
    such contention.
    """
    with open(_RESULT, "w") as fh:
        json.dump(rec, fh, default=str)

def fail(step, detail):
    emit({"ok": False, "step": step, "detail": str(detail)[:1500]})
    raise SystemExit(0)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import solution as mod
except Exception:
    fail("import", traceback.format_exc(limit=4))

REQUIRED = ["TITLE", "STATEMENT", "CONSTRAINTS", "solution", "brute_force",
            "is_valid", "generate_cases", "EXAMPLES"]
missing = [n for n in REQUIRED if not hasattr(mod, n)]
if missing:
    fail("interface", "the module does not define: " + ", ".join(missing))

for name in ["solution", "brute_force", "is_valid", "generate_cases"]:
    if not callable(getattr(mod, name)):
        fail("interface", name + " must be a function")

try:
    sig = inspect.signature(mod.solution)
    params = [p.name for p in sig.parameters.values()
              if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
except Exception:
    fail("interface", "solution has no inspectable signature")
if not params:
    fail("interface", "solution takes no parameters")

# A validator that ignores its arguments is not a validator. Parse the body
# rather than splitting on a colon -- the first colon is inside a type hint.
try:
    import ast as _ast
    _tree = _ast.parse(inspect.getsource(mod.is_valid).strip())
    _fn = _tree.body[0]
    _used = {n.id for n in _ast.walk(_fn) if isinstance(n, _ast.Name)}
    _used |= {n.attr for n in _ast.walk(_fn) if isinstance(n, _ast.Attribute)}
except Exception:
    _used = set(params)          # cannot tell; do not punish for that
if not _used & set(params):
    fail("is_valid", "is_valid ignores its arguments; it must actually test them")

examples = list(getattr(mod, "EXAMPLES", []) or [])
if not examples:
    fail("examples", "EXAMPLES is empty; at least one worked example is needed")

worked = []
for i, ex in enumerate(examples):
    args = ex.get("args") if isinstance(ex, dict) else None
    if not isinstance(args, dict):
        fail("examples", "EXAMPLES[%d] needs an 'args' mapping" % i)
    unknown = [k for k in args if k not in params]
    if unknown:
        fail("examples", "EXAMPLES[%d] has arguments solution does not take: %s"
             % (i, ", ".join(unknown)))
    try:
        value = mod.solution(**args)
    except Exception:
        fail("solution", "solution(**%r) raised:\\n%s"
             % (args, traceback.format_exc(limit=3)))
    try:
        json.dumps(value)
    except Exception:
        fail("solution", "solution returned something not JSON-serialisable for %r" % (args,))
    try:
        if not mod.is_valid(**args):
            fail("is_valid", "is_valid rejects your own example %r" % (args,))
    except Exception:
        fail("is_valid", "is_valid(**%r) raised:\\n%s"
             % (args, traceback.format_exc(limit=3)))
    worked.append({"args": args, "expected": value,
                   "why": ex.get("why") or ex.get("explanation")})

WANTED = int(os.environ.get("FTC_WANTED", "12"))
try:
    cases = list(mod.generate_cases(random.Random(20260913)))
except Exception:
    fail("generate_cases", "generate_cases raised:\\n%s" % traceback.format_exc(limit=3))

clean = []
for i, case in enumerate(cases):
    if not isinstance(case, dict):
        fail("generate_cases", "case %d is %s, expected a mapping of parameter to value"
             % (i, type(case).__name__))
    unknown = [k for k in case if k not in params]
    if unknown:
        fail("generate_cases", "case %d has arguments solution does not take: %s"
             % (i, ", ".join(unknown)))
    try:
        if not mod.is_valid(**case):
            fail("generate_cases",
                 "case %d is rejected by your own is_valid: %r" % (i, case))
    except Exception:
        fail("is_valid", "is_valid raised on generated case %d:\\n%s"
             % (i, traceback.format_exc(limit=3)))
    try:
        json.dumps(case)
    except Exception:
        fail("generate_cases", "case %d is not JSON-serialisable" % i)
    clean.append(case)

if len(clean) < WANTED:
    fail("generate_cases",
         "generate_cases yielded %d case(s); at least %d are needed" % (len(clean), WANTED))

# Brute force must agree on the smallest cases. Two implementations agreeing is
# the only evidence either is right.
by_size = sorted(clean, key=lambda c: len(json.dumps(c)))[:5]
for case in by_size:
    try:
        a = mod.solution(**case)
        b = mod.brute_force(**case)
    except Exception:
        fail("brute_force", "brute_force raised on %r:\\n%s"
             % (case, traceback.format_exc(limit=3)))
    if json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str):
        fail("brute_force",
             "solution and brute_force disagree on %r: %r vs %r" % (case, a, b))

emit({
    "ok": True,
    "title": str(getattr(mod, "TITLE", "") or ""),
    "statement": str(getattr(mod, "STATEMENT", "") or ""),
    "constraints": str(getattr(mod, "CONSTRAINTS", "") or ""),
    "function_name": mod.solution.__name__,
    "parameters": params,
    "examples": worked,
    "cases": clean[:40],
})
'''
