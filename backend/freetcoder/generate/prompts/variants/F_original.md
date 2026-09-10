## Author it; do not recall it

Write a problem that does not already exist. Do not reproduce a published
question from LeetCode, HackerRank, Codeforces, Project Euler, *Cracking the
Coding Interview* or any interview-preparation set, whether from memory or by
paraphrase.

This is checked. A question is rejected when its worked examples match the
canonical examples of a known problem — reusing `nums = [2,7,11,15], target = 9`
or `nums = [4,5,0,-2,-3,1], k = 5` gives it away immediately, and reproducing a
famous problem with its numbers changed is the same failure.

Two things make a question yours rather than remembered:

- **Invent the scenario.** Give it a concrete setting that a published problem
  does not have — the reading of a tide gauge, a picking route in a warehouse, a
  ledger of shift swaps. The underlying technique may be common; the framing
  must not be borrowed.
- **Choose your own examples.** Build them from your scenario, then verify by
  hand that your reference returns exactly those values.

A familiar *technique* is fine and expected — prefix sums, two pointers, a hash
map. A familiar *problem* is not.
