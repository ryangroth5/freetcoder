# The test suites

Four suites, all run in-container. `make check` runs lint and the Python
suites; `make e2e` and `make e2e-prod` run the browser suites.

| suite | command | count | wall clock |
|---|---|---|---|
| backend | `make test` | 450 | ~3m30s |
| library | `make test` | 27 | <1s |
| browser, dev stack | `make e2e` | 45 | ~2m20s |
| browser, prod image | `make e2e-prod` | 45 | ~1m10s |

**They must run in the container.** Resource limits behave differently on
macOS, so a green run on the host proves nothing about the sandbox. The source
is mounted read-only, so anything that writes -- `ruff --fix`, a pytest cache --
has to run on the host or be disabled with `-p no:cacheprovider`.

The library suite borrows the backend's pytest config
(`-c backend/pyproject.toml`), which is where `asyncio_mode` is set. Run it any
other way and all 27 error out on async fixtures; run it inside the `library`
container and they error too, because that image has no test dependencies.

## Why `e2e-prod` exists, and what it caught

The dev suite talks to Vite on `http://localhost:5173`. The prod suite talks to
the built image on `http://prod:8080`. That hostname difference is the entire
point, and it is not hypothetical: it caught a bug that made the app unusable
for every user except the person running Docker.

`crypto.randomUUID` is defined **only in a secure context**. An origin is secure
on https or on localhost -- and `http://prod:8080` is neither. The session-start
handler called it unguarded, so it threw, so the Start button did nothing.
Since freetcoder ships as a container serving plain HTTP on a bound port,
anyone reaching it by hostname or LAN IP got a dead button.

The dev suite could not have caught this, structurally. Playwright's default
`baseURL` is localhost, which is always a secure context.

Two lessons worth keeping:

- **Run the prod suite before believing a release works.** It is the only thing
  exercising a non-localhost origin and the built bundle rather than Vite.
- **A suite failing wholesale is a signal, not noise.** Those 44 failures read
  for a while as "prod e2e is flaky". They were reporting a real defect, and
  the flakiness reading delayed finding it.

`smoke.spec.ts` now deletes `crypto.randomUUID` to simulate an insecure origin,
so the dev suite catches a regression too. Verify a test like that actually
bites -- put the bug back and watch it fail -- because a test that passes for
the wrong reason is worse than no test.

## Where the backend suite spends its time

Three groups, wanting different treatment. Profile with `--durations=25` before
changing anything.

**Waiting out a kill (~10s total, was ~42s).** Tests asserting that runaway code
is killed and its process tree dies with it. The production wall clock governs
how long a candidate waits, which is not what they assert, so the `quick_kill`
fixture in `conftest.py` drops the limit to two seconds. It patches the
module-level `Limits` in `gate`, `tools` and `service`, so production code needs
no test-shaped seam.

Applied per test, deliberately. It must never become autouse.

**Measuring runtime (~83s).** The reference-scaling checks, which run work at
two input sizes `TIMING_REPEATS` times and fit an exponent. These are slow
because measurement takes time, and a shorter limit does nothing for them.
Cutting repeats or input sizes would make the measurement noisier -- and
robustness to noise is exactly the property they exist to prove. The 28-second
one re-runs the check to show it does not flap. Leave them alone; they are the
price of catching a quadratic reference that claims to be linear.

**The long tail (~96s across ~425 tests).** No individual offender. Wants
parallelism.

## Future: parallel workers

Measured, works, not yet adopted. `pytest -n 6` (pytest-xdist, not currently a
dependency) takes the backend suite from 241s to **92s**, with one failure:
`test_forked_children_do_not_survive`.

That failure is the documented `RLIMIT_NPROC` hazard -- the quota is per-UID
**across the whole kernel**, so six workers spawning sandboxes as the same
`runner` uid share one process budget. See the note in `runner/types.py`.

The subtler risk is the timing tests. They passed under contention in the trial,
but `EXPONENT_TOLERANCE` is fitted from wall-clock measurements, so competing
workers push toward false `REFERENCE_TOO_SLOW` rejections. Passing once is not
evidence of stability, and flakiness there lands in the check that decides
whether a question is good enough to serve.

So adopting it means pinning the sandbox and timing tests to a serial group
(`--dist loadgroup` with `xdist_group` marks) rather than adding a flag. Worth
doing when the suite gets slow enough to be worth the risk; at ~3m30s it is not
yet, and a flaky trust anchor costs more than three minutes.

## Other things worth knowing

- `make e2e` needs `FREETCODER_FAKE_LLM=1` in the environment when the stack
  starts. Without it the backend has no client, and exactly the tests needing
  one fail -- generation progress and the tutor -- while the rest pass off the
  cache. That partial failure looks like a code regression and is not.
- `test_no_repair_target_can_touch_validation` fails whenever a repair target is
  added. That is the feature: every new target must be consciously admitted
  rather than silently gaining reach into the gate.
- Playwright runs with `workers: 1`. Submissions execute real code in a shared
  sandbox on a memory-constrained VM, so this has the same hazards as the
  backend parallelism above.
