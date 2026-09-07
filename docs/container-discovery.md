# Container discovery transcript (Phase A)

Findings from an interactive `python:3.12-slim-bookworm` session on **linux/arm64**
(Docker 28.0.1). Every line in the Dockerfile traces back to something verified here.

## Base

- `python:3.12-slim-bookworm` → Python 3.12.14, Debian 12. Debian (not Alpine) confirmed
  necessary: pyright ships a Node runtime and glibc keeps native wheels working.
- Node 22.23.2 / npm 10.9.8 via `deb.nodesource.com/setup_22.x`.
- apt packages needed: `curl ca-certificates gcc make procps libseccomp2`.

## Language server — VERIFIED WORKING

`npm i -g pyright@1.1.406` installs both `pyright` and `pyright-langserver`.
A raw JSON-RPC `initialize` over stdio returned:

| Capability | Present |
|---|---|
| completionProvider | yes |
| hoverProvider | yes |
| signatureHelpProvider | yes |
| definitionProvider | yes |
| diagnostics | yes |

`textDocument/didOpen` with `f('hello')` against `def f(x: int)` produced:
`Argument of type "Literal['hello']" cannot be assigned to parameter "x"`.

**Bridge design note:** pyright emits two `window/logMessage` notifications *before* the
`initialize` response. The WS↔stdio bridge must be a transparent byte pump that
correlates by message `id`, never a request/response pair.

## Runner sandbox — VERIFIED, with two bugs found and fixed

Design: `preexec_fn` doing `setsid` → rlimits → `setgid`/`setgroups([])`/`setuid(runner)`
→ `PR_SET_NO_NEW_PRIVS` → seccomp filter.

Containment results:

| Attack | Result |
|---|---|
| `while True: pass` | wall-clock kill, SIGKILL to process group |
| `[0]*10**9` | `MemoryError` via `RLIMIT_AS` |
| fork bomb | `BlockingIOError` via `RLIMIT_NPROC` |
| 1 GB stdout | `MemoryError`, capped |
| `/etc/shadow` | `PermissionError` |
| write `/srv/app`, `/etc`, `/usr/local`, `/` | all `PermissionError` |
| `socket.create_connection` | `PermissionError` (seccomp) |
| DNS lookup | `gaierror` (seccomp) |

### Bug 1 — network egress was wide open
`unshare -n` fails: default Docker grants no `CAP_SYS_ADMIN` (`CapEff: a80425fb`).
Solution that needs **no extra container capabilities**: a `pyseccomp` filter erroring
`socket/socketcall/connect/bind/listen/accept/accept4/sendto/recvfrom` with EPERM.

Two non-obvious requirements, both discovered by failure:
1. **`PR_SET_NO_NEW_PRIVS` (prctl 38) must be set before `load()`** — otherwise loading a
   filter requires `CAP_SYS_ADMIN` and fails.
2. **`import pyseccomp` must happen in the parent, before fork.** It calls
   `ctypes.util.find_library`, which *shells out to `ldconfig`* — that fork fails after
   `setuid` under `RLIMIT_NPROC`, and `subprocess` reports only the useless
   "Exception occurred in preexec_fn."

### Bug 2 — submissions escaped through the dev bind mount
A submission wrote `/app/PWNED` into the host repo. Docker Desktop's macOS bind mount
ignores unix ownership, so `chmod` cannot protect it. Without the mount, every write
outside the per-run tmpdir is denied.

**Consequence for docker-compose:** the dev source bind mount must be **read-only** (`:ro`).
uvicorn `--reload` and Vite only read source; `venv/` and `node_modules/` are named volumes.
The prod image has no mount at all, so it is unaffected.

`RLIMIT_NPROC=32` also blocks `subprocess`/`multiprocessing` in submissions. Intended.

## Frontend — arm64 clean

All install without conflict: react 18.3.1, monaco-languageclient 9.5.0,
vscode-ws-jsonrpc 3.4.0, @typefox/monaco-editor-react 6.7.0, react-resizable-panels 4.12.3,
react-markdown 10.1.0, remark-gfm 4.0.1, rehype-sanitize 6.0.0, mermaid 11.17.2, zustand 5.0.15.

