---
name: process
label: Process
description: Optional check of how the solver worked (not of the answer's content) - arithmetic done by commands, and rule text in front of the solver for every provision it applies. Reads the solver's trajectory. Off by default.
table: required
quote: optional
trajectory: true
max_steps: 4
---

# Check: process

**Property: the solver did its arithmetic with commands, and had the text of every provision it applies in front of it.**

This check is about how the answer was produced, not about whether it is right. The packet names a file with the solver's full trajectory (its analyses, commands, terminal outputs and sub-agent results, headed `--- step N ---`); read it with `grep -n` and `sed -n`.

## Table

    {"item": "<a computation in the answer file, or a provision it applies>", "kind": "computation" | "provision", "on_record": "yes" | "no", "where": "<step number, or 'first message' for the rule document printed there, or 'nowhere'>"}

- **Computations.** One row per figure in the answer file that results from arithmetic. `on_record` is `yes` when a command in the trajectory computed it and the output shows the figure.
- **Provisions.** One row per provision the answer file applies. `on_record` is `yes` when the provision's text reached the solver: in the rule document printed in its first message, in a reader result, or in the output of a command.

## Findings

One finding for each row with `on_record` = `no`: a figure computed in prose only, or a provision applied from memory. `rule_text` may be empty; `basis` says what you searched for in the trajectory.
