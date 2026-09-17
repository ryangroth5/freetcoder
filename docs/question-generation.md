# How a question is generated

> Maintainer documentation. If you just want to run freetcoder and
> practise, read [using-freetcoder.md](using-freetcoder.md) instead.

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

---

## The progress channel

Generating a question takes tens of seconds, and with repair rounds, minutes.
Before this, the UI said `Generating and validating…` and nothing else — no step,
no elapsed time, and on failure a single sentence of apology.

`GET /api/progress/{id}` now returns a log of what is actually happening:

```
·  asking the model for a question                          0.0s
✓  validating "Counting Dogs"                               6.2s
·  checking the starter code parses in python, javascript    6.2s
·  running the reference against the examples                6.9s
·  generating hidden test cases                              7.4s
·  computing expected answers for 12 hidden cases            7.9s
!  rejected: the javascript reference did not run: …         9.1s
·  asking the model to fix the javascript solution           9.1s
✓  repaired, and it now passes                              14.6s
```

### An observer, not a stage

Generation is unchanged. `POST /api/sessions` is still synchronous, so there is
no second code path to drift, and the reporter threaded through
`obtain_question → generate_question → validate_question → repair_question`
defaults to a no-op.

That default is the design: **progress must never be able to affect whether a
question is produced.** `test_gate.py` and `test_repair.py` needed no changes at
all when this was added, which is the assertion that it worked.

The client mints a `progress_id`, sends it with the request, and polls while the
request is in flight.

### Why polling

The payload is tiny, it survives a reload, and there are no streaming edge cases
to get wrong. A second of latency is irrelevant against a forty-second
operation, and this project has lost enough time to transport subtleties
(WebSocket framing, worker formats, service initialisation order) to prefer the
boring option.

The panel reads once immediately on mount rather than waiting a full interval:
a fast generation would otherwise finish before anything was ever shown.

### Why no percentage

Attempts and repair rounds are unbounded, so any percentage would be invented,
and a bar that stalls at 80% is worse than no bar. Elapsed time plus a truthful
list of completed steps says more and claims less.

### Diagnosis

The log stays on screen after a failure, with the attempt history intact. The bug
that blocked a real session — four attempts rejected identically because the
gate's complaint was empty — would have been obvious here and was invisible
without it.

### Cancellation

`POST /api/progress/{id}/cancel` sets a flag that the loop checks between
attempts, repair rounds and gate steps. It cannot interrupt a request already in
flight to the model, so the button says *"Waiting for the model to finish
responding"* rather than appearing to hang.

One subtlety: a cancel can arrive *before* the request it refers to, because the
client mints the id then hits Start and Cancel in quick succession. `start()`
preserves an existing cancelled flag rather than clobbering it, or that cancel
would be silently ignored.

### Bounded by construction

Runs live in memory — progress is worthless once the request it describes has
returned. The registry caps the number of runs, evicts finished ones on a TTL,
and caps steps per run, because an in-memory store that grows forever is a slow
leak in a container meant to run for days.

---

## The tutor

A chat panel beside the problem that can see what the candidate sees, and no
more.

### What it knows

- the statement, its structured constraints, and the **verified clarifications**;
- the candidate's current code and language;
- their last run in full — verdict, per-case results, the first failing case,
  and compiler or syntax errors verbatim;
- the history of earlier attempts, so it can notice a case that started passing
  or one that broke while fixing another;
- the visible examples;
- a **characterisation** of the hidden cases: how many, the range of sizes, and
  which shapes are present.

### What it never knows

**The reference solution.** A tutor that can read the answer is a back channel
to the oracle, which is the boundary the Solution tab already enforces.

**The hidden cases verbatim.** This one is subtler and worth stating. Hidden
*inputs* leak little on their own, since the expected outputs are withheld. But
a "compute expected" affordance would let someone enumerate the inputs through
chat, compute each answer, and pass with a lookup table. Neither feature is a
problem alone; together they are a bypass. The characterisation preserves what
was actually wanted — reasoning about what valid input looks like — without
handing over a test vector list.

Both are asserted directly: `test_tutor.py` puts a distinctive marker in the
reference and a distinctive string in a hidden input, and fails if either
reaches the assembled context.

The tutor has its own context builder rather than reusing the repair prompt's,
which *does* see reference solutions. Sharing one would put a leak a single edit
away.