### The Monaco duplication trap
`monaco-languageclient` depends on `@codingame/monaco-vscode-editor-api`. Installing plain
`monaco-editor` alongside it yields **two copies of the Monaco API**, which breaks the language
client at runtime. Fix, verified to dedupe to exactly one copy:

```json
"dependencies": { "monaco-editor": "npm:@codingame/monaco-vscode-editor-api@~15.0.2" },
"overrides":    { "monaco-editor": "npm:@codingame/monaco-vscode-editor-api@~15.0.2" }
```

## Backend — verified imports

fastapi 0.141.1, pydantic 2.13.5, openai 3.8.0, plus uvicorn[standard], pydantic-settings,
aiosqlite, PyYAML, httpx, pyseccomp, pytest, pytest-asyncio, ruff, mypy.

---

# Phase B findings (building the runner)

Four defects found by building against the real container. All fixed and regression-tested.

## `RLIMIT_NPROC` is per-UID, not per-process-tree
Submissions failed to even `execve` at `max_processes` ≤ 24 with `EAGAIN`. The cause is not our
own process count: the kernel counts processes for the *uid* globally, and under Docker Desktop
uid 999 already carries a ~25-process baseline we do not control. Below ~32 it rejects
legitimate submissions outright. Raised the default to 64 and demoted it to a backstop —
**the process-group kill is the real containment.**

## `getpgid()` after reaping silently loses the tree
`_kill_tree` called `os.getpgid(proc.pid)`, but `communicate()` has already reaped the direct
child by then, so it raised `ProcessLookupError` and every grandchild was left running. Now the
pgid is captured at spawn time (the child calls `setsid()`, so pgid == pid).

## Killing only on timeout leaks processes
The group was torn down only in the `TimeoutExpired` path. A submission that forks children and
then exits *cleanly* orphaned them — measured 32 survivors from one fork bomb whose parent
returned on its own. The teardown now runs in a `finally`.

## Zombies count against the process quota
After the fix the children were dead but unreaped (`ps` state `Z`), because PID 1 in the
container was pytest/uvicorn, which does not reap. Zombies hold PID slots and *count against the
per-UID `RLIMIT_NPROC`*, so they would eventually deny service to legitimate submissions.
Fixed with `init: true` in compose (PID 1 becomes `docker-init`). Verified: zero processes in
any state after a forking submission.

## Read-only mounts and nested volumes
A named volume cannot be mounted *inside* a read-only bind mount — Docker cannot create the
mountpoint. Resolved by following the threat model instead of fighting it: only the backend
executes submissions, so only it gets read-only source, and it does not mount the frontend at
all. The Vite service mounts the frontend writable and never runs untrusted code.

Read-only source also means tool caches must be relocated: `cache_dir` for pytest and mypy,
`cache-dir` for ruff, all pointed at `/tmp`.

---

# Phase C findings (frontend, Monaco and the language client)

Six defects, all found only by running a real browser. Every one produced either
a blank page or a silently dead feature — none would have shown up in a unit test.

## `monaco-vscode-api` must exist exactly once
`@typefox/monaco-editor-react` 6.7.0 → `monaco-editor-wrapper` → `monaco-languageclient`
**9.6.0**, while a direct dependency pinned **9.5.0**. The two pull different
`@codingame/monaco-vscode-api` majors, and the page dies at boot with:

> Another version of monaco-vscode-api has already been loaded. Trying to load
> 1.98.2-…, 1.99.3-… is already loaded

Fix — pin the whole stack in `overrides` so npm hoists one copy:
```json
"overrides": {
  "monaco-editor": "npm:@codingame/monaco-vscode-editor-api@~16.1.1",
  "monaco-languageclient": "~9.6.0",
  "@codingame/monaco-vscode-api": "~16.1.1"
}
```
Verify with `find node_modules -path "*node_modules/monaco-languageclient/package.json" | wc -l` — it must print `1`.

## React `StrictMode` mounts two editors
StrictMode double-invokes effects; `monaco-vscode-api` cannot initialise twice in one
document. Symptom: the console fills with "Element already has context attribute", and
the DOM holds **two** `.monaco-editor` textareas — so Playwright (and any user click)
addresses the wrong one. Fix: do not wrap the app in `StrictMode`.

## Putting `value` in the wrapper config's `useMemo` deps
Rebuilt the config on every keystroke, tearing the editor down mid-edit.
The config must be seeded once; deps are `[language]` only.

