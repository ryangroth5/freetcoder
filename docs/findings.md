# What we measured, and what it taught us

> The conclusions, without the archaeology. The chronological log — every
> experiment, in the order it happened — is in
> [question-generation.md](question-generation.md).

The problem this project set out to solve: **can a language model author a
coding-assessment question that is provably solvable?** Not "does it look
plausible" — provably, in the sense that a machine has run the reference
solution against generated tests and agreed with itself before a human sees it.

The answer is yes, and almost everything we learned getting there was about
something other than the model's ability to reason.

---

## 1. It was the wire format, not the model

The first design asked for one JSON object with fourteen fields: statement,
constraints, reference solutions per language, a hidden-case generator, a brute
force, worked examples. It failed constantly, and every failure looked like the
model being incapable.

It wasn't. Three of four models returned whole Python functions on a single
line, newlines replaced by double spaces, because the code was inside a JSON
string and indentation is syntax in Python. Models dropped `name` from every
entry of a nested list. They wrote `{"args": [[42], 0]}` positionally where a
mapping was demanded — the same model, on consecutive calls, doing it both
ways.

None of that is a model failing to write a coding question. It is a model
fighting a wire format.

So we stopped asking for data and started asking for **a Python module
implementing a fixed interface**:

```python
TITLE: str
STATEMENT: str          # markdown the candidate reads
CONSTRAINTS: str
TOPICS: list[str]
HINT: str
COMPLEXITY: str
CLARIFICATIONS: list[dict]

def solution(...): ...        # the oracle
def brute_force(...): ...     # obvious and slow, for cross-checking
def is_valid(...) -> bool:    # are these arguments legal input?
def generate_cases(rng): ...  # hidden test inputs, inputs only
EXAMPLES: list[dict]
```

Measured on the same model, same prompts, same gate:

| strategy | accepted |
|---|---|
| monolithic JSON, 14 fields | **0 / 3** |
| delimited text sections | 1 / 4 |
| **a Python module** | **4 / 6** |

Code is code. Nothing needs escaping, because it is a file and not a string.
Validation stops being parsing and becomes procedure: lint it, type-check it,
import it, call it — and hand back whatever the failing step said, which is the
loop these models are already tuned for inside coding agents.

Two things the shape deletes outright:

- **Expected values are never stated.** They come from calling `solution`, so
  "you claimed `[0, 3]` but your reference returns `[1, 3]`" cannot happen.
- **The function name, parameter names and starter code come from
  `inspect.signature`**, so they cannot disagree with the solution they
  describe.

## 2. Model-agnostic, once our own bugs were out of the way

Three questions each, seeded with a random real-world setting to discourage
recall:

| model | accepted | first try | complete prose | recalled a known problem |
|---|---|---|---|---|
| `deepseek/deepseek-v4.1-flash` | **3/3** | 3/3 | 3/3 | 0 |
| `moonshotai/kimi-k2.5` | 2/3 | 2/2 | 2/2 | 0 |
| `z-ai/glm-4.6` | 2/3 | 0/2 | 1/2 | 0 |
| `deepseek/deepseek-chat` | 0/3 | — | — | — |

The first run of this table scored **every model worse**, and every single
rejection in it turned out to be our bug, not theirs: four cases of a
hidden-case generator that killed itself, and one Markdown fence we failed to
strip.

`deepseek-chat`'s 0/3 has its own lesson. Two of its three attempts were
HTTP 429s from a shared upstream pool — and a rate limit arrives as "the model
did not answer", which scores identically to a model that answered badly. The
table read like a capability result when two-thirds of it was queueing. **Read
the log before believing the table.**

## 3. The pipeline is not the slow part

Timed directly, with no provider in the loop:

| stage | time |
|---|---|
| ruff + pyright + the sandboxed probe | 0.5s |
| generating a module, including two language translations | 0.9s |
| the full gate on a real 25-case question, three languages | 0.8s |

Against provider time of **68–857 seconds** for the same work. In benchmark
runs the provider accounted for 98–99% of wall clock on every model.

There is no meaningful optimisation available on our side of that line. Making
generation faster means choosing a different model, which is why the progress
log now ends by saying where the time actually went:

```
took 3m 47s: 3m 45s waiting on the model, 2s checking it
```

## 4. Three bug classes we hit more than once

These generalise well past this project.

### Checks that cannot fail

Three separate instances, each of which read as evidence while proving nothing:

- The gate's brute-force cross-check was handed the same module as both the
  reference *and* the brute force, so it called `solution` twice and agreed
  every time.
- An empty `constraints` list satisfied "every parameter has a constraint"
  vacuously, and shipped a question with no stated bounds at all.
- A migration meant to delete stale cached questions protected "anything a
  session references" — which, since every cached question was created *for*
  some session, was all 802 of them.

The last one is the clearest: the safeguard looked careful, read as
conservative, and deleted nothing. **A check that cannot fail is worse than no
check, because it is mistaken for one.**

