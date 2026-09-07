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
git clone <this repo> && cd freetcoder
FREETCODER_LLM_API_KEY=sk-or-... make dev
```

Then open **http://localhost:5173**.

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
| `FREETCODER_LLM_API_KEY` | *(empty)* | Held in process memory; never written to disk |
| `FREETCODER_LLM_MODEL` | `anthropic/claude-sonnet-4.5` | |
| `FREETCODER_DB_PATH` | *(empty → in-memory)* | Set to `/data/freetcoder.db` with a volume |
| `FREETCODER_FAKE_LLM` | `0` | `1` serves recorded questions; no key or network needed |
| `FREETCODER_LIBRARY_URL` | *(empty)* | Question library service; empty disables save/browse |

**Local models.** Point the base URL at `http://host.docker.internal:11434/v1`
for Ollama and use any non-empty key. Smaller models often fail the solvability
gate repeatedly — check the acceptance rate before blaming the app:

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

### The editor is a real one

Monaco talking LSP over a WebSocket to language servers running in the
container — `pyright` for Python, `typescript-language-server` for JavaScript
and TypeScript: completion, hover tooltips, signature help, go-to-definition and
live diagnostics. If a language server dies, editing keeps working and only the
intelligence degrades.

### Editable test cases

The Testcase tab is an editor, not a display. Change the provided examples, add
as many cases as you like, duplicate or delete them. Leave the expected value
blank to just see what your code returns; fill it in to get pass/fail. Your cases
run on **Run** only and never affect your score — Submit always uses the
question's own examples plus its hidden cases.

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
payoff — generating and validating a question costs tokens and tens of seconds,
so replaying one is a large win. Attempt history is kept too.

---

## Development

```bash
make dev        # library :8090 + backend :8081 + Vite HMR :5173  (open 5173)
make check      # ruff, mypy, pytest, tsc
make test       # backend tests only
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
docs/container-discovery.md   why the Dockerfile and build config look like this
docs/testing.md               the four suites, what they cost, and what e2e-prod caught
```

**Read `docs/container-discovery.md` before changing the Dockerfile, the runner,
or the Monaco setup.** It records the failures behind decisions that look
arbitrary — why the monaco stack is pinned in `overrides`, why there is no
React `StrictMode`, why `RLIMIT_NPROC` is only a backstop, why workers must be
ES modules.

---

## Status

Python, JavaScript and TypeScript working end to end — 243 backend tests,
26 library tests, 28 browser tests.

Not yet built: Go (needs an adapter and toolchain; the compile phase it requires
already exists for TypeScript), SQL concentration (needs a SQLite runner
adapter), system design (no executable answer, needs LLM-rubric grading; flagged
in the UI), attempt analytics.
