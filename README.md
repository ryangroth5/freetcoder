# freetcoder

A free, fully local, LLM-generated coding-assessment environment. Run one
container, open a browser, choose a test format, and practise against questions
that are generated *and machine-validated* on the spot — in a VS Code–like
editor with real autocomplete, hover tooltips and live type errors.

Nothing leaves your machine except the calls you make to the LLM endpoint you
configure.

---

## Quick start

```bash
git clone https://github.com/ryangroth5/freetcoder && cd freetcoder
cp .env.example .env        # then put your key in it
make dev
```

The key goes in `.env` and nowhere else. It is deliberately the one setting
that is never stored in the database: keeping the credential out of the app's
own data means the settings API cannot be turned into an exfiltrator by
repointing the endpoint at a hostile host.

Then open **http://localhost:5173**.

**Expect the first question to take one to several minutes.** Almost all of
that is the model writing it; freetcoder's own share — linting, type-checking
and executing the result — is about two seconds. The progress panel names each
step as it runs and tells you afterwards where the time went, so you can see
which it was. `deepseek/deepseek-v4.1-flash` was the fastest model measured
that also produced good questions; see [Which model](#which-model).

**New here?** [docs/using-freetcoder.md](docs/using-freetcoder.md) is the guide
to actually using it — the screens, the tutor, what Run and Submit each do, and
what to check when something looks wrong. The rest of this file is the overview
and the design rationale.

**Curious what came out of building it?**
[docs/findings.md](docs/findings.md) is the short version: why asking a model
for JSON was the bottleneck, what four models actually scored, and the three
bug classes that cost the most time.
[docs/architecture.md](docs/architecture.md) is how the pieces fit.

Prefer to kick the tyres with no API key at all? Recorded questions are served
in offline mode:

```bash
FREETCODER_FAKE_LLM=1 make dev
```

### Shipping container (single port, no mounts)

```bash
docker compose --profile prod up --build prod    # http://localhost:8080
```

Or directly:

```bash
docker run -p 8080:8080 \
  -e FREETCODER_LLM_API_KEY=sk-or-... \
  -e FREETCODER_LLM_BASE_URL=https://openrouter.ai/api/v1 \
  -e FREETCODER_LLM_MODEL=anthropic/claude-sonnet-4.5 \
  -v freetcoder:/data \
  freetcoder-prod
```

The `-v` is optional but recommended — see [Persistence](#persistence).

---

## Configuration

Any OpenAI-compatible endpoint works. Set these as environment variables, or
enter them on the app's setup screen.

| Variable | Default | Notes |
|---|---|---|
| `FREETCODER_LLM_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter, Ollama, vLLM, LM Studio… |
| `FREETCODER_LLM_API_KEY` | *(empty)* | Put it in `.env`. Never persisted, never returned by the API |
| `FREETCODER_LLM_MODEL` | `anthropic/claude-sonnet-4.5` | |
| `FREETCODER_DB_PATH` | *(empty → in-memory)* | Set to `/data/freetcoder.db` with a volume |
| `FREETCODER_FAKE_LLM` | `0` | `1` serves recorded questions; no key or network needed |
| `FREETCODER_LIBRARY_URL` | *(empty)* | Question library service; empty disables save/browse |

Tunables, all also editable from the Settings page. The first three cost tokens
when raised:

| Variable | Default | Notes |
|---|---|---|
| `FREETCODER_GENERATION_STRATEGY` | `module` | How a question is asked for: `module` (the model writes a Python file we lint, type-check and run) or `monolithic` (one JSON payload). See [question-generation.md](docs/question-generation.md) |
| `FREETCODER_GENERATION_ATTEMPTS` | `4` | Regenerations before giving up on a question |
| `FREETCODER_REPAIR_ROUNDS` | `3` | Patch-and-re-gate rounds before regenerating |
| `FREETCODER_CHECK_STATEMENT_SUFFICIENCY` | `1` | Second model solves from the prose alone; roughly doubles generation cost |
| `FREETCODER_TOOL_CALL_BUDGET` | `6` | Code executions the model may make while repairing. `monolithic` only — the module strategy's critics already run the code |
| `FREETCODER_TUTOR_TOOL_BUDGET` | `4` | Reference probes the tutor may make per reply |
| `FREETCODER_TUTOR_MESSAGE_CAP` | `60` | Tutor messages per session |
| `FREETCODER_LLM_TIMEOUT_S` | `300` | Per request, not per question. A slow model writing a whole module can take minutes |
| `FREETCODER_LLM_FIRST_TOKEN_S` | `30` | Generation streams; no token by then and the call is abandoned instead of waited on |
| `FREETCODER_LLM_IDLE_S` | `60` | Silence allowed between tokens once a reply has started |
| `FREETCODER_LLM_FALLBACK_MODEL` | *(empty)* | A second model on the same endpoint, tried once when the first stalls or fails |
| `FREETCODER_LLM_MAX_RETRIES` | `3` | |

The HTTP API documents itself: **`/docs`** serves Swagger UI and
`/openapi.json` the schema, so there is no hand-written route table here to fall
out of date.

Everything except the key is also editable at runtime from the **Settings**
page (the gear on the picker or the problem header), and a value saved there
wins over the environment — because a settings page whose fields silently do
nothing is worse than no settings page. Each field shows its source, and
*Reset* returns it to the environment value.

Server settings persist in the SQLite database, so they need a volume;
`FREETCODER_DB_PATH` empty means in-memory and the page says so rather than
pretending. Theme and default language are stored per browser instead, so two
people sharing a container do not overwrite each other.

### Which model

The default is `anthropic/claude-sonnet-4.5`, which has not
been measured on this pipeline. What has, on OpenRouter, three questions each:

| model | accepted | first try | complete prose |
|---|---|---|---|
| `deepseek/deepseek-v4.1-flash` | 3/3 | 3/3 | 3/3 |
| `moonshotai/kimi-k2.5` | 2/3 | 2/2 | 2/2 |
| `z-ai/glm-4.6` | 2/3 | 0/2 | 1/2 |

Three questions per model is thin evidence and worth treating as such, but
`deepseek-v4.1-flash` is the one to reach for on a budget: it was the only one
that took no revision rounds, and the fastest. `deepseek/deepseek-chat` is not
a coding model and fails this pipeline; do not confuse the two.

**Local models.** Point the base URL at `http://host.docker.internal:11434/v1`
for Ollama and use any non-empty key. Smaller models often fail the solvability
gate repeatedly — check the acceptance rate before blaming the app. This runs
whichever strategy the server is set to, so it measures what you would actually
get:

```bash
docker compose run --rm dev python -m freetcoder.generate --style leetcode -n 10
```

---

## What it does

### One format choice drives three things

Picking a style resolves a single config object that determines how the coding
environment is set up, how the question is prompted for, and how the attempt is
scored. The presets encode what actually distinguishes these platforms:

| | Structure | Scoring | Support |
|---|---|---|---|
| **LeetCode** | 1 question, untimed | pass/fail | examples + hint |
| **CodeSignal GCA** | 4 questions, 70 min, ascending | 200–600 composite | **no hints** |
| **Codility** | 3 tasks, 120 min | % of tests, performance graded separately | stated complexity target |
| **Coderbyte** | employer-configurable | weighted, **speed counts** | examples + hint |

The picker is three tiers of buttons — style → preset → concentration — each
ending in an **Other…** free-text field. That text *narrows* the tier above it;
it never rewrites the format's structure. Asking a GCA for "a single untimed
easy question" still gets you four questions in seventy minutes.

Formats that model a fixed real assessment lock their difficulty curve. A GCA
whose difficulty you choose is not simulating a GCA.

### Questions are proven solvable before you see one

LLM output is untrusted. Every candidate question is executed before it is
served, and rejected if any of this fails:

1. The reference solution runs.
2. It reproduces the examples printed in its own statement. *(This is what
   catches a wrong oracle.)*
3. The hidden-case generator produces parseable cases.
4. The reference passes all of them inside the time budget.
5. If a complexity target is claimed, a deliberately naive solution must **agree**
   with the reference on small inputs (proving the reference is right) and
   **time out** on large ones (proving the tests discriminate). Without this a
   performance score is decoration.

Expected outputs for hidden cases are **never taken from the model** — they are
computed by executing the reference solution.

Rejections are fed back to the model with the specific complaint, then retried.

Measure the acceptance rate for a format at any time:

```bash
docker compose run --rm dev python -m freetcoder.generate \
    --style codility --preset performance -n 20
```

### A second model checks the question can be answered

Every other check validates a question against itself; none of them read the
prose. So a second model solves each question from the **statement, constraints
and examples alone** — no reference, no hidden cases — and its answer is run
against the oracle. Disagreement means the statement is missing something a
candidate would need, and it is rewritten.

This is the only check that validates what you actually read. It roughly doubles
generation cost, and `FREETCODER_CHECK_STATEMENT_SUFFICIENCY=0` turns it off.

### The editor is a real one

Monaco talking LSP over a WebSocket to language servers running in the
container — `pyright` for Python, `typescript-language-server` for JavaScript
and TypeScript: completion, hover tooltips, signature help, go-to-definition and
live diagnostics. Syntax colouring comes from the same TextMate grammars VS Code
ships. If a language server dies, editing keeps working and only the
intelligence degrades.

### A tutor that cannot leak the answer

A chat beside the test results, with your code, your run history, the failing
case and a *characterisation* of the hidden tests — how many, what shapes — so
it can help you reason about valid input. It can query the reference solution
for what a given input produces, and report the result.

It never sees the reference's source and never the hidden cases verbatim:
either alone is harmless, but together they are a lookup table. It is disabled
during GCA and Codility sessions until you submit, because a timed assessment
that ships an AI assistant is not simulating anything.

### Bring your own question

Describe one ("something about counting dogs") or paste one you saw elsewhere.
It is tightened into a well-defined problem, given a reference solution and
hidden tests, and validated like any generated question. Every judgement call it
had to make is recorded in a note you can read, because being told "ties break
toward the earliest word" up front is the difference between an informed answer
and a baffling failure.

### Told what is happening, and whether it is real

Generation takes one to several minutes — nearly all of it waiting on the
provider — so a panel names each step as it runs, with a
cancel that is honest about what it cannot interrupt.

A status dot next to Settings says whether the app is really talking to a
provider: **live**, **offline** (recorded questions), or **no key**. It exists
because a valid key and `FREETCODER_FAKE_LLM=1` can be true at once, and
`configured` and `has_key` were both true while every question came from a
fixture.

### Editable test cases

The Testcase tab is an editor, not a display. Change the provided examples, add
as many cases as you like, duplicate or delete them. Leave the expected value
blank to just see what your code returns; fill it in to get pass/fail, or press
**Compute expected** to ask the intended solution what those arguments produce.
Your cases run on **Run** only and never affect your score — Submit always uses
the question's own examples plus its hidden cases.

### A shared question library

Press **☆ Save** on a question worth keeping and it goes to a separate library
service; pick **From the library** on the start screen to solve saved questions
without generating anything. Submissions are ranked against everyone else's,
LeetCode-style.

That ranking is a *ratio*, never a wall-clock time: your runtime divided by the
reference solution's, both measured on your machine moments apart, so hardware
and load cancel out. See [docs/library-service.md](docs/library-service.md).

The library is optional and can never break local practice — unconfigured or
unreachable, everything except saving and browsing works exactly as before.

### Three languages, one contract

Python, JavaScript and TypeScript are selectable per question; each language
keeps its own buffer, so switching never discards work. TypeScript is compiled
with `tsc`, so type errors are reported as compile errors rather than being
stripped away unchecked.

Every language implements the harness contract in
[docs/execution-protocol.md](docs/execution-protocol.md), enforced by a
conformance suite parameterized over all of them. The rule that matters: a
submission's own output must never reach the result channel. Getting that wrong
does not merely crash — it silently misgrades correct answers.

Adding a language means writing an adapter in `backend/freetcoder/runner/
adapters.py` and making `tests/test_language_conformance.py` pass. There is
deliberately no other way in.

---

## Sandboxing

The container is the security boundary — single user, no mounts, one exposed
port. Inside it, every submission additionally runs:

- as an unprivileged `runner` uid, in a per-run temp directory
- under `RLIMIT_CPU` / `RLIMIT_AS` / `RLIMIT_NPROC` / `RLIMIT_FSIZE`
- with a wall-clock kill and full **process-group** teardown
- with a `seccomp` filter denying every network syscall
- with capped stdout/stderr

Verified contained: infinite loops, memory bombs, fork bombs, output floods,
reads of `/etc/shadow`, writes to `/srv/app` `/etc` `/usr/local` `/`, outbound
connections and DNS.

This needs **no elevated Docker privileges** — notably not `--privileged`, which
is why the project does not use Judge0 or Piston. Those solve *multi-tenant*
isolation and would weaken the boundary here.

> Assume a determined attacker with code execution can still reach the rest of
> the container. That is an accepted trade-off for a single-user local tool.

## Persistence

Optional. With no volume the database is in memory and everything works, just
forgetfully. Attaching one caches **gate-approved questions**, which is the real
payoff — generating and validating a question costs tokens and minutes,
so replaying one is a large win. Attempt history and server settings are kept
too.

`dev` and `prod` use **separate** volumes — `freetcoder-dev-data` and
`freetcoder-data`. They shared one until test runs were found writing cached
questions into the database behind the app you actually practise against.

---

## Development

```bash
make dev        # library :8090 + backend :8081 + Vite HMR :5173  (open 5173)
make check      # ruff, mypy, pytest, tsc
make test       # backend tests only
make web-test   # frontend unit tests (vitest)
make e2e        # Playwright vs the dev stack
make e2e-prod   # Playwright vs the built production image
make shell      # shell in the dev container
```

Backend tests **must** run in-container (`make test` does): on a macOS host
there is no `runner` user and no libseccomp, so the containment tests would
pass vacuously.

The dev source mount is **read-only**. A submission once escaped through a
writable mount and wrote into the host repo — macOS bind mounts ignore `chmod`,
so read-only is the only thing that stops it.

### Layout

```
backend/freetcoder/
  runner/      sandboxed execution (rlimits, setuid, seccomp)
  generate/    prompts, generation loop, solvability gate
  formats/     style presets + resolve(); the one place the picker tiers merge
  llm/         OpenAI-compatible client + offline FakeLLM
  api.py       HTTP routes            service.py   orchestration
  scoring.py   per-platform scoring   storage.py   SQLite
  lsp_bridge.py  WebSocket <-> language-server stdio
frontend/src/  React + Monaco + the three-tier picker
docs/using-freetcoder.md      how to use the app (the only user-facing doc)
docs/container-discovery.md   why the Dockerfile and build config look like this
docs/testing.md               the four suites, what they cost, and what e2e-prod caught
docs/question-generation.md   how a question is generated, gated and repaired
docs/findings.md              what the measurements showed, and the lessons
docs/architecture.md          how the pieces fit at runtime
```

**Read `docs/container-discovery.md` before changing the Dockerfile, the runner,
or the Monaco setup.** It records the failures behind decisions that look
arbitrary — why the monaco stack is pinned in `overrides`, why there is no
React `StrictMode`, why `RLIMIT_NPROC` is only a backstop, why workers must be
ES modules.

---

## Status

Python, JavaScript and TypeScript work end to end. **663 backend tests, 27
library tests, 14 frontend unit tests and 59 browser tests**, the last run
against both the dev stack and the built production image.

Questions are generated as Python modules and validated by execution before you
see them; measured across four models, the best of them produced three
acceptable questions out of three on the first try. The numbers are in
[docs/findings.md](docs/findings.md).

## Roadmap

Honest about what is missing, roughly in the order it would be worth doing.

**Reduce the wait.** A question takes one to several minutes, and 98–99% of
that is the provider. There is nothing left to optimise in the pipeline — our
own share is about two seconds — so this is a question of model choice and
routing, not code.

**Resumable sessions.** There is no client-side routing at all: a session has no
URL, so a reload loses the problem you were working on. This is the most
obviously missing thing for anyone practising seriously.

**Go.** `Language.GO` exists in the enum and maps to `gopls`, but there is no
runner adapter and no toolchain in the image. The compile-then-run phase it
needs already exists for TypeScript, so the shape is known.

**SQL and system design.** SQL needs a SQLite runner adapter. System design has
no executable answer at all and would need rubric grading by a model, which
sits awkwardly beside a project whose whole premise is machine-checkable
validation. Both are flagged as unsupported in the picker rather than quietly
producing bad questions.

**Multi-file questions.** The interface leaves room for a `FILES` mapping —
enough for React-style problems or questions graded by a unit-test suite — but
the workspace writer would need directory support and traversal checks first.

**Attempt analytics.** Submissions and verdicts are already stored; nothing
reads them back to tell you what you keep getting wrong.

## Licence

MIT — see [LICENSE](LICENSE). Contributions welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md).
