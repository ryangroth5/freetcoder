# The execution protocol

> Maintainer documentation. If you just want to run freetcoder and
> practise, read [using-freetcoder.md](using-freetcoder.md) instead.

The contract between the backend and the *harness* — the driver program wrapped
around a candidate's submission to run it against test cases.

**Read this before adding a language.** Every rule below exists because breaking
it produced a real defect, and one of them can silently misgrade a correct
answer rather than failing loudly.

`backend/tests/test_language_conformance.py` enforces this contract against
every registered adapter. A new language is not done until that suite passes for
it.

---

## Shape

One harness process handles every case for a submission. Starting a fresh
interpreter per case would dominate the runtime and make per-case timings
meaningless.

```
        JSON lines: {"args": {...}}          JSON lines: result records
backend ───────────────────────────▶ harness ──────────────────────────▶ backend
                    stdin                        the saved stdout handle

                                     harness ──────────────────────────▶ backend
                                                      stderr
                                        (tracebacks, compiler diagnostics)
```

### Input — stdin

One JSON object per line, `{"args": {<param name>: <value>}}`. Parameters are
passed **by name**, so the UI can render each argument in its own labelled box
rather than as one opaque blob. Produced by `encode_cases()`.

### Output — result records

One JSON object per line. Parsed by `decode_results()`.

| Field | Meaning |
|---|---|
| `__freetcoder` | **Required.** Marks the line as a genuine record. |
| `ok` | Did the function return without raising? |
| `value` | The return value. JSON types only. |
| `error` | Message + short traceback when `ok` is false. |
| `ms` | Wall time for this case, milliseconds. |
| `stdout` | What the submission printed **during this case**. |

A `harness_error` field replaces the rest when the harness cannot proceed at all
(for example, the required function is not defined).

---

## The rules

### 1. Results and the candidate's output must not share a stream

**Capture the real stdout handle before any user code can run, emit records only
through that saved handle, and redirect the language's normal output mechanism
into a buffer.**

This is the whole reason the document exists. Originally the harness printed
records with plain `print()` — the same stream the candidate's own `print()`
writes to, with no framing. Three failure modes, worsening:

| Candidate prints | Result before the fix |
|---|---|
| `debugging` (not JSON) | silently swallowed — their output vanished |
| `[1, 2]` (JSON array) | `AttributeError: 'list' object has no attribute 'get'` → **HTTP 500** |
| `{"ok": true}` (JSON object) | **counted as a test result** — every later case shifted by one, silently marking correct answers wrong |

The third is the dangerous one. The crash merely made the flaw visible.

The naive implementation in any language reproduces this exactly:
`console.log(JSON.stringify(rec))` in JavaScript, `fmt.Println` in Go. Do not
write the harness that way.

Per language, the mechanism is the same shape:

| Language | Save | Redirect |
|---|---|---|
| Python | `_OUT = sys.stdout` | `sys.stdout = io.StringIO()` |
| JavaScript / TypeScript | `process.stdout.write.bind(process.stdout)` | replace `process.stdout.write` and `console.log` |

### 2. Every record carries the marker; nothing else is a record

`decode_results()` accepts **only** JSON objects containing `__freetcoder`.

Rule 1 keeps the candidate off the result stream in normal use, but every
language offers a way around the redirect — `sys.__stdout__` in Python,
`fs.writeSync(1, …)` in Node. The marker means that reaching the raw handle
still cannot forge a result, which turns a silent misgrade into, at worst, an
ignored line.

Anything unparseable, non-object, or unmarked is skipped.

### 3. Candidate output is captured per case and bounded

Drain the buffer after each case into that case's `stdout` field, and cap it at
`MAX_CASE_STDOUT` (4 KB) so a printing loop cannot inflate the response.

Output produced while the module *loads* belongs to the first case, not to
nowhere.

### 4. Syntax and type errors must reach stderr

A submission that does not parse is the candidate's mistake at compile time, not
a crash. Adapters classify it as `COMPILE_ERROR`, and they do so by inspecting
**stderr** — so the harness must not swallow load-time failures.

In the Python harness, `import solution` is deliberately *not* wrapped in
`try`/`except` for exactly this reason.

### 5. One case failing must not lose the others

Records are newline-delimited and flushed per case, so a crash on case 7 still
leaves cases 1–6 readable. Catch exceptions around the function call, not around
the loop.

### 6. Return values are compared as JSON

`values_equal()` normalises both sides through JSON before comparing: tuples
become lists, integer keys become strings, and `1 == 1.0`. A value that cannot
be serialised is a **wrong answer**, not a harness failure.

---

## Adding a language

Implement `LanguageAdapter` in `backend/freetcoder/runner/adapters.py`:

```python
language: Language
source_filename: str                       # solution.py / solution.js / solution.ts
def harness(function_name) -> (filename, code)
def compile(ws, limits) -> RunResult | None   # non-None short-circuits
def command(entry) -> Sequence[str]
def classify(result) -> RunResult              # language-specific verdicts
```

Register it in `ADAPTERS`, then run
`pytest tests/test_language_conformance.py` — it is parameterized over the
registry, so the new language is exercised automatically.

### Compilation

`compile()` returning a `RunResult` short-circuits the run. TypeScript uses this
to type-check with `tsc`; Python and JavaScript return `None`.

TypeScript is deliberately **not** run with Node's `--experimental-strip-types`.
Stripping skips type checking entirely — which defeats the point of choosing
TypeScript — and rejects features requiring transformation, such as `enum`.
Because `tsc` emits `solution.js`, one JavaScript harness serves both languages.

---

## What stays Python-only, on purpose

**`hidden_generator_py`** — the program that produces hidden test *inputs* — is
always Python, whatever language the candidate writes in. It generates data, not
solutions, so there is nothing to gain from multiplying it across languages, and
one implementation means one set of behaviour to trust.

**The oracle.** Expected outputs for hidden cases are computed by executing the
*Python* reference solution, and are never taken from the model. Other languages'
reference solutions are then checked against those same expected values: a
language whose reference disagrees causes the whole question to be rejected,
because otherwise the UI would offer a language that cannot actually pass.
