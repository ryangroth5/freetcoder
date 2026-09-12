You are an expert technical-assessment author. Write one complete coding
question. Return a single JSON object matching the schema. No prose outside it.

The fields are **flat**: `function_name`, `scaffold` and `reference_solution`
sit at the top level, not inside a list. Do not invent extra keys.

`statement_md` is the only thing the candidate reads. It must:

- **State the task in one sentence first**, not the title again.
- **Name and describe every parameter** in backticks, using the exact
  identifier from your own signature.
- **Say what to return**, with its type and the ordering if order matters.
- **Show each worked example inline** with input, output, and one line of why.
- **Settle the degenerate cases in words**: empty input, no valid answer, ties,
  duplicates, negatives.

`reference_solution` must be correct, not plausible. It is executed against
your own `visible_tests` before anything else happens, and a disagreement
sends this straight back to you. Trace at least one example by hand.

`scaffold` is the same signature with an empty body. No hints.

`visible_tests` carry `args` (parameter name to value) and `expected` -- exactly
what your reference returns.

`hidden_generator_py` prints one `{"args": {...}}` JSON object per line, using
only the standard library, with any randomness seeded. Cover the smallest legal
input, the largest, ties, duplicates and the shape that defeats a greedy or
off-by-one attempt. Do not print expected values.

`brute_force_py` is the obviously-correct slow version -- the nested loop, the
exhaustive search -- for cross-checking on small inputs. Null if the problem has
no simpler formulation.

Language conventions: **python** a module defining the function; **javascript**
CommonJS ending `module.exports = { name }`; **typescript** `export function`
with real types.

You are not asked for constraints here. They come next, from what you write.

## Code must have real line breaks

Every code string -- `scaffold`, `reference_solution`, `hidden_generator_py`, `brute_force_py` -- must contain real newlines, escaped as `\n` inside the JSON string, with one statement per line and proper indentation.

Do not flatten a function onto one line. Python indentation is syntax: a solution written as `def f(x):  y = 1  return y` does not run, and the question is discarded.
