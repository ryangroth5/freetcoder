You are given a problem statement. Write the **reference solution** for it, and
the worked examples it produces.

Return a single JSON object matching the schema. No prose outside it.

- `reference_solution` defines the named function and returns the answer. It
  must be correct, not merely plausible: it is executed against every example
  you give, and any disagreement discards the whole question.
- `scaffold` is the starter code the candidate sees: the same signature, with an
  empty body. No hints, no partial implementation.
- `visible_tests` are the examples from the statement, as data. `args` maps each
  parameter name to its value; `expected` is **exactly what your reference
  returns** for those arguments. Trace each one by hand before writing it down.

Language conventions:

- **python** — a module defining the function at top level.
- **javascript** — a CommonJS module ending in `module.exports = { name }`.
- **typescript** — `export function name(...)` with real parameter and return
  types, compiled with `tsc`, so type errors are failures.

If the statement is ambiguous about a case your solution has to handle, pick the
reading the examples support and stay consistent with it.

## Code must have real line breaks

Every code string -- `scaffold`, `reference_solution`, `hidden_generator_py`, `brute_force_py` -- must contain real newlines, escaped as `\n` inside the JSON string, with one statement per line and proper indentation.

Do not flatten a function onto one line. Python indentation is syntax: a solution written as `def f(x):  y = 1  return y` does not run, and the question is discarded.
