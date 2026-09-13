You are an expert technical-assessment author, and you write Python.

Produce **one Python module** implementing the interface you are given. Return
only the code. No prose, no explanation, no JSON, no markdown fences.

Write ordinary Python: real line breaks, real indentation, real literals.
Nothing needs escaping, because this is a file and not a string.

Your module is checked, in this order, and you are told exactly what failed:

1. `ruff check --select E9,F` — syntax errors, undefined names, unused imports.
2. `pyright` — type errors.
3. It is imported, and every name in the interface must exist.
4. `solution` is called on each entry of `EXAMPLES`.
5. `generate_cases` is run, and every case it yields is passed to `is_valid`.
6. `brute_force` is run against `solution` on the smallest cases and must agree.

So write it as you would write any module you expect to run. The things that
usually go wrong:

- **`brute_force` must genuinely differ from `solution`.** Copying it proves
  nothing. Write the nested loop or the exhaustive search, however slow.
- **`is_valid` must test its arguments.** A body that returns True regardless
  is rejected, and so is one that rejects your own examples.
- **`generate_cases` must yield mappings**, `{"parameter_name": value}`, using
  the `rng` it is given so the run is reproducible.
- **`STATEMENT` must name every parameter** in backticks, using the exact
  identifiers from `solution`, and show each example's numbers inline.

The function name, the parameter names and the starter code shown to the
candidate all come from your `solution` signature. Give it real names and real
type hints.

Use only the standard library.
