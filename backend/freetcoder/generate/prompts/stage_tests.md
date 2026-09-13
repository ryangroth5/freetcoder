You are given a problem statement, its reference solution, and the bounds its
inputs must obey. Write the **hidden test machinery**, as delimited plain text.
Not JSON.

Use these markers exactly, each on its own line:

```
=== GENERATOR ===
import json, random
random.seed(7)
...

=== BRUTE ===
def solve(...):
    ...
```

Write code exactly as it would appear in a file, with real line breaks and real
indentation. Do not escape anything and do not wrap it in backticks.

**=== GENERATOR ===** a Python script printing one `{"args": {...}}` JSON
object per line, standard library only, with any randomness seeded so it is
reproducible.

**Every case must obey the bounds you were given.** They are checked, and a
single case outside them discards the question — grading someone on input the
problem promised could not occur is worse than having no test at all. Read the
numbers off the bounds above rather than inventing your own.

Print at least the number of cases asked for. Cover the smallest legal input,
the largest the bounds allow, all-equal values, ties, duplicates, negatives if
the bounds permit them, and the shape that defeats a greedy or off-by-one
attempt. Do not print expected values: they come from running the reference.

**=== BRUTE ===** the obviously-correct slow version of the same function — the
nested loop, the exhaustive search — for cross-checking on small inputs. Not a
second clever one. Write `none` if the problem has no simpler formulation.
