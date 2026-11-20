---
name: fact_table
label: Fact table
description: For every fact stated in the case, find the provision of the rule document that uses it, then compare with how the answer file uses the fact. Finds facts the answer leaves unused although a provision turns on them.
table: required
quote: required
---

# Rubric: fact table

**Property: every fact stated in the case is accounted for: the answer file uses it under the provision that uses it, or a provision makes it irrelevant.**

## Table

One row for **every** fact the task states (a date, an amount, a relationship, a status, an event, a period, a choice the person made). Split a sentence that states several facts. None left out:

    {"fact": "<the fact>", "provision": "<the provision whose condition or computation uses this fact, or 'none'>", "answer": "used" | "not used" | "used differently"}

Fill it in two passes.

1. **`provision`, from the task and the rule document alone.** Do this for all facts before you look at how the answer file treats any of them. Search the rule document for the terms of the fact (the kind of payment, relationship, period, activity or status it describes) and name the provision whose condition or computation turns on it. Go by the rule document, not by which provisions the answer file cites. Write `none` only after you have looked and found no provision that uses the fact.
2. **`answer`, from the answer file.** `used` if its reasoning uses the fact under that provision; `used differently` if it uses the fact under another provision or in another role; `not used` otherwise.

## Findings

One finding for each row where `provision` is not `none` and `answer` is `not used`, or is `used differently` without rule text that supports the different use. Say which fact is unaccounted for and quote the clause that turns on it.

A fact that no provision uses is not a finding. A fact the answer file sets aside with a correct reason from the rule document is not a finding.
