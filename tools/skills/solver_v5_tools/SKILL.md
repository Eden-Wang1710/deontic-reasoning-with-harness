---
name: solver-v5-tools
description: How the solver of the solver-reader-verifier-compaction harness (v0.1) works - the rule document reaches it through the reader (a survey before its first step, more on request), it goes through every top-level unit of the document and every case fact before it computes, writes a four-section answer file with the write_file tool, has it checked once by the verifier (a rubric table) and decides on each finding itself. Same content as solver-v5; the reader, the verifier, compaction and the file write are native tools (KIRA adapter) instead of fields of a JSON reply. v5 (2026-11-20).
---

# Solver (v5, tool calls)

You apply a written rule document to the facts of a case and write the answer to the file named in the task. A **verifier** checks your answer file once before you finish.

## The rule document

The rule document is not printed in full. A **reader** has surveyed it for this task; its result is at the end of your first message, headed `=== READER RESULT (survey ...) ===`:

- an **outline**: one line for every top-level unit of the document (sections, articles, numbered rules: whatever the document's own top-level division is) with its line range and the reader's first reading of whether the case engages it;
- **passages**: the rule text the reader judged this case to need, copied verbatim by the harness, with line ranges.

The outline is the reader's first reading, not a finding: you decide. Whenever you need text that is not in front of you (a unit the outline marks as not engaged and you want to see, a definition, an exception, the lines around a passage), ask the reader: call the tool `reader` with `query` set to what you need (a unit, a line range from the outline, or a question). It returns the lines verbatim. Do not print the rule file into the terminal (`cat`, `sed`, `head`, a script): that fills your context with text you will not use. A `grep -n` for a term is fine.

If a block headed `=== RULE DOCUMENT: ... (complete, N lines) ===` is printed in your first message instead, that block is the whole rule file: work from it; the reader is then not available.

## Work in this order

1. **Go through the whole outline before you compute anything.** Take the top-level units in document order and decide for each one whether the facts of this case engage it. A unit is engaged when the facts meet the conditions under which it imposes, grants, limits or defines something the question depends on, whether or not the question names it. Decide from the unit's own conditions and definitions, not from its heading: if you have not seen the text of a unit you cannot rule out, ask the reader for it. A unit often has a general rule together with exceptions, limitations or reductions that have conditions of their own: decide those too.
2. **Go through the case facts.** For each stated fact, name the provision that uses it, or the provision that makes it irrelevant. A fact that no provision on your list uses is a sign that a provision is missing from the list: look for it.
3. **Apply the engaged provisions.** Do arithmetic with python3 in a command, not in prose, so that the computation is on record.
4. **Write the answer file and call the verifier**, in one response: a `write_file` call followed by a `verifier` call. The verifier only looks at an answer file that has all four sections.

## Writing files

Write the answer file with the tool `write_file` (`path`: the answer file, `content`: the complete file), not with a heredoc or `echo` in the terminal. The harness writes the file and answers with one line (path, number of lines, last line). Do not print the file afterwards (`cat`): what you wrote is what is there. To change the file, call `write_file` again with the complete new content.

## Sub-agents

The sub-agents are **tools**, called like `execute_commands`. They are never a keystroke. One sub-agent per response; `write_file` calls and the commands of that response run first, then the sub-agent, and its result arrives as your next message.

- Reader: tool `reader`, argument `query`. Returns rule text verbatim with line ranges (see "The rule document").
- Verifier: tool `verifier`, no arguments. Judges your **current** answer file against a rubric table: several rubrics, each judged in its own context with the task and the complete rule document. Each rubric reports PASS or FAIL with numbered findings. A finding says what is wrong or missing, quotes the rule text it rests on and names the case fact. The verifier never tells you the answer. It runs **once** per task: call it when the answer file is complete, not before.
- Compaction: tool `compaction`, no arguments. Rebuilds your context when it has grown long: the steps between your first message and your latest ones are replaced by one page with your state, the rule text you rely on, your answer file and the verifier's report. Nothing in the environment changes. The harness also does this by itself when the context passes its limit.

## Acting on the verifier's report

A finding is a claim about your answer file made by an agent that read the complete rule document. It can be wrong. For each finding, read the quoted rule text together with the conditions around it (ask the reader for the surrounding lines if you do not have them), then decide:

- **The finding holds**: correct the answer file (recompute with python3 where numbers change, then `write_file` with the complete new content).
- **The finding does not hold**: leave the file as it is.

Do not change the answer file only because a finding says so, and do not dismiss a finding without reading the text it quotes. A finding marked `quote NOT found in the rule document` cites text the harness could not find in the file: treat it as a lead, not as rule text. The corrected file is not checked again: after you have decided on every finding, finish.

## Finishing

- `task_complete` is accepted once the verifier has run. Before that you are told to call it.
- To finish: call `task_complete` alone, with no other tool call in the response. The task ends at once.
- A response with both a sub-agent call and `task_complete` is a call; `task_complete` is ignored.

## Answer file

Four sections in this order. The LAST line is exactly `Answer: <value>` in the form the task asks for, with nothing after the value; no other line starts with `Answer:`.

    ## Sections
    <one line for EVERY top-level unit of the rule document, in document order:
     <unit>: applies | does not apply - <the case fact or condition that decides it>>
    ## Facts
    <one line per case fact:
     <fact> - <the provision that uses it, or the provision that makes it irrelevant>>
    ## Reasoning
    <provision by provision, with subsection numbers, how each engaged provision applies to the facts>
    ## Answer
    Answer: <value>
