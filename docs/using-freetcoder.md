# Using freetcoder

A guide for practising with freetcoder. If you want to modify it instead, start
with the [README](../README.md) and the other files in this folder — they are
written for maintainers.

---

## First run

```bash
git clone <this repo> && cd freetcoder
cp .env.example .env        # put your API key in it
make dev
```

Open **http://localhost:5173**.

The key goes in `.env` and nowhere else. Any OpenAI-compatible endpoint works —
OpenRouter, Ollama, vLLM, LM Studio. It is never written to the database and
never returned by the API.

**No key yet?** You can still look around. Offline mode serves a recorded
question so nothing calls a provider:

```bash
FREETCODER_FAKE_LLM=1 make dev
```

You will get the same question every time. That is the point of it.

---

## Is it actually talking to a model?

Next to **Settings**, on both the start screen and the problem screen, there is
a coloured dot:

| | Meaning | What to do |
|---|---|---|
| 🟢 **LLM live** | Real questions, generated now | Nothing |
| 🟠 **offline** | Recorded questions from a fixture | See below |
| ⚪ **no key** | Nothing configured | Add a key |

Read this first if questions look repetitive. **A valid key and offline mode can
both be true at once** — `FREETCODER_FAKE_LLM=1` wins over your key — and until
the dot existed there was no way to tell. If you see amber, either unset that
variable and restart, or type a key into Settings, which overrides it for the
running server.

Hover the dot for the specific reason.

---

## Choosing what to practise

Three ways to start:

- **Generate a new question** — the usual path.
- **From the library** — solve something you or a teammate saved earlier, with
  no generation cost or wait.
- **Bring your own** — either *describe* a question ("something about counting
  dogs") or *paste* one you saw elsewhere. It gets tightened into a well-defined
  problem, given a reference solution and hidden tests, and proven solvable like
  anything else. Anything it had to invent is recorded in a note you can read.

Then three tiers of buttons, each narrowing the last:

1. **Assessment style** — LeetCode, CodeSignal GCA, Codility, Coderbyte. This
   sets the whole shape of the session: how many questions, whether there is a
   timer, how you are scored, and whether hints are available.
2. **Preset** — e.g. *Easy warm-up*, *Blind 75 style*, *Medium grind*.
3. **Concentration** *(optional)* — a topic such as *dynamic programming* or
   *two pointers*.

Each tier ends in an **Other…** free-text box. That text *narrows* the tier
above it; it never rewrites the format. Asking a GCA for "one easy untimed
question" still gets you four questions in seventy minutes, because a GCA whose
structure you choose is not simulating a GCA.

A summary card shows what you are about to get before you press **Start**.

### While it generates

Generation takes tens of seconds, because every question is executed and checked
before you see it. A panel names each step as it happens.

**Cancel** stops the work, but it cannot interrupt a request already in flight to
the model — the panel says so rather than appearing to hang.

---

## The problem screen

Left is the question: statement, constraints, worked examples, and
**clarifications** — explicit answers to the things statements usually leave
vague ("does capitalisation matter?"). Those answers are verified against the
reference solution, so they cannot contradict the grader.

Right is the editor and, below it, your test cases.

### The editor

Monaco — the editor from VS Code — with syntax colouring, completion, hover
tooltips, signature help and live diagnostics from a real language server
(`pyright` for Python, `typescript-language-server` for JavaScript and
TypeScript).

- **Language** — switch with the dropdown. Each language keeps its own buffer,
  so switching never discards what you wrote.
- **⟲ Reset** — throw away your changes for the current language and go back to
  the starting scaffold.

If a language server dies, editing keeps working and only the intelligence
degrades.

---

## Test cases

The **Testcase** tab is an editor, not a display. Change the given examples, add
cases, duplicate or delete them.

- Leave **expected** blank to just see what your code returns.
- Fill it in to get pass/fail.
- **Compute expected** asks the intended solution what those arguments produce,
  so you can explore behaviour without guessing.

**▶ Run** uses your cases and never affects your score. **Submit** ignores them
and uses the question's own examples plus its hidden cases.

---

## The tutor

The **Tutor** tab is a chat about the question you are on. Enter sends;
Shift+Enter starts a new line.

It can see your current code, your run history, the failing case, compiler
errors, and a *characterisation* of the hidden tests — how many, what sizes and
shapes they cover — so it can help you think about what valid input looks like.

It can also ask the reference solution what a given input produces, and tell you
the answer. So "what does it do with an empty string?" gets a definite answer.

It can never see the reference solution's source, and never the hidden cases
verbatim. Those two together would turn it into a lookup table.

**It is disabled during CodeSignal GCA and Codility sessions** until you submit
or skip. Those formats offer no help, and a timed assessment that ships an AI
assistant is not simulating anything.

---

## Submitting

**Submit** runs the question's examples plus its hidden cases — which are
written to break plausible-but-wrong solutions.

You get a verdict (*Accepted*, *Wrong Answer*, *Time Limit Exceeded*, *Runtime
Error*, *Compile Error*), the first failing case, and a speed figure.

The speed figure is a **ratio**: your runtime divided by the reference
solution's, both measured on your machine moments apart. Below 1.00 beats the
reference. It is deliberately not a wall-clock time, so your hardware and
whatever else your machine is doing cancel out. Once the library has enough
submissions for a question you also get a percentile.

After you submit or skip, the **Solution** tab unlocks and you can read the
reference.

### Timed formats

GCA and Codility sessions run a countdown. Under five minutes it turns red, and
**at zero the attempt submits itself**. **Skip** moves on and unlocks the
solution.

---

## Saving and reusing questions

**☆ Save** sends a question you liked to the question library — a separate
service, so it can be shared. **From the library** on the start screen solves
saved questions without generating anything, and your submissions are ranked
against everyone else's.

The library is entirely optional. Unconfigured or unreachable, everything except
saving and browsing works exactly as before; it can never break local practice.

---

## Settings

Reach it from the ⚙ on the start screen or in the problem header. Two sections,
because they store in different places:

**This browser** — theme (light, dark, or follow the system) and default
language. Kept in your browser, so two people sharing a container do not
overwrite each other.

**This server** — the endpoint, model, and the generation options, shared by
everyone using this container. Each field shows whether it came from the
environment or was saved here, and **Reset** returns it to the environment
value.

The **API key** field applies a key to the running server without storing it
anywhere. Useful for trying a different provider, or for getting out of offline
mode. It is lost when the server restarts — `.env` is the durable place.

If no database volume is attached, the page says so: server settings then last
only until the process restarts. Saving still works.

---

## When something looks off

**The dot is amber and questions repeat.** Offline mode is on. Unset
`FREETCODER_FAKE_LLM` and restart the container, or enter a key in Settings.

**Generation keeps failing.** Small local models often cannot produce a question
that survives validation. Check the acceptance rate before blaming the app:

```bash
docker compose run --rm dev python -m freetcoder.generate --style leetcode -n 10
```

**Editing works but there is no completion.** A language server has died. Your
code still runs and submits normally; restart the container to get the
intelligence back.

**The library is unreachable.** A notice says so, and everything except saving
and browsing continues to work.

**A question seems ambiguous.** Read the clarifications under the statement
first — they are checked against the grader. If it is still unclear, ask the
tutor; it can query the reference for a definite answer.
