# Architecture

> How the pieces fit at runtime. For *why* several of them look the way they
> do — the failures behind decisions that otherwise seem arbitrary — read
> [container-discovery.md](container-discovery.md).

## Containers

Three services, only one of which is required.

```
dev profile                          prod profile
┌──────────────────────┐             ┌──────────────────────┐
│ web   :5173          │             │ prod  :8080          │
│  Vite + HMR          │             │  one image:          │
│  proxies /api, /lsp ─┼──┐          │  FastAPI + built     │
└──────────────────────┘  │          │  frontend + runtimes │
┌──────────────────────┐  │          └──────────┬───────────┘
│ dev   :8081          │◀─┘                     │
│  uvicorn --reload    │                        │
│  ./backend mounted   │                        │
│  READ-ONLY           │             ┌──────────▼───────────┐
└──────────────────────┘             │ library :8090        │
                                     │  optional, shared    │
                                     │  question archive    │
                                     └──────────────────────┘
```

**dev** bind-mounts `./backend` **read-only**. That is not tidiness: a
submission once escaped through a writable mount and wrote into the host repo,
and macOS bind mounts ignore `chmod`, so read-only is the only thing that
actually stops it.

**prod** is a single self-contained image on one port, with an optional `/data`
volume. It bakes the built frontend, so it needs a rebuild to pick up changes —
unlike dev, which reloads.

**library** is optional (`FREETCODER_LIBRARY_URL` empty disables it). It stores
accepted questions so several people can practise the same set and compare
submissions.

## The path a question takes

```
POST /api/sessions
  └─ api.py                         opens a progress run, times the provider
     └─ service.obtain_question     picks the strategy, owns the cache
        └─ generate_question_as_module
           ├─ generate_module       ── one provider call ──▶ a Python file
           │  ├─ alarming_source    a cheap scan for obviously destructive code
           │  ├─ ruff               syntax, undefined names
           │  ├─ pyright            types
           │  ├─ probe (sandboxed)  import it, introspect it, run it
           │  └─ translate_signature   one more call per extra language
           ├─ validate_question     ◀── THE GATE
           └─ check_statement_sufficiency
                                    a second model solves from the prose alone
        └─ storage.cache_question   keyed by style, preset, difficulty,
                                    languages AND strategy
```

Each critic hands its own output back to the model as the retry message, which
is the loop these models are tuned for. A gate rejection is answered by
*revising* the module rather than writing a new one — measured, a module costs
one to twenty minutes of provider time, so discarding it because the brute
force ran too fast is the expensive way to fix a cheap problem.

### The interface

The contract lives in
[`generate/interface/question_interface.py`](../backend/freetcoder/generate/interface/question_interface.py)
and is handed to the model **verbatim** — a specification it can read rather
than prose about one. Module-level names, not a class, so the same shape
translates to exported functions in TypeScript and package functions in Go.

`is_valid` is the interesting one. It replaces a list of min/max bound objects —
the shape models kept mangling — with a predicate we can simply call. Strictly
more expressive, and nothing to mis-nest. Two guards stop it being gamed: its
source must reference its own parameters (a body returning `True` regardless is
refused), and every case `generate_cases` yields must satisfy it.

### The gate

[`generate/gate.py`](../backend/freetcoder/generate/gate.py) is synchronous,
LLM-free and deterministic. It runs the reference against the statement's
examples, executes every clarification's probe, materialises the hidden cases,
computes expected answers *from the reference* rather than from the model,
checks a naive solution cannot pass when a complexity target is claimed, and
runs every other language's reference against the Python oracle's answers.

**It is never repairable.** The model may rewrite anything the question is made
of; it may not touch the validator. See
[findings.md](findings.md#7-what-holds-the-whole-thing-together).

## Running untrusted code

Everything executed — candidate submissions, model-written references, the
probe — goes through the same sandbox
([`runner/sandbox.py`](../backend/freetcoder/runner/sandbox.py)):

- a drop to an unprivileged `runner` uid
- `setsid`, so the whole process group can be killed rather than just the child
- `RLIMIT_CPU`, `AS`, `NPROC`, `FSIZE`, `CORE`
- a `pyseccomp` filter blocking network syscalls
- a disposable workspace, wiped after each run

`RLIMIT_NPROC` is a backstop only — it counts processes per *uid across the
whole kernel*, and under Docker Desktop uid 999 already carries a baseline we
do not control. The process-group kill is the real containment.

Language support is per-adapter (`runner/adapters.py`): Python, JavaScript and
TypeScript are implemented. `Language.GO` exists in the enum with a `gopls`
mapping but **has no adapter and no toolchain in the image** — it is declared,
not built.

Results cross the boundary as JSON lines stamped with a marker, so a
submission's own `print` can never be mistaken for a result. That protocol is
documented in [execution-protocol.md](execution-protocol.md).

## Storage

SQLite, one file, optional. `FREETCODER_DB_PATH` empty means in-memory and the
app says so rather than pretending.

The question cache is a **fallback, not a source**. An earlier version preferred
the cache whenever one existed, which meant a given format generated exactly one
question and then replayed it forever — the tool's defining feature quietly
traded away to save tokens. It now only serves cached questions when generation
fails, and the cache key includes the generation strategy so questions from
different pipelines never mix.

The API key is deliberately the one setting **never** stored in the database.
Keeping the credential out of the app's own data means the settings API cannot
be turned into an exfiltrator by repointing the endpoint at a hostile host.

## Frontend

React + Monaco, three seams worth knowing:

- **Monaco** runs the real VS Code language services in a worker, which is why
  autocomplete, hover and type errors are genuine rather than regex
  highlighting. The stack is version-pinned in `overrides` for reasons
  `container-discovery.md` records.
- **The LSP bridge** (`lsp_bridge.py`) is a transparent byte pump between a
  WebSocket and a language server's stdio — `pyright-langserver` for Python,
  `typescript-language-server` for JS/TS.
- **The progress channel** is polled, not streamed: the payload is tiny, it
  survives a reload, and a second of latency is irrelevant against an operation
  measured in minutes. Each step carries its start offset, so the UI renders per
  step durations and counts the in-flight one live.

There is no client-side routing. A session is not addressable by URL, and a
reload loses it — see the roadmap in the [README](../README.md).
