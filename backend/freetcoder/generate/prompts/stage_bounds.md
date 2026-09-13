You are given a problem statement and its reference solution. Write the
**constraints**, as delimited plain text. Not JSON.

Use these markers exactly, each on its own line:

```
=== BOUNDS ===
- `1 <= levels.length <= 100000`
- `-500 <= levels[i] <= 500`
- `0 <= drift <= 1000`

=== BOUND ===
name: levels
min_length: 1
max_length: 100000
element_min: -500
element_max: 500

=== BOUND ===
name: drift
min: 0
max: 1000
```

**=== BOUNDS ===** is the Markdown bullet list the candidate reads. It needs a
line for every parameter: a size range for a list or string, a value range for
a number, and the element range where the values matter.

**=== BOUND ===** repeats each of those as data, one block per parameter. The
`name:` must match the parameter exactly. Use only these keys, one per line,
and only the ones that apply:

- `min` / `max` — for a number's value
- `min_length` / `max_length` — for the size of a list or string
- `element_min` / `element_max` — for the values inside a list

**Every parameter needs a `=== BOUND ===` block.** A parameter with no bound is
rejected: the candidate cannot choose an algorithm without knowing the size, and
the generated test cases are checked against these numbers.

Choose bounds the reference can meet in a couple of seconds, and large enough
that an inefficient approach would struggle. A bound is also a promise about
what cannot occur: if the solution assumes a non-empty list, write `1 <=`, not
`0 <=`.
