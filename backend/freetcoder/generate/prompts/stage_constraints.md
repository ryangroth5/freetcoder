You are given a problem statement and its reference solution. Write the
**constraints**.

Return a single JSON object matching the schema. No prose outside it.

`constraints_md` is a Markdown bullet list the candidate reads. It needs a bound
for **every** parameter:

- a size range for a list or string (`1 <= levels.length <= 10^5`)
- a value range for a number (`0 <= drift <= 1000`)
- an element range where the values matter (`-500 <= levels[i] <= 500`)

`constraints` is the same information as data, one entry per parameter, with the
parameter's exact name. The two must agree — they are one promise to the
candidate, written twice. Every generated test case is checked against this
data, so a bound you invent loosely will reject your own question.

Choose bounds the reference solution can actually meet within a couple of
seconds, and large enough that an inefficient approach would struggle. A bound
is also a promise about what cannot occur: if your solution assumes a non-empty
list, say `1 <=`, not `0 <=`.
