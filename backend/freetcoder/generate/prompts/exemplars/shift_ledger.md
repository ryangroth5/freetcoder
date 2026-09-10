### Exemplar — shape only, never content

Third example, same rule: the subject is one you will never be asked for.

**Title:** Unmatched Shift Swaps

**statement_md:**

> A rota logs every swap as a pair `[from, to]`, meaning one nurse gave a shift
> to another. Given `swaps`, return the identifiers of the nurses who gave away
> more shifts than they took on, sorted ascending.
>
> `swaps` holds the pairs in the order they were logged; order does not affect
> the answer. Return a list of integers, ascending, possibly empty.
>
> A nurse who gave and took equally does not appear. Repeated identical swaps
> count each time. A nurse may appear in `swaps` only as a recipient, in which
> case they are never in the answer.
>
> **Example 1** — `swaps = [[1, 2], [2, 3], [1, 3]]` returns `[1]`. Nurse 1 gave
> twice and took nothing; nurse 2 gave once and took once; nurse 3 only took.
>
> **Example 2** — `swaps = [[4, 5], [5, 4]]` returns `[]`. Both are square.

**constraints_md:**

> - `0 <= swaps.length <= 10^4`
> - `swaps[i]` has exactly 2 elements
> - `1 <= swaps[i][j] <= 10^6`

Note the third paragraph: it answers the three questions a reader would
otherwise have to ask.
