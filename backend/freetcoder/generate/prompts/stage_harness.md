You are given a problem statement, its reference solution and its constraints.
Write the **hidden test machinery**.

Return a single JSON object matching the schema. No prose outside it.

`hidden_generator_py` is a Python script that prints one JSON object per line,
each `{"args": {...}}`, using only the standard library. Seed any randomness so
it is reproducible. Every case must obey the stated constraints — a case outside
them grades the candidate on input the question promised could not happen.

Cover the cases a plausible-but-wrong solution would get away with: the smallest
legal input, the largest, all-equal values, ties, duplicates, negatives if the
bounds allow them, and the shape that defeats a greedy or off-by-one attempt.
Do not print expected values; they are computed by running the reference.

`brute_force_py` is an obviously-correct, slow implementation of the same
function, used to cross-check the reference on small inputs. Write the naive
version — the nested loop, the exhaustive search — not a second clever one. If
the problem has no simpler formulation, return null.
