You are an expert technical-assessment author. Write one complete coding
question as **delimited plain text**, not JSON.

Use these markers exactly, each on its own line, in this order:

```
=== TITLE ===
=== STATEMENT ===
=== FUNCTION ===
=== SCAFFOLD ===
=== SOLUTION ===
=== CASE ===        (repeat, two or three times)
```

Write code between the markers exactly as it would appear in a file, with real
line breaks and real indentation. Do not escape anything, do not put it on one
line, and do not wrap it in backticks. The markers are the delimiter.

**=== TITLE ===** one line, a name for the problem.

**=== STATEMENT ===** the Markdown the candidate reads, and the only thing they
read. State the task in one sentence first. Name and describe every parameter
in backticks, using the exact identifiers from the signature. Say what to
return and in what order. Show each worked example inline with its input,
output and a reason. Settle the degenerate cases in words: empty input, no
valid answer, ties, duplicates, negatives.

**=== FUNCTION ===** one line: `name(param1, param2)`.

**=== SCAFFOLD ===** the starter code the candidate sees -- the signature with
an empty body. No hints.

**=== SOLUTION ===** a correct implementation. It is executed against your own
cases below, and any disagreement sends this straight back to you, so trace at
least one by hand.

**=== CASE ===** one block per worked example, exactly:

```
=== CASE ===
args: {"levels": [4, 6, 5, 9], "drift": 2}
expected: 3
why: the first three readings span 2, which is within tolerance
```

`args` and `expected` are single-line JSON. `args` maps each parameter name to
its value. `expected` is exactly what your solution returns.

Language conventions: **python** a module defining the function; **javascript**
CommonJS ending `module.exports = { name }`; **typescript** `export function`
with real types.

Constraints and the hidden tests are not asked for here. They come next,
derived from what you write, so that they can agree with it.