### Behavioural access, not source access

`probe_reference(args)` runs the intended solution on arguments the candidate is
asking about and returns **only the output**. "What does it do on an empty
string?" becomes answerable with certainty while the algorithm stays invisible.

This grants no capability the candidate lacks — they can already run their own
code against any input — it is a more convenient route to the same information.

### It follows the format

`generation.give_hints` already encodes how much support each platform gives.
LeetCode and Coderbyte offer hints; a CodeSignal GCA and Codility offer none. The
tutor follows: locked during those simulations with the reason shown, unlocked on
submit or skip. A timed assessment that ships an AI assistant is not simulating
anything.

### Streaming, and its fallbacks

Replies stream over SSE, because a silent ten-second pause reads as broken. Two
fallbacks, since new transport is where this project has lost the most time:

- a provider that cannot stream gets one whole reply instead;
- a stream that dies mid-reply surfaces what arrived plus the reason, rather than
  a blank bubble.

Tool calls are shown as they happen — watching it check rather than guess is the
most reassuring part of the exchange.

Budgets: `tutor_tool_budget` (probes per reply) and `tutor_message_cap`
(questions per session).

---

## Computing an expected value

`POST …/questions/{i}/compute` runs the intended solution on arguments you supply
and returns **only the value**. It goes through the same `run_reference` the
tutor's probe uses, so the boundary — output, never source — is enforced in one
place.

**On demand, not automatic.** Filling every case in as you type would turn any
question into "type an input, read the answer". A button keeps probing one
awkward edge case a single click away without making that the default. Computed
values are labelled `expected (computed)` so you can tell them from your own.

Arguments the question's own constraints rule out are refused: computing an
answer for input the statement says cannot occur teaches the wrong thing.

## Is the reference actually good?

"Your code vs the reference" only means something if the reference is good. A
secretly quadratic baseline claiming `O(n)` would flatter every submission, and
the resulting percentile would be worse than no percentile.

So a stated `complexity_target` is **measured, not trusted**. The reference is
timed on the smallest and largest hidden inputs and the observed exponent

```
k = log(t_large / t_small) / log(n_large / n_small)
```

is compared with the claim, rejecting as `REFERENCE_TOO_SLOW` when it exceeds it
by more than `EXPONENT_TOLERANCE`.

### Two things this got wrong first

**Comparing groups instead of sizes.** The first version compared the summed
time of the smaller half of the cases against the larger half. Over
exponentially growing inputs that shows roughly a 4x jump for a *linear*
reference, which says nothing about complexity — it would have rejected good
questions. Fitting an exponent against the actual input sizes is the fix.

**Blaming the wrong artifact.** The brute-force check ran first, so a quadratic
reference surfaced as `PERF_NOT_DISCRIMINATING` — "the tests do not separate a
good solution from a naive one" — when the real problem was the baseline. The
scaling check now runs first and names the reference.

### Deliberate restraint

- A question whose largest case runs in under `MEASURABLE_MS` is **skipped**,
  not judged: silence beats a verdict invented from scheduling noise.
- Timings are best-of-three, since this runs on a laptop sharing cores.
- The tolerance is wide on purpose. This separates an *order of complexity*, not
  constant factors.
- The check tests honesty, not speed: the same quadratic reference is accepted
  when it honestly claims `O(n^2)`.

The measured growth is stored and shown beside the ratio, so the results pane
says `1.3× the reference (~linear)` rather than a bare multiplier.

---

## Is the statement enough?

Every other check validates the question **against itself**: the reference
reproduces the examples, the generator obeys the constraints, the clarifications
match the code, the scaffolds parse. None of them read the statement.

So a question can pass everything and still be underivable from its own prose.
That is not hypothetical — it is what happened with *Most Frequent Word*, which
asked for "the word that appears most often" while its examples silently
established that punctuation splits words and case is folded. Internally
flawless; unfair to solve. `test_sufficiency.py` asserts the gate accepts that
version, which is exactly the point.

A second model therefore solves each question from the **statement, constraints,
clarifications and worked examples alone** — no reference, no hidden cases — and
its solution is run against the oracle. Disagreement means the prose is missing
something, reported as `STATEMENT_INSUFFICIENT` and repairable through the
`statement` target, which rewrites the prose, the clarifications, or both.