### Silent degradation

Worse than a crash, because nothing reports it:

- The hidden-case generator hit the sandbox's 64KB output cap, was truncated
  mid-line, and yielded **14 of 40 cases while still reporting success**. A
  question would have shipped graded on a third of the tests it promised.
- The same generator embedded its cases as a Python *literal*, so the
  interpreter built millions of boxed integers before printing them straight
  back out as JSON. Forty cases of a hundred thousand elements died with
  `MemoryError` — a question discarded for being exactly as large as the format
  asked it to be.

### Retries that multiply invisibly

Four loops re-ask the model — the client's retries, the strategy's per-stage
tries, its repair rounds, and whole regenerations. None of them can see the
others, and they compose by multiplication. Nobody chose the product.

It surfaced as a progress step sitting at **677 seconds** reading "writing
question 1 — first try". A 300-second deadline had fired three times inside the
client, which logged to the container and reported nothing. A working call in
the same run took 103 seconds.

The fix was two-part, and the second half is the transferable one: a timeout is
no longer retried at all (a 429 says *momentarily unable*; a timeout says *too
slow for this budget*, and the identical request will be too), and every re-send
now announces itself. **A retry that reports nothing is indistinguishable from
a hang.**

## 5. Measure the metric before believing it

One question was flagged as having a thin statement, and we nearly went
prompt-hunting for it.

The metric asked whether a worked example's values appear in the prose, by
testing whether `json.dumps(value)` occurs as a literal substring. That demands
`[4, 6, 5, 9]` — spaces after the commas included. Of four perfectly
well-written statements, **three scored as showing nothing**: one wrote
`[4,6,5,9]`, one laid the example out in prose, one merely wrapped the line.

The model was about to be blamed for a formatting bug in the scorer. Worse, the
bench recorded that a question scored badly and kept no copy of its text, so the
question "is it really thin, or is the metric wrong?" could not be answered by
reading it. It records the statement now.

## 6. Thinking less is a trade, not a free speedup

A live generation streamed 11,462 chunks for a module of roughly 2,000 tokens:
most of a two-minute call was the model reasoning. So we made reasoning effort
a setting and measured it the way that matters — seconds per *accepted*
question, counting the time spent on rejections and revision rounds, through
the same path the app runs.

First, a cheap probe of whether models honour the setting at all. They don't,
uniformly: kimi-k2.5 went from ~600 reasoning tokens to zero (and got a simple
arithmetic question wrong without them); deepseek-v4.1-flash stopped reasoning
privately and did it out loud in its answer instead; mimo-v2.5 reasoned *more*
when told "none". **A reasoning setting is a request, not a guarantee.**

Then three questions per setting:

| model | thinking | accepted | first try | typical | seconds per accepted |
|---|---|---|---|---|---|
| deepseek-v4.1-flash | default | 3/3 | 3/3 | 77s | **107** |
| deepseek-v4.1-flash | low | 3/3 | 3/3 | 97s | 132 |
| deepseek-v4.1-flash | none | 3/3 | 2/3 | 42s | 148 |
| moonshotai/kimi-k2.5 | none | 3/3 | 1/3 | 312s | **373** |
| moonshotai/kimi-k2.5 | default | 3/3 | 2/3 | 719s | 784 |
| moonshotai/kimi-k2.5 | low | 2/3 | 2/2 | 688s | 1,200 |

What it says:

- **Model choice dwarfs the setting.** flash at its default is 3.5x faster than
  kimi at its best.
- **Turning thinking off pays in proportion to how much a model thinks by
  default.** kimi thinks heavily and halved its time. flash thinks briefly: its
  typical question got faster, but one needed a revision round and took 372s,
  so the average got worse.
- **It lowers first-try success on both** (3/3 → 2/3, 2/3 → 1/3), yet not one
  of the six questions failed outright. The revision loop recovered every one.
  The gate is what makes "none" safe to try at all.
- **`low` was never the answer.** flash ignored it; kimi at `low` was the
  slowest and produced the only rejection.
- **That rejection was a limit, not a model.** Two kimi calls were still
  producing tokens when the 300-second overall limit ended them. Since streamed
  calls are already caught within a minute if they are genuinely stuck, the
  overall limit now defaults to 600s.

Three questions per cell is thin, and single outliers — 372s, 1,440s — move
these averages a long way. The direction is clear enough to act on; the exact
ratios are not.

## 7. What holds the whole thing together

One rule, and it is the reason any of the above is checkable:

**The gate is not repairable.** `validate_question` is the trust anchor. The
model may rewrite a question's statement, solutions, generator, brute force,
constraints — anything the question is made of. It may never touch the
validator. A loop that could weaken its own validator could make anything pass,
and the guarantee that questions are provably solvable would be worthless.

That single constraint is why this is a few hundred lines of pipeline rather
than an agent framework: **the objective is machine-checkable**, so the model
never gets to decide whether it is finished.
