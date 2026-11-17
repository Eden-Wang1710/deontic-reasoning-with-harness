---
name: grounded_reasoning
label: Grounded reasoning
description: Checks that what the answer file says the rules say is what they say, that its arithmetic reproduces, and that its final answer line follows from its reasoning.
table: required
quote: optional
max_steps: 2
---

# Check: grounded reasoning

**Property: what the answer file says the rules say is what the rule document says, its computations reproduce, and its final answer follows from its reasoning.**

This check takes the provisions the answer file relies on as given. Whether a provision is missing is another check's question.

## Table

One row for each claim the reasoning rests on:

    {"claim": "<what the answer file asserts>", "source": "<the provision it cites, or 'computation', or 'conclusion'>", "holds": "yes" | "no"}

- **Rule claims.** For each provision the reasoning relies on, find it in the rule document and compare what the answer file says it provides (a rate, an amount, a limit, a condition, who qualifies, the order of operations) with the text. `holds` is `no` when the text says something else or the cited provision does not exist.
- **Computations.** Redo each arithmetic step with python3 in one command, from the figures the answer file itself uses. `holds` is `no` when a step does not reproduce.
- **Conclusion.** One row: the last line `Answer: <value>` is the value or conclusion the reasoning arrives at, in the form the task asks for.

## Findings

One finding for each row with `holds` = `no`. For a rule claim, quote the clause that says otherwise. For a computation, leave `rule_text` empty, name the step in `issue` and put what you recomputed for that step in `basis`. For the conclusion, say what the reasoning arrives at and what the last line says.