### Why it lives outside the gate

`gate.py` is synchronous, deterministic and LLM-free, which is what makes it the
trust anchor and testable offline. This check needs a model, so it runs as a
separate stage after the gate rather than inside it. The gate stays something you
can reason about without a network.

### It reports suspicion, not proof

A second model failing does not *prove* ambiguity — it may simply be weaker.
Three deliberate limits keep it from blaming the statement unfairly:

- **Cases outside the stated constraints are excluded.** A failure on input the
  question says cannot occur is the generator's fault, not the prose's.
- **A provider failure is inconclusive**, not failing. Rejecting a good question
  because an API call errored would be worse than not checking.
- **A solution that will not even run is inconclusive.** That says more about
  the attempt than about the statement.

The verdict is worded "the statement may be under-specified" and carries the
disagreeing case *and* what the attempt assumed — so a repair has something
concrete to act on, and a human reading the log can judge for themselves.

### Cost

It roughly doubles generation time and tokens: a second solve, plus running it.
`FREETCODER_CHECK_STATEMENT_SUFFICIENCY=0` turns it off. It is on by default
because the alternative is questions you cannot fairly answer, and the
acceptance-rate report lists its rejections separately so its value stays
measurable rather than assumed.

---

## Experiment log: monolithic versus staged generation

Real measurements, 2026-09-11, against OpenRouter. Recorded because they cost
money to obtain and because two of them contradict what we assumed.

**Monolithic (the shipped path), glm-4.6 — 0 of 4 accepted.** 50-292s each, and
**13 schema-validation retries**. Failures: `schema_invalid` x3,
`prose_too_thin` x3, `perf_not_discriminating` x1.

**Staged (`generate/staged.py`), glm-4.6 — 1 of 3 accepted.** 567-885s each,
**zero** schema-validation retries, ~47k completion tokens for the accepted one.

Single call, same prompt, three models:

| model | time | output | statement | title |
|---|---|---|---|---|
| deepseek-v4.1-flash | 334s | 7642 ch | 1185 ch | *Longest Subarray Summing to K* |
| kimi-k2.5 | 511s | 4571 ch | 136 ch | *count-subarrays-divisible-by-k* |
| glm-4.6 | 52s | 5611 ch | 155 ch | *Longest Subarray with Sum K* |

### What this overturned

- **Gate latency is irrelevant.** 858.3 of 858.7 seconds was provider time; the
  14-18 sandboxed processes are 0.05% of a question. Optimising the gate would
  have been wasted effort, and we were about to.
- **Output volume is not the driver.** Staged generated *one* language instead
  of three and was slower, with roughly ten times the completion tokens.
- **The 14-field schema is a failure amplifier.** Thirteen validation retries
  against zero for four narrow schemas is the clearest signal in the set.
- **Recall is ours, not the model's.** Four topic-only prompts across three
  models returned the same memorised problem, and kimi emitted a URL slug as a
  title. A single local scenario seed -- no model call -- produced the only
  original question of the session.
- **The two strategies fail differently.** Monolithic fails on prose and
  schema; staged fails on code. That suggests the seam is prose-versus-code
  rather than one-call-versus-many.

### Caveat on the staged numbers

`staged.py` did not validate anything in-stage when these were taken, despite
its docstring saying otherwise. Both staged failures were caught by the gate
after all four stages ran, so the strategy was measured without the localised
failure that is its whole point. Treat 1-of-3 as a floor, not a verdict.

---

## Experiment log, part two: it was the wire format

Four days of measurement against OpenRouter (deepseek-chat, deepseek-v4.1-flash,
glm-4.6, kimi-k2.5). Recorded because several results overturned assumptions we
were about to act on.

### What was actually wrong

Every failure we chased turned out to be **serialisation**, not reasoning:

| Observed | What it was |
|---|---|
| Whole Python functions on one line, newlines replaced by double spaces | Code inside a JSON string; 3 of 4 models did this |
| `constraints[].name` missing on every entry | A nested list of objects is the shape models drop fields from |
| `{"args": [[42], 0]}` counted as zero cases | The generator was fine; **our** decoder demanded a mapping |
| `signatures` absent, fields flat at top level, `difficulty: "Medium"` | `strict: False` makes the schema a hint, and models write the obvious shape |

