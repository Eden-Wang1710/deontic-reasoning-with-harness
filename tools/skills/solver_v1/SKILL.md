---
name: solver-v1
description: How the solver of the reader-verifier harness works - it obtains rule text through a reader sub-agent, has its answer file checked by a verifier sub-agent, writes a three-section answer file and finishes once the checked file is unchanged.
---

# Solver (v1)

You apply a written rule document to the facts of a case and write the answer to the file named in the task. Two sub-agents work for you. You call one by adding a top-level `"subagent"` field to your JSON response, next to `"commands"`. The commands of that response run first, then the sub-agent; its result arrives as your next message. One sub-agent per response.

## Reader: `"subagent": {"name": "reader", "query": "..."}`

The reader searches the rule file in its own context and returns the provisions you asked for **verbatim** (the harness copies the lines from the file), each with its line range, a label and one line on why it matters, plus a list of what it could not resolve. It does not interpret the rules and does not answer the question. Get rule text through the reader rather than by reading the file yourself: your context stays small and the text is exact.

- Write the query as the rule or question you need text for, not as a guessed section number: "the provisions that decide whether the father is a dependent, with the support and income conditions".
- Make the first call broad ("every provision these facts and this question engage") and later calls specific to a gap. Call the reader as often as you need.

## Verifier: `"subagent": {"name": "verifier"}`

The verifier checks your **current** answer file in its own context, with the rule file and your trajectory, against four rubrics: tool use, provision coverage, provision selection and application, grounded reasoning. It reports a status per rubric with one line of evidence, the open items and up to three places to look. A **NO** is a violation it checked against the rule file; an **UNKNOWN** is something it could not verify, a lead rather than an instruction. It never tells you the answer. Write the answer file first, then call it. At most 3 rounds per task.

## Finishing

- Have the verifier check the answer file before you finish. `"task_complete": true` is accepted only if the file is unchanged since the verifier last checked it; otherwise you are asked to call the verifier. Once the 3 rounds are used, it is accepted as the file stands.
- To finish: `"task_complete": true`, an empty `"commands"` array, no `"subagent"`. The task ends at once.
- A response with both a sub-agent call and `"task_complete"` is a call; the flag is ignored.

## Answer file

Three sections in this order. The LAST line is exactly `Answer: <value>`; no other line starts with `Answer:`. Write it with a quoted heredoc (`cat > <answer file> <<'EOF'` ... `EOF`).

    ## Statute
    <the provisions you rely on, one section number per line, e.g. §63(c)(2)(A)>
    ## Reasoning
    <how you apply those provisions to the case facts>
    ## Answer
    Answer: <value>

Do arithmetic with python3 in a command, not in prose, so that the computation is on record.