## Echoing the editor's own text back into it
With the editor uncontrolled, a naive `useEffect` that pushes `value` into the model
fires on the component's *own* change, calling `setValue()` and resetting the cursor
to the start. The visible symptom is wonderfully clear: typed text comes out
**reversed** (`]0 ,0[ nruter …`) with the status bar stuck at `Ln 1, Col 1`. Fix: track
what the editor last emitted and only push genuinely external changes.

## The language server connected but produced nothing
The hardest failure to read, because *everything looked right*: the WebSocket opened,
`initialize` succeeded with full capabilities, `workspace/configuration` was answered.
But no `textDocument/didOpen` was ever sent, so no diagnostics ever came back.

Cause: the model's `languageId` was `plaintext`, so the client's `documentSelector`
never matched. Two contributing mistakes:
- `codeResources.modified` takes `{ text, uri, enforceLanguageId }`. There is no
  `fileExt` field — the invented one was silently ignored — and `uri` must be a real
  `file://` URI.
- `@codingame/monaco-vscode-python-default-extension` did **not** register the language
  in this setup. Registering explicitly with `monaco.languages.register({ id: 'python',
  extensions: ['.py'] })` does, and drops 54 packages.

Confirmed fixed in-browser: `languageId: "python"`, `didOpen` sent, one marker rendered.

## Build and memory
- `worker: { format: 'es' }` is **required**: rollup refuses IIFE workers for a
  code-splitting build, which is what monaco-vscode-api produces.
- Docker Desktop's VM defaults to ~6 GB. The build OOMs at Node's default heap *and* at
  `--max-old-space-size=8192` (which exceeds the VM). `3072` plus
  `rollupOptions.maxParallelFileOps: 2` fits. esbuild also gets SIGKILLed during dev
  dependency pre-bundling under memory pressure; the symptom is
  `The service is no longer running: write EPIPE` and a blank page, cured by restarting
  the Vite service.

## End-to-end test hazards
- The API key lives in server process memory, so whether the setup screen appears
  depends on what ran before — and it can be visible when checked and gone a tick later.
  Race-tolerant helpers, or `page.route` stubbing of `/api/setup`.
- Monaco's textarea sits under `.view-lines`, which intercepts pointer events: click
  `.view-lines`.
- Monaco auto-indents typed newlines and corrupts Python. Type single-line solutions
  (`def f(a, b): return ...`) instead.

---

# Phase D findings (packaging for use)

## Compose builds one image per service
`docker compose build dev` does **not** update the `web` service, even though both
declare `target: dev` — each service gets its own image tag. After changing the
Dockerfile, build both, or the running container silently keeps the old layers.
This surfaced as Playwright reporting a missing browser that was demonstrably
present in the image.

## Playwright must be baked into the image
It was originally installed ad hoc into a running container, so `make e2e` would not
have worked from a clean clone. It now lives in the Dockerfile's `dev` stage, pinned
so the bundled browser build matches the project's `@playwright/test`.

## dev and prod both wanted host port 8080
Running them side by side (useful: point the browser suite at the *production* image)
failed with "port is already allocated". The dev backend now publishes **8081**;
prod keeps 8080.

## The production image is verified, not assumed
`make e2e-prod` runs the full browser suite against the built prod container. It
caught nothing this time, but the dev server and the FastAPI-served static build are
genuinely different code paths — the SPA fallback route, asset paths and the absence
of the Vite proxy are only exercised in prod.

---

# Phase E findings (first user testing)

## The harness shared stdout with the candidate's `print()`
`print(nums)` in a submission returned **500**. The harness wrote its per-case
result records to stdout — the same stream the solution prints to — with no framing.
Three failure modes, worsening:

| Printed | Old behaviour |
|---|---|
| `debugging` (not JSON) | silently swallowed |
| `[1, 2]` (JSON array) | `AttributeError: 'list' object has no attribute 'get'` → 500 |
| `{"ok": true}` (JSON object) | **counted as a result**, shifting every later case and silently misgrading correct answers |

The third was the real defect; the crash merely made it visible. A comment in the
original code said "or stray print", so stray prints were anticipated — but only the
unparseable case was handled, and swallowing output was never right.

