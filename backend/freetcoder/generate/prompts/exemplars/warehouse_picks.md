### Exemplar — shape only, never content

As before: the subject is deliberately one you will never be asked for. Reuse
nothing from it but the standard of writing.

**Title:** Splitting the Pick List

**statement_md:**

> A picker walks a single aisle collecting items. Given `weights`, the weight of
> each item in the order they appear on the list, and `capacity`, the most a
> trolley can carry, return the smallest number of trolleys needed if items must
> be collected in list order and a trolley is filled before the next is started.
>
> `weights` is the list in walking order and may not be reordered. `capacity` is
> the weight limit for one trolley. Return the count of trolleys as an integer.
>
> Every item fits on an empty trolley, so a solution always exists. An empty
> list needs no trolleys, so return 0.
>
> **Example 1** — `weights = [3, 1, 4, 2], capacity = 5` returns `3`. The first
> trolley takes 3 and 1; the second takes 4; the third takes 2.
>
> **Example 2** — `weights = [5, 5], capacity = 5` returns `2`. Each item fills
> a trolley exactly.

**constraints_md:**

> - `0 <= weights.length <= 10^4`
> - `1 <= weights[i] <= capacity`
> - `1 <= capacity <= 10^6`

The bound `weights[i] <= capacity` is what makes "a solution always exists" true
rather than merely hoped for. State that kind of thing.