None of those is a model failing to author a coding question.

### Measured, on deepseek-chat

| strategy | accepted | median time | dominant failure |
|---|---|---|---|
| monolithic (one JSON call) | 0 / 3 | 400s | `prose_too_thin` |
| flat (one JSON call, no nested lists) | 0 / 3 | 24s | flattened code |
| delimited (`=== MARKERS ===`, no JSON for code) | **1 / 5** | ~110s | generator case count |

The accepted question scored 1.0 on every prose metric and was flagged neither
thin nor recalled. The same model could not previously produce a *parseable*
solution through the JSON path.

### Four assumptions that were wrong

- **Gate latency mattered.** It does not: 858.3 of 858.7 seconds was provider
  time. The 14-18 sandboxed processes are 0.05% of a question. We were about to
  optimise them.
- **Output volume drove latency.** It does not: the staged run produced *one*
  language instead of three, was slower, and used roughly ten times the tokens.
- **The schema was merely verbose.** It was a failure amplifier: 13 validation
  retries against zero for four narrow schemas.
- **Recall was a model quirk.** It is prompt-induced. Four topic-only prompts
  across three models returned the same memorised problem, one emitting a URL
  slug as its title. A single local scenario seed -- no model call -- produced
  the only original question of the session.

### Process notes worth keeping

- **A retry must not relax the ask.** The client dropped to unconstrained JSON
  after the first failure, which turned a transient miss into a structural one.
- **Validate inside the stage.** `prose_too_thin` was the most common rejection,
  and by the time the gate says so the reference, generator and brute force have
  all been written for a question that was never going to be served.
- **Look at the payload.** Four hypotheses died to one capture of what a
  rejected generation actually contained. Reading source produced the wrong
  answer four times; reading one response produced the right one.

---

## Questions as modules

The conclusion of the experiments above was that we were fighting the wire
format, not the model. So the model is now handed
`generate/interface/question_interface.py` -- a real file, not prose about one
-- and writes a module implementing it.

Measured on glm-4.6, seeded, three tries:

```
27.6s   tok= 2280   Balanced Trees
37.8s   tok= 3050   Tidal Stability Window
204.6s  tok= 7711   Busiest Departure Window
42.4s   tok= 2344   Longest Orchard Segment
60.8s   tok= 2355   Longest Baking Shift
584.5s  tok=23849   Rotated Bookshelf Search
```

Against 0 of 3 for the JSON monolith on the same gate, and 1 of 5 for
delimited text. None was flagged thin or recalled, and the scenario seed is
visible in every title.

### Why it works

Validation stopped being parsing and became running:

1. `ruff check --isolated --select E9,F` -- real errors only. Style is not a
   reason to reject a question, and `--isolated` keeps a throwaway workspace
   from inheriting the project's rules.
2. `pyright --outputjson` -- it is Node, so it needs `limit_address_space=False`
   like every other V8 process here.
3. Import it in the sandbox and check the interface is present.
4. Run it: `solution` over `EXAMPLES`, `generate_cases`, `is_valid` over every
   case, `brute_force` for agreement.

Each step hands back its own output as the retry message, which is the loop
these models are already tuned for.

### What the shape removes

- **Expected values are never stated.** They come from calling `solution`, so
  "you claimed [0, 3] but your reference returns [1, 3]" cannot happen.
- **The scaffold, function name and parameter names come from
  `inspect.signature`.** They cannot disagree with the solution they describe.
- **`is_valid` replaces the constraints list of objects** -- the shape models
  kept mangling -- with a predicate we call. A body ignoring its arguments is
  refused, since `return True` would pass everything vacuously. The check parses
  the body: splitting on the first colon lands inside a type hint.
- **Module-level names, not a class**, so the same shape translates to exported
  functions in TypeScript and package functions in Go.

### Two bugs it surfaced in us

- `PROBE_LIMITS` inherited the sandbox's 64KB output cap, so a question with
  forty sizeable cases was truncated mid-JSON and reported as "the module
  produced no result" -- true about what arrived, wrong about what was
  produced. The cap protects against untrusted candidate output; this is our
  own probe.
- Rate limiting (HTTP 429 from an upstream shared pool) is now legible rather
  than arriving as a generic failure, because the client reports the provider's
  own error text.

### What the module strategy still had to learn

