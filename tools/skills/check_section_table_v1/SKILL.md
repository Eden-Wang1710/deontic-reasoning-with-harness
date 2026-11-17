---
name: section_table
label: Section table
description: For every top-level unit of the rule document, decide from the case facts whether the case engages it, then compare with what the answer file applies. Finds provisions that are engaged but missing from the answer, and provisions applied although their conditions are not met.
table: required
quote: required
---

# Check: section table

**Property: the answer file applies every part of the rule document that the facts of the case engage, and no part that they do not engage.**

## Table

One row for **every** top-level unit of the rule document (sections, articles, numbered rules: whatever the document's own top-level division is), in document order, none left out:

    {"unit": "<its number or heading>", "engaged": "yes" | "no", "basis": "<the case fact that meets, or fails, the unit's condition>", "answer": "applied" | "not applied" | "partly"}

Fill it in two passes.

1. **`engaged` and `basis`, from the task and the rule document alone.** Do this for all units before you look at how the answer file treats any of them. A unit is engaged when the facts meet the conditions under which it imposes, grants, limits or defines something the question depends on, whether or not the question names it. Decide from the unit's operative conditions and definitions, not from its heading. Where a unit has a general rule together with exceptions, limitations or reductions that have conditions of their own, decide each such part: the unit is engaged if any part is, and `basis` names the part.
2. **`answer`, from the answer file.** `applied` if its reasoning applies the unit; `partly` if it applies the unit but leaves out a part whose condition the facts meet; `not applied` otherwise. A line that only declares a unit inapplicable counts as `not applied`.

## Findings

One finding for each row where the two passes disagree:

- `engaged` is `yes` and `answer` is `not applied` or `partly`: say which unit or part is missing and quote the clause whose condition the facts meet.
- `engaged` is `no` and `answer` is `applied`: say which unit is applied without its condition being met and quote the clause that states the condition.

A row where both passes agree is not a finding.
