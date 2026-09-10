## Check your own work before you answer

Before returning the JSON, verify it against this list. If any line fails, fix
it and check again. Return only the corrected object.

1. Does `statement_md` name every parameter, in backticks, using the exact
   identifier from the signature?
2. Does it show each worked example with its input, output and a reason?
3. Does it say what happens on empty or degenerate input?
4. Does `constraints_md` give a bound for every parameter?
5. Does `constraints` have one entry per parameter, agreeing with the prose?
6. Would a competent engineer who read only `statement_md` and `constraints_md`
   — not your reference solution, not the title — implement this correctly?
7. Does `reference_solution` return exactly the `expected` value of every entry
   in `visible_tests`? Trace at least one by hand.

Question 6 is the one that matters. If the answer depends on recognising the
problem rather than reading the words, the statement is not finished.