Measuring it was not the same as shipping it. Four things `GeneratedQuestion`
supports and the module path never set would have been dropped silently by the
switch:

| name | who consumes it |
|---|---|
| `hint_md` | `give_hints`, shown to the candidate |
| `complexity_target` | the gate measures the reference against it when `perf_tests` is on |
| `clarifications` | the gate executes each `probe` against the reference |
| a second language | `_check_other_languages`, and `execute_against` |

The interface declares `HINT`, `COMPLEXITY` and `CLARIFICATIONS`
unconditionally — the contract a model reads should not change shape between
formats — and the prompt says which are wanted. An unasked-for name stays `""`
and is dropped on the way out, rather than trusting the model's restraint.

Clarifications carry a `probe` but no `expect`, for the reason hidden cases
carry inputs only: asking a model what its own code returns is asking it to
guess. The probe calls `solution`.

The last gap was the interesting one. The strategy built exactly one signature
and stamped it with whatever language it was handed, so a format offering
Python *and* JavaScript — which `leetcode` does — cached a Python-only
question. The gate rejects that, and `execute_against` answers
`INTERNAL_ERROR` for the other language. Its own tests had been resolving
`leetcode` without saying what they wanted, so they had been building
single-language questions against a two-language format and not noticing.

Only `solution` and the scaffold cross the language boundary.
`generate_cases`, `is_valid` and `brute_force` stay Python, because Python is
the oracle: another language's job is to reproduce its answers, not to hold
opinions. That is the split `_check_other_languages` already assumed.

### Is TypeScript worth measuring separately?

No — not as a bench dimension. Measuring a strategy means scoring first-pass
acceptance of a whole question. Translation is not a strategy: it moves one
function and a scaffold, and the gate validates it by *executing* it against
the Python oracle's answers on eight sampled cases. There is no statement, no
constraints and no case generator to get wrong, so it is pass/fail
conformance, not quality.

What it gets instead is a counter: `translations_attempted` and
`translations_ok` on the quality report. If the rate is high, TypeScript is a
non-issue. If it is low it earns a dimension then, from data rather than up
front.

## Choosing a strategy

`generation_strategy` (settings, `FREETCODER_GENERATION_STRATEGY`) is `module`
by default and `monolithic` as the escape hatch. Monolithic is also forced,
whatever the setting says, when a format supplies `import_text`: adapting
prose someone else wrote is the one thing the module interface has no
equivalent for.

The strategy is part of the cache key. The two do not produce interchangeable
questions, and a fallback cache that might hand back either would make a
change in generation quality unobservable. Keys from before that segment
existed can never match again, so they are deleted on migration — except any
question a session is still using.

## Three bugs the cross-model bench found

The first cross-model run of the module strategy scored deepseek-chat at
**0 of 3**, which looked like a model-agnosticism problem. It was not. All
three failures were ours.

**A fence in the wrong language.** `extract_code` matched only ` ```python `,
so a translation that came back fenced ` ```javascript ` kept its backticks,
went to Node as source, and returned a `SyntaxError` attributed to the model's
translation. That function exists *because* models fence code when asked for a
bare file; it had simply never been applied to the translation path. The
prompt asks for bare code in each section, and asking is not getting.

**A generator that could not survive its own cases.** `_replay_generator`
embedded the hidden cases as a Python *literal*, so the interpreter parsed
27MB of source into millions of boxed integers before printing them straight
back out as JSON. Forty cases of a hundred thousand elements died with
`MemoryError` inside the generator's limits and surfaced as
`generator_failed` — a question discarded for being exactly as large as a
perf-discriminating format asked it to be. The cases are already JSON; they
never needed to become objects. Two of the three deepseek failures were this.

**Silent truncation, which was worse.** Fixing the memory problem exposed the
output cap underneath it: forty cases of a thousand elements is 200KB against
a 64KB default, and the run still reported `ok`. A question would have been
served and graded on fourteen of the forty hidden tests it promised, with
nothing anywhere saying so. Truncation that fails loudly is a bug; truncation
that succeeds quietly is a wrong grade. Cases are now trimmed to a byte
budget, but never below the number the question promised.

The bench also printed `generator_failed` and nothing else, which is why two
of these took a reproduction script to find rather than a glance at a log. It
now carries the gate's own detail — the same blindness the stage errors were
added to fix, left in place one layer up.

