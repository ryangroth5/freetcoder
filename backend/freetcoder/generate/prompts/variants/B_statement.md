## The statement is the product

The candidate reads `statement_md` and nothing else. A reference solution they
never see cannot explain the task to them. Write it so someone with no access to
your other artifacts could implement the function correctly on the first try.

It must:

- **State the task in a sentence**, before any detail. Not the title again.
- **Name and describe every parameter** using the exact identifier from the
  signature, in backticks. A parameter the prose never mentions is a parameter
  the candidate has to guess at.
- **Say what to return**, including the type and the ordering if order matters.
- **Show the worked examples inline**, with their input, their output, and one
  line of why. The `visible_tests` data is not a substitute: the candidate reads
  prose.
- **Settle the degenerate cases** in words: empty input, no valid answer, ties,
  duplicates, negatives. If a case cannot occur, say so in the constraints
  instead.

`constraints_md` must give a bound for **every** parameter — a size range for a
collection or string, a value range for a number, and the element range where it
matters. A question whose bounds are missing cannot be reasoned about: the
candidate cannot choose an algorithm, and complexity has no meaning.

`constraints` repeats those bounds as data, one entry per parameter. Both are
checked. A parameter with no entry is rejected.
