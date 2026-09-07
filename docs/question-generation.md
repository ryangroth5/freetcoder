# How a question is generated

Three escalating mechanisms, each engaged only when the one before it fails:

```
generate ──▶ gate ──✗──▶ repair the broken artifact ──▶ gate ──✗──▶ regenerate
                │                    │
                │                    └── with tools, when the provider has them
                └──✓── serve it
```

The gate decides when the loop is finished, not the model. That is why this is a
few hundred lines rather than an agent framework: **the objective is
machine-checkable.**

## Rule zero: the gate is not repairable

`validate_question` is the trust anchor for the entire product. A loop that could
weaken the validator could make anything pass, and the guarantee that questions
are provably solvable would be worthless.

The question's *own* material is fair game — statement, reference solutions,
hidden-case generator, brute force, expected values, constraints. Validation
logic is not. `backend/tests/test_repair.py::TestTheGateIsNotRepairable` asserts
it: a confidently wrong patch still gets rejected.

## Why the loop was blind, and what fixed it

A real session failed four times identically:

```
attempt 1 rejected (reference_failed): javascript reference did not run (runtime_error):
attempt 2 rejected (reference_failed): javascript reference did not run (runtime_error):
```

Nothing after the colon. The harness reports its own faults — "function is not
defined or not exported" — as a record on **stdout**, while the gate reported
only `stderr`, which was empty. The model was asked to fix a problem it was never
told about, so it reproduced it every time.

**More iterations cannot help when the signal is missing.** An agentic harness
fed the same empty feedback would have failed just as reliably. `_failure_detail`
now prefers the harness diagnostic, then the failing case's error, then stderr,
and never returns an empty string. A test asserts every rejection path explains
itself.

The underlying cause was equally mundane: the system prompt described "a
self-contained *Python* module", written before JS and TS existed, so the model
never knew JavaScript needs `module.exports`.

## Repair targets

Regenerating discards everything that was right — often a good statement and a
sound reference — to fix one broken artifact. Each rejection instead routes to
the thing that is actually wrong:

| Rejection | Patched artifact |
|---|---|
| `REFERENCE_FAILED` | that language's reference solution |
| `GENERATOR_FAILED`, `NO_HIDDEN_CASES` | the hidden-case generator |
| `PERF_NOT_DISCRIMINATING` | the generator (cases must get bigger) |
| `BRUTE_FORCE_DISAGREES`, `MISSING_BRUTE_FORCE` | the brute force |
| `CONSTRAINT_VIOLATION` | the bounds or the generator |
| `UNSAFE_MAGNITUDE` | the generator |
| `VISIBLE_MISMATCH` | **the model chooses** |

`VISIBLE_MISMATCH` is the one genuine ambiguity: either the stated answers are
wrong or the reference is. The repair prompt shows both and lets the model
decide which to patch.

A patch is far smaller than a question, so it is cheaper, faster and likelier to
come back valid. `GenerationAttempt.repaired` marks repair rounds so the
acceptance-rate report does not conflate them with regenerations.

## Tools

When the provider supports tool calls, the model can execute code before
committing to a patch:

| Tool | What it does |
|---|---|
| `run_code(language, source, stdin)` | Runs a program, returns stdout, stderr and verdict |
| `run_against_cases(language, source, function_name, cases)` | Runs a solution over inputs, showing each return value, timing and anything printed |

**Both execute through `runner.run_source` — the same sandboxed path a
candidate's submission takes.** There is deliberately no second way to run code:
model-written code is exactly as untrusted as candidate-written code, and it runs
under the same unprivileged uid, rlimits and seccomp filter.
`test_tools.py::TestToolsRunInTheSandbox` proves it by asserting a tool call
cannot reach the network, cannot loop forever and cannot write outside its
workspace.

Capability is probed once and cached on the client. A provider that refuses tool
calls is marked incapable for the process and everything falls back to the
feedback loop, so local models keep working — a promise the README already makes.