### What that says about the problem space

The pattern across all four days is unchanged and now has a third instance:
**the model is rarely the thing that is broken.** Serialisation, a fence, an
output cap, a literal that should have been a string — every one of these
presented as "the model produced something unusable" and every one was ours.
A bench that reports only an outcome name will keep attributing our bugs to
the model, which is an expensive way to be wrong.

## Which models we measure against

`deepseek/deepseek-chat`, `z-ai/glm-4.6`, `moonshotai/kimi-k2.5` and
`deepseek/deepseek-v4.1-flash` have all been used. Two of those are no longer
worth a slot:

**`deepseek-chat` is dropped.** It is not a coding model, and the runs say so:
in the last clean measurement its only non-rate-limited attempt produced a
`solution` and a `brute_force` that disagreed on a one-element input, which is
the gate doing its job on a model that cannot hold two implementations of the
same function in agreement. It is also the model most often rate-limited on
OpenRouter's shared pool — two of three attempts returned HTTP 429 — so a run
against it measures the pool rather than the strategy. Use
`deepseek/deepseek-v4.1-flash` when a DeepSeek data point is wanted.

The three worth keeping are **glm-4.6**, **kimi-k2.5** and
**deepseek-v4.1-flash**. They differ enough to be a real agnosticism test:
glm and kimi are both slow and verbose, flash is the one that emitted real
newlines inside JSON when the others did not.

**Rate limiting is a measurement hazard, not a result.** A 429 arrives as
"the model did not answer", which scores identically to a model that answered
badly. When a run shows an unexpected collapse, read the log before believing
the table.

## Retries are nested, and they multiply

Four loops re-ask the model, and none of them can see the others:

| where | budget | reports to the log? |
|---|---|---|
| `client.py` `complete_text` / `complete_json` | `llm_max_retries` (3) | yes, since it gained a callback |
| `module.py` `generate_module` | `tries_per_stage` (3) | yes |
| `module.py` `generate_question_as_module` | `repair_rounds` (3) | yes |
| the same, regenerating from scratch | `generation_attempts` (4) | yes |

They compose by multiplication, and nobody chose the product. It surfaced as a
progress step that sat at **677 seconds**: a 300s deadline fired three times
inside `complete_text`, which logged to the container and reported nothing, so
the step looked frozen and only the final failure reached the screen. A working
call in the same run took 103s.

Two things fixed it. The client no longer re-sends a **timeout** — a 429 or a
5xx says the provider was momentarily unable and is worth another go, while a
timeout says this request is too slow for this budget and the identical request
will be too. And the client can now narrate a retry through an `on_retry`
callback, joined to the progress reporter in `generate_module`, so a re-send is
a line in the log rather than a silent minute.

The lesson generalises past this bug: **a retry that reports nothing is
indistinguishable from a hang**, and retry budgets at different layers need to
be read as a product rather than one at a time.

## Seeing a stall while it happens

A run sat at `writing question 1 — first try` for 300 seconds and then reported
*"the model did not answer: no text response"*. Nothing could have said so
sooner, because generation calls were **not streamed**: no bytes exist until a
non-streamed reply is complete, so a model that is generating and one that is
stuck look identical for the whole deadline. Only the tutor streamed.

Generation calls now stream, with three clocks instead of one:

| clock | default | trips when |
|---|---|---|
| `llm_first_token_s` | 30s | no content or reasoning token has arrived |
| `llm_idle_s` | 60s | tokens were flowing and then stopped |
| `llm_timeout_s` | 600s | the whole call, however lively |

Reasoning tokens count as alive — thinking models emit them long before any
content. Gateway keep-alive comments do not: they prove the connection, not
generation. A stall raises `LLMStalled`, which like any timeout is not re-sent
identically. An endpoint that refuses `stream=True`, or ignores it and answers
whole, still works.

`llm_fallback_model` names a second model on the same endpoint, tried once when
the first stalls or fails; the progress log says so (`no tokens from X in 30s —
switching to Y`).

Every call is recorded from the moment it starts — model, the upstream that
actually served it, time to first token, tokens streamed, the longest silence,
the outcome, and the exact prompt and reply — and the progress run exposes them
to a closed-by-default **Inspect model calls** panel under the generation log.
It is memory only and leaves with the run.
