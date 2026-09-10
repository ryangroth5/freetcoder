# The question library

> Maintainer documentation. If you just want to run freetcoder and
> practise, read [using-freetcoder.md](using-freetcoder.md) instead.

A **separate service** holding questions people want to keep, with its own
FastAPI app, its own SQLite database, its own port (8090) and its own volume.

It is separate on purpose. Accounts, voting and authentication belong here, and
keeping the boundary real from the start means adding them will not touch the
practice app.

```
browser ──▶ freetcoder app ──HTTP──▶ library service ──▶ its own SQLite
                 │
                 └── practice works completely without it
```

## The rule that shapes everything

**The library must never be able to break local practice.**

An external dependency that can take down offline practice would be a bad trade
for a tool whose whole point is running on your own machine. So every call in
`backend/freetcoder/library.py` is *total*: failures are logged and returned as
an absent result, never raised into a request handler.

| Library state | What the candidate sees |
|---|---|
| Not configured | Browsing says so; everything else is unaffected |
| Unreachable | Browsing reads as empty; publishing reports "unreachable"; **sessions, Run and Submit are untouched** |
| Returns an error | Same as unreachable |
| Returns a question this version cannot parse | "Saved in a format this version cannot read" |

`backend/tests/test_api.py::TestLibraryResilience` points the client at a dead
port and asserts exactly this. Those tests matter more than the happy path.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/questions` | Publish a gated question; returns its id |
| `GET` | `/questions` | Search by style, difficulty, language, topic; paginated |
| `GET` | `/questions/{id}` | Fetch one, with its stats |
| `POST` | `/questions/{id}/timings` | Record a submission **and** return its standing |
| `GET` | `/questions/{id}/percentile` | Standing for a given language and ratio |
| `GET` | `/health` | Liveness |

Recording and ranking share one call because the caller always wants both, and a
second round trip would only widen the window for inconsistency.

**Questions are stored verbatim** and never interpreted. The library does not
need to understand the question format to serve it back, so that format can
evolve without a library migration.

## Timing: ratios, never milliseconds

The interesting number is LeetCode's "faster than 90% of submissions". Wall-clock
milliseconds cannot deliver it — a submission timed on a busy laptop is not
comparable to one timed on a quiet server, and this container may be sharing a
machine with a browser and a compiler.

**What is stored is a ratio:**

```
ratio = submission time / reference solution time
```

both measured **on the same machine, moments apart**, over the same cases. CPU
speed, container limits and ambient load appear in both terms and cancel. Below
1.0 means faster than the reference.

Three details make it trustworthy:

- **The reference is re-measured at submit time**, not read from the stored
  `reference_ms`. A question pulled from the library carries a number recorded on
  a stranger's hardware; comparing against it would rank machines, not code.
- **Ratios are bucketed by language.** Python and JavaScript have different
  constant factors, so a shared pool would rank the language rather than the
  solution.
- **Only correct solutions are ranked.** Timing a wrong answer would reward
  failing fast.

### Percentiles are withheld below five samples

`library/percentile.py` returns `None` under `MIN_SAMPLES`. A percentile drawn
from two data points is worse than showing nothing: it looks authoritative and
is noise. Ties count as half, so identical submissions do not all land on 0 or
all on 100.

Expect run-to-run variation of roughly ±30% on very fast questions, where total
runtime is small enough that scheduling noise dominates. The ratio separates
quadratic from linear reliably; it does not resolve constant factors.

## Configuration

| Variable | Where | Meaning |
|---|---|---|
| `FREETCODER_LIBRARY_URL` | app | Library base URL. Empty disables the feature |
| `LIBRARY_DB_PATH` | library | SQLite path; empty means in-memory |

```bash
docker compose up library        # http://localhost:8090
```

## What centralizing it will involve

The schema already carries `author`, `votes` and `created_at`, unused. They are
present so that adding accounts and voting is a change of behaviour rather than a
migration of shared data.

Still to do when this stops being a single-user service:

- **Authentication** on `POST /questions` and timings. `author` is currently
  whatever the client claims and is trusted for nothing.
- **Rate limiting.** Every client call is already timed and logged in
  `library.py::_call`, which is where a budget or throttle attaches.
- **Moderation.** Published questions are LLM-generated and unreviewed; a
  centralized library needs a way to remove bad ones.
- **Abuse-resistant timings.** A client can currently post any ratio it likes.
  Server-side execution, or signed results, would be needed before a leaderboard
  meant anything.