Budgets live in settings: `repair_rounds` (3) and `tool_call_budget` (6), plus
the runner's own wall-clock limits. A confused model cannot spend unbounded
tokens or time.

## Why no agent framework

`pi-coding-agent` and similar were considered and rejected for reasons specific
to this project:

- **We already own the execution sandbox, and it is the security-critical part.**
  Any agent has to run its code through it anyway — that integration *is* the
  work.
- **A concrete blocker:** pi-mono's sandbox uses bubblewrap, which needs user
  namespaces. This container has no `CAP_SYS_ADMIN` (`unshare -n` fails; see
  `container-discovery.md`), the same constraint that ruled out Judge0 and
  Piston. Nesting it would mean granting privileges we deliberately withheld.
- **Shape mismatch.** A coding agent edits a workspace and emits diffs. We need
  one validated JSON artifact.
- **The objective is machine-checkable**, so the agent never decides whether it
  is done. That removes most of what a framework provides.

## Measuring it

The acceptance rate is the number that matters:

```bash
docker compose run --rm dev python -m freetcoder.generate \
    --style coderbyte --preset practical -n 10
```

It reports every attempt by outcome, so a systematic failure — one language's
convention, a generator pattern the model keeps getting wrong — shows up as a
cluster rather than a vague sense that generation is flaky.

---

## Importing a question from text

Two ways to bring your own problem, and they are genuinely different.

**Describe** — "make a question about counting dogs" — is a topic steer. It goes
to `generation.freeform`, which has always worked; it was simply buried in the
picker's third tier.

**Paste** — a question seen elsewhere, half-remembered, or sketched — goes to
`generation.import_text` and sets `source = "imported"`. The distinction
matters: the freeform prompt explicitly tells the model the text "refines the
topic, it does not change the format", so a pasted statement would be treated as
a hint and the model would write its own question instead.

### The gate does not change

An imported question passes exactly the same validation as a generated one: the
reference runs, the examples match it, hidden cases generate, the brute force
discriminates, every scaffold parses in its own language. So "I pasted something
approximate" still yields a *provably solvable* question, or a clear reason why
not.

This is enforced, not merely intended:
`test_import.py::test_no_gate_check_branches_on_provenance` reads the gate's own
source and asserts it never mentions `import_text` or `source`. If validation
could tell an import apart, an import could be held to a lower standard without
anyone noticing.

### Prompt layering

`prompts/import.md` is appended *after* the style template, never instead of it,
so an imported question is still a LeetCode or a Codility question. The source
text supplies the problem; the style still supplies the format.

The text is treated as a **starting point**. Supplied prose is almost always
under-specified — the "Most Frequent Word" example elsewhere in these docs is
typical — so the model is required to decide what the prose leaves open
(tokenisation, tie-breaks, degenerate input, bounds) and to record every such
judgement in `import_notes`.

Those notes render above the statement as "Adapted from your text". Being told
"ties break toward the earliest word" up front is the difference between an
informed answer and a baffling failure.

### Provenance

The library is meant to be shared. A shared library quietly full of questions
copied from LeetCode or Codility is a liability far easier to avoid now than to
retract later.

- `GatedQuestion` carries `source` and `import_text` into storage and across the
  library boundary.
- `library.publish` refuses an imported question unless
  `allow_import_publish=True`; the API returns **409** for that (a decision you
  can override) as opposed to **503** for an unreachable library (which you
  cannot).
- The Save button asks for confirmation on an imported question rather than
  silently refusing or silently publishing.

Recording provenance costs nothing now and cannot be reconstructed later.

### Measuring it

Import is a model-quality feature, so fixtures prove the plumbing and not the
result:

```bash
docker compose run --rm dev python -m freetcoder.generate \
    --style leetcode --import-text "count the dogs in a kennel log" -n 5
docker compose run --rm dev python -m freetcoder.generate \
    --style codility --import-file /srv/app/pasted.md -n 5
```