**Fix.** The harness grabs the real stdout, rebinds `sys.stdout` to a buffer, and
writes records only to the saved handle. Per-case output is captured, capped at 4 KB,
and returned as `stdout` on each record — so `print()` debugging now works and is
displayed in the results pane. Every record carries a `__freetcoder` marker and
`decode_results` accepts nothing else, so a submission reaching the real stdout by
another route (`sys.__stdout__`) still cannot forge a result.

The `import solution` line is deliberately **not** wrapped in try/except: a
`SyntaxError` must reach stderr for the adapter to classify it as a compile error.

`run` and `submit` now also convert an unexpected exception into an `internal_error`
verdict shown in the pane, so a future harness bug degrades instead of 500ing.

## Monaco tooltips rendered outside the viewport
Hover and signature-help widgets are children of the editor container, so in the
narrow right-hand pane they were clipped and ran off the right of the screen.
Fixed with `editorOptions: { fixedOverflowWidgets: true }`, which moves them into a
viewport-fixed overlay that repositions to stay visible.

## Test-writing lesson
Every existing test used clean `return`-only solutions. Printing — the most ordinary
debugging action there is — was never exercised, so a routine user action reached
production broken. New coverage lives in `backend/tests/test_harness.py` (one test per
row of the table above) and `frontend/e2e/problem.spec.ts`.

---

# Phase F findings (JavaScript and TypeScript)

The print fix did **not** extend to other languages: it lived inside the Python
harness string, and a naive JS harness (`console.log(JSON.stringify(rec))`)
reproduces the original bug exactly. The contract is now written down in
`docs/execution-protocol.md` and enforced by
`backend/tests/test_language_conformance.py`, which is parameterized over every
registered adapter.

## RLIMIT_AS is incompatible with JIT runtimes
Node died instantly under the 256 MB address-space cap:

> Fatal process out of memory: Failed to reserve virtual memory for CodeRange

V8 reserves *gigabytes of virtual address space* for its code range regardless of
actual usage, and `RLIMIT_AS` caps virtual, not resident, memory. `Limits` gained
`limit_address_space`; the Node adapters turn it off and bound the heap with
`--max-old-space-size` instead. Anything else JIT-compiled (a JVM, Go's runtime)
will need the same treatment.

## Python `!r` produces broken JavaScript
`{function_name!r}` emitted `'f'` — a valid Python literal, and a syntax error
inside a JS single-quoted string. JS templates interpolate with `json.dumps`.

## tsc needs ambient globals, and `--lib dom` is the wrong fix
Type-checking a bare `solution.ts` fails with "Cannot find name 'console'".
`--lib dom` would also make `document` and `window` type-check and then fail at
run time, and `@types/node` is not resolvable from a workspace with no
`node_modules`. The adapter writes a small `globals.d.ts` declaring exactly what a
submission may legitimately reach.

TypeScript deliberately compiles rather than using `--experimental-strip-types`:
stripping skips type checking entirely, which is the main reason to offer
TypeScript, and rejects `enum`. Because tsc emits `solution.js`, one JavaScript
harness serves both languages.

## The question cache key must include the offered languages
A question cached when only Python was offered has no JavaScript signature.
Replaying it for a multi-language format produced a language dropdown whose
scaffolds did not exist. `cache_key()` now includes the language set.

## Monaco: switching language must not remount
Remounting the editor to change language left it **blank** — monaco-vscode-api
cannot be initialised twice in one document (the same constraint that rules out
React `StrictMode`). One editor now holds a model per language, and every
language server the session may need is connected at mount; each client's
`documentSelector` picks up its own documents.

Two follow-on defects, both found only in a browser:

- **The wrapper creates the first model itself.** Registering that model is not
  the same as listening to it; without an explicit `onDidChangeContent`, edits
  never reached the store and the value effect then overwrote them.
- **A language change before `onLoad` was silently dropped**, because the swap
  effect returned early on a null editor ref and never re-ran. It now depends on
  an explicit `ready` flag.

## What you see must be what runs
The deepest bug here: the editor displayed the candidate's solution while the
backend executed the **scaffold**, reporting `Your output = null`. The model had
the text; the store had not caught up. Chasing the synchronisation is a losing
game, so Run and Submit now read the live editor buffer
(`store.currentSource()`), with the store as fallback. This is a correctness
property, not a test convenience — the code on screen is the code that runs.

Symptom to recognise: results that match a previous buffer, often with the caret
back at `Ln 1, Col 1`.

## End-to-end test hazards (additions)
- Monaco renders spaces as `U+00A0`, so substring assertions on editor text fail
  on strings that look identical. Normalise before comparing.
- Switching language is asynchronous; wait for the new scaffold before typing.
- `data-editor-ready` marks the point at which the change listener is attached.

---

# Phase G findings (editable cases, the library, relative timing)

## "No expected value" is not the same as "expects null"
A candidate-authored case with a blank expected field should run and show its
output, not be judged. That is indistinguishable from `expected: null` if you
look only at the value, so `CaseInput` carries an explicit `assert_expected`
flag, and `CaseOutcome` carries `judged`. Unjudged cases are excluded from
scoring outright, so exploratory cases can never influence a grade.

An unjudged case can still *fail by crashing* — that is worth reporting; it just
cannot be a wrong answer.

## The preset reached the config but not the prompt
`resolve()` applied a tier-2 preset's effects (question count, difficulty,
topics) but never told the model which preset was chosen, so "Blind 75 style" and
"Top interview 150" produced near-identical prompts. Presets now carry an
`intent` sentence that reaches the generator, and `preset_id` is part of the
cache key so siblings do not share cached questions.

