# Contributing

## Before a pull request

```bash
make check     # ruff, mypy, pytest, tsc, vitest — everything CI would run
make e2e       # Playwright against the dev stack, if you touched the UI
```

## Two rules that are not style preferences

**Backend tests must run in a container.** `make test` does this for you. On a
macOS host there is no `runner` user and no libseccomp, so the containment
tests — the ones asserting a submission cannot reach the network, fork
endlessly or write outside its workspace — would pass without testing anything.
A green run on the host is worse than no run.

**The dev source mount is read-only, deliberately.** A submission once escaped
through a writable mount and wrote into the host repository. macOS bind mounts
ignore `chmod`, so read-only is the only thing that stops it. Please do not
"fix" it.

## Read first, if you are touching these

- **The Dockerfile, the runner, or anything Monaco** —
  [docs/container-discovery.md](docs/container-discovery.md) records the
  failures behind decisions that look arbitrary: why the Monaco stack is pinned
  in `overrides`, why there is no React `StrictMode`, why `RLIMIT_NPROC` is only
  a backstop, why workers must be ES modules.
- **Question generation** —
  [docs/question-generation.md](docs/question-generation.md) for the mechanism,
  [docs/findings.md](docs/findings.md) for what was measured and why the design
  is what it is.
- **Anything that executes code** —
  [docs/execution-protocol.md](docs/execution-protocol.md).

## The one thing that must not change

`validate_question` is the trust anchor for the entire product. A question's own
material — statement, solutions, generator, brute force, constraints — is fair
game for the repair loop. **Validation logic is not.** A loop that could weaken
its own validator could make anything pass, and the guarantee that questions are
provably solvable would be worthless.
`backend/tests/test_repair.py::TestTheGateIsNotRepairable` asserts it.

## Tests

New behaviour wants a test that fails without it. Two habits this codebase has
learned the hard way:

- **Make sure the test can fail.** Several bugs here were checks that could not:
  a brute force compared against itself, an empty list satisfying "every
  parameter is covered" vacuously. If you cannot describe the input that breaks
  your assertion, it is not asserting anything.
- **Test the behaviour, not the fixture.** A browser test that pins
  `module.exports` because one recorded fixture happens to contain it is testing
  the fixture. Assert what the user would notice.
