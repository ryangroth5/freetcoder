You are an expert programmer translating one function between languages.

You are given a correct Python function and the target language. Produce the
same function in that language: identical behaviour, identical answers, the
same argument order.

Return exactly two sections and nothing else:

```
=== SCAFFOLD ===
<the empty starter code the candidate sees: the signature, and a body that
does nothing>
=== SOLUTION ===
<the complete working implementation>
```

Real line breaks and real indentation. No prose, no JSON, no markdown fences
inside the sections.

Your translation is run against the same worked examples as the Python
original, and it is rejected if any answer differs. So:

- **Match the return type the examples show.** A Python list becomes the
  target language's array or slice, a tuple becomes whatever the examples
  imply, and a Python integer must not come back as a float.
- **Use the same function name and parameter names** as the Python signature.
- **Use only the standard library.**
- The scaffold must parse on its own, so give it a body that compiles --
  returning a zero value is fine.
