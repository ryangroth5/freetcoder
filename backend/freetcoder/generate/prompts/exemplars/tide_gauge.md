### Exemplar — shape only, never content

The question below is an example of the **standard of writing** expected. Its
subject is deliberately one you will never be asked for. Do not reuse its
scenario, its parameter names, its numbers or its phrasing. Copy only its
thoroughness.

**Title:** Longest Steady Stretch

**statement_md:**

> A tide gauge writes a reading every minute. Given the readings `levels` and a
> tolerance `drift`, return the length of the longest run of consecutive
> readings whose highest and lowest values differ by no more than `drift`.
>
> `levels` holds the readings in the order they were taken. `drift` is the
> largest spread a run may have and still count as steady. Return the number of
> readings in the longest such run, as an integer.
>
> A single reading is always a steady run, so the answer is at least 1 when
> `levels` is non-empty. When `levels` is empty, return 0. Where two runs are
> equally long, the answer is the same either way — only the length is asked
> for.
>
> **Example 1** — `levels = [4, 6, 5, 9], drift = 2` returns `3`. The first
> three readings span 4 to 6, a spread of 2, which is within tolerance; adding
> the 9 would make the spread 5.
>
> **Example 2** — `levels = [7], drift = 0` returns `1`. One reading spans
> nothing, so it is steady at any tolerance.

**constraints_md:**

> - `0 <= levels.length <= 10^5`
> - `-500 <= levels[i] <= 500`
> - `0 <= drift <= 1000`

Note what it does: states the task first, names `levels` and `drift` in
backticks and says what each means, gives the return type, settles the empty
case and the tie explicitly, shows both examples with a reason, and bounds every
parameter.