## Timing must be a ratio, and the reference must be local
See `docs/execution-protocol.md` and `docs/library-service.md`. The short version:
store `submission / reference` measured back to back on the same machine, re-measure
the reference at submit time rather than trusting a number that travelled with the
question, and bucket by language. Observed ratios for the *same* algorithm varied
0.88–1.39 across five runs, which is a useful calibration of how much noise to
expect: this separates quadratic from linear, not constant factors.

## Tests must not inherit the compose environment
The dev container sets `FREETCODER_DB_PATH` and `FREETCODER_LIBRARY_URL`. A test
asserting "no library configured" passed locally and failed in-container until
`conftest.py` cleared both. Same class of bug as the earlier database one: tests
that inherit ambient configuration quietly assert something other than what they
appear to.

---

# Phase H findings (syntax highlighting, unresolved)

Three approaches tried, none delivering colour. Recorded so the next attempt
starts from the facts rather than repeating them.

## Monarch is not available in `extended` mode
`monaco-editor-wrapper/dist/vscode/services.js` decides by `$type`:

```js
if ($type === 'extended') { textmate + theme service overrides }
else                      { monarch service override }
```

We run `extended` (the language client needs it), so
`monaco.languages.setMonarchTokensProvider` is a **silent no-op** — it neither
throws nor colours. Hand-written Monarch grammars were written, verified to
compile, and discarded on this finding.

## The default-extension packages do not contribute their languages
Importing `@codingame/monaco-vscode-python-default-extension` and friends does
not register `python` as a language here: with our manual
`monaco.languages.register` calls removed, models resolved to **`plaintext`**,
which breaks the language client's `documentSelector` and stops diagnostics
entirely. Manual registration is therefore required for the LSP to work, and it
appears to shadow whatever grammar binding the extension would otherwise make —
every token renders as `mtk1`.

`optimizeDeps.exclude` does fix the earlier esbuild OOM, so the packages now
install and load without crashing Vite. They simply have no visible effect.

## Two ordering constraints, both found by a silently dead editor
- **Monaco's theme API cannot be touched before the wrapper starts.** Calling
  `defineTheme`/`setTheme` at app boot leaves monaco-vscode-api in a state where
  the editor never renders — no exception, no console error, just nothing.
- **Cosmetics must be applied after readiness is signalled.** With
  `registerGrammars()` and `monacoDidLoad()` running before `setReady(true)`, a
  throw in either stranded `data-editor-ready="false"` forever: a working
  editor that nothing would type into.

Both are the same lesson: theming and highlighting are decoration, and must
never sit on the path that makes the editor usable.

## Where to go next
1. Find how the TextMate service resolves a grammar to a language id, and
   register the grammar against our manually-registered language directly,
   rather than relying on the extension's contribution.
2. Or run a second wrapper configuration in `classic` mode purely to establish
   whether Monarch colours work there, then decide whether the language client
   can live without `extended`.

Not attempted: shipping the real `monaco-editor` package for its grammars. Two
copies of the Monaco API is the trap that produced a blank page in Phase C.
