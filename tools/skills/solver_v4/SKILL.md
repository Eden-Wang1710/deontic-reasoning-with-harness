---
name: solver-v4
description: How the solver of the solver-checks harness works - the rule document is in its first message when it is short enough (otherwise it comes through the reader), the solver goes through every top-level unit of the document and every case fact before it computes, writes a four-section answer file, has it checked by the verifier (a list of independent checks) and answers each finding by a correction or a rebuttal. v4 (2026-11-17); no compactor.
---

# Solver (v4)

You apply a written rule document to the facts of a case and write the answer to the file named in the task. A **verifier** checks your answer file before you finish.

## The rule document

- If a block headed `=== RULE DOCUMENT: ... (complete, N lines) ===` is printed above this skill, that block is the whole rule file, verbatim. Work from it. Do not print the file again (`cat`, `sed`, `head`, a script): it is already in front of you and a second copy only fills your context. A `grep -n` for a term is fine.
- If a note in its place says that the rule document is too long to include, get rule text through the reader: `"subagent": {"name": "reader", "query": "<the rule or question you need text for>"}`. It returns provisions verbatim with their line ranges. Start with a broad query (every provision these facts and this question engage, with the definitions, conditions and exceptions they depend on) and ask again for each gap.

## Work in this order

1. **Go through the whole rule document before you compute anything.** Take its top-level units in document order (sections, articles, numbered rules: whatever the document's own top-level division is) and decide for each one whether the facts of this case engage it. A unit is engaged when the facts meet the conditions under which it imposes, grants, limits or defines something the question depends on, whether or not the question names it. Decide from the unit's own conditions and definitions, not from its heading. A unit often has a general rule together with exceptions, limitations or reductions that have conditions of their own: decide those too.
2. **Go through the case facts.** For each stated fact, name the provision that uses it, or the provision that makes it irrelevant. A fact that no provision on your list uses is a sign that a provision is missing from the list: look for it.
3. **Write the first two sections of the answer file** (`## Sections`, `## Facts`, see below) from steps 1 and 2.
4. **Apply the engaged provisions.** Do arithmetic with python3 in a command, not in prose, so that the computation is on record.
5. **Complete the answer file and call the verifier.** The verifier only looks at an answer file that has all four sections; a file with nothing but the answer line is sent back unchecked.

## Sub-agents

`"subagent"` is a **top-level field of your JSON response, next to `"commands"`**. It is never a keystroke and never a field inside a command object. One sub-agent per response; the commands of that response run first, then the sub-agent, and its result arrives as your next message.

- Verifier: `"subagent": {"name": "verifier"}`. Runs a list of independent checks on your **current** answer file, each in its own context with the task and the rule document. Each check reports PASS or FAIL with numbered findings. A finding says what is wrong or missing, quotes the rule text it rests on and names the case fact. The verifier never tells you the answer. At most 3 rounds per task.
- Verifier with a rebuttal: `"subagent": {"name": "verifier", "rebuttal": "<finding number>: <the rule text or case fact that defeats it>"}`. See below.
- Reader: only when the rule document is not printed above (see "The rule document").

## Acting on a verifier report

A finding is a claim about your answer file made by an agent that read the same rule document. It can be wrong. For each finding, read the quoted rule text in the rule document together with the conditions around it, then decide:

- **The finding holds**: correct the answer file (recompute with python3 where numbers change) and call the verifier again.
- **The finding does not hold**: leave the file as it is and call the verifier with a rebuttal that names the finding by its number and gives the rule text or the case fact that defeats it. The checks that raised findings then look again; they withdraw what the rule text does not support.

Do not change the answer file only because a finding says so, and do not dismiss a finding without reading the text it quotes. A finding marked `quote NOT found in the rule document` cites text the harness could not find in the file: treat it as a lead, not as rule text.

## Finishing

- `"task_complete": true` is accepted when the latest report on the answer file as it stands is PASSED. Otherwise you are told what is missing. Once the 3 rounds are used, it is accepted as the file stands.
- To finish: `"task_complete": true`, an empty `"commands"` array, no `"subagent"`. The task ends at once.
- A response with both a sub-agent call and `"task_complete"` is a call; the flag is ignored.

## Answer file

Four sections in this order. The LAST line is exactly `Answer: <value>` in the form the task asks for; no other line starts with `Answer:`. Write the file with a quoted heredoc (`cat > <answer file> <<'EOF'` ... `EOF`).

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
