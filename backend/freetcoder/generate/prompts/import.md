## Adapting a question the candidate supplied

The candidate has given you prose describing a problem: something they saw
elsewhere, half-remembered, or sketched themselves. Treat it as a **starting
point**, not a specification to copy.

Keep the central idea, and where they work, keep the framing and the examples —
this should feel like the question they had in mind. Beyond that you are free to
rewrite for clarity, correctness and testability.

Supplied prose is almost always under-specified, so **decide the things it
leaves open and say so in the statement**:

- how input is tokenised or normalised (does punctuation split words? does case
  matter?);
- how ties are broken;
- what happens on empty, single-element or otherwise degenerate input;
- the bounds on every input — a question with no stated limits cannot be
  reasoned about, let alone optimised for.

Author everything the text lacks: a reference solution for every target
language, a scaffold for each, the hidden-case generator, a brute force, the
constraints. The result is executed and validated exactly like any other
question, so it has to actually run.

Record every judgement call as a clarification — the question their text left
open, your answer, and a `probe` showing it. Being told "ties break toward the
earliest word" up front is the difference between an informed answer and a
baffling failure, and a probe makes it a checked fact rather than a promise:
yours is run against your own solution before the question is accepted.

If the text is too vague to make a well-defined problem at all, do not refuse:
choose the most natural reading, build the question, and make each thing you
had to invent one of those clarifications.
