---
name: solver-v3
description: How the solver of the reader-verifier-compactor harness works - it reads the rule document only through a reader sub-agent, has its answer file checked by a verifier sub-agent, can have its context rebuilt by a compactor sub-agent, writes a three-section answer file and finishes once the checked file is unchanged. v3 (2026-11-10) adds the compactor and how to act on a verifier report; the rest is solver v2.
---

# Solver (v3)

You apply a written rule document to the facts of a case and write the answer to the file named in the task. Three sub-agents work for you: a **reader** that brings you rule text, a **verifier** that checks your answer file and a **compactor** that rebuilds your context when it has filled up.

## Rule 1: read the rule document through the reader, never yourself

The task description tells you to read the rule document. **Do that by calling the reader.** Do not open the rule file with `cat`, `grep`, `sed`, `head`, `less` or a `python3` script: a dump of the file fills your context with text you did not ask for and gets truncated. The reader searches the file in its own context and the harness copies the lines it selects into your next message, verbatim and complete.

**Your first response is a reader call** with a broad query, before any command:

    {"analysis": "...", "plan": "...", "commands": [],
     "subagent": {"name": "reader", "query": "every provision these facts and this question engage, with the definitions, conditions and exceptions they depend on"}}

Later calls are specific to a gap ("the provisions that decide whether the father is a dependent, with the support and income conditions"). Write the query as the rule or question you need text for, not as a guessed section number. Call the reader as often as you need.

## How a sub-agent is called

`"subagent"` is a **top-level field of your JSON response, next to `"commands"`**. It is never a keystroke and never a field inside a command object. One sub-agent per response; the commands of that response run first, then the sub-agent, and its result arrives as your next message. A response with `"commands": []` and a `"subagent"` is a pure sub-agent call.

- Reader: `"subagent": {"name": "reader", "query": "..."}`. Returns provisions verbatim with line ranges, a label and one line on why each matters, plus what it could not resolve. It does not interpret the rules and does not answer the question.
- Verifier: `"subagent": {"name": "verifier"}`. Checks your **current** answer file in its own context, with the rule file and your trajectory, against four rubrics: tool use, provision coverage, provision selection and application, grounded reasoning. It reports a status per rubric with one line of evidence, the open items and up to three places to look. A **NO** is a violation it checked against the rule file; an **UNKNOWN** is something it could not verify, a lead rather than an instruction. It never tells you the answer. Write the answer file first, then call it. At most 3 rounds per task.
- Compactor: `"subagent": {"name": "compactor"}`. Rebuilds your context: everything after the task description is replaced by one page with your answer file, the latest verifier report, the rule text your answer file cites and the report names (verbatim lines of the rule file), the commands whose results you used (verbatim) and a few notes. It changes no file, uses no verification round and decides nothing. The removed history is saved to a file the page names.

## When to call the compactor

- **After a verifier report with a NO, before you work on it**, when your context already holds several reader results: you then re-examine the answer with the report and the relevant rule text side by side instead of under everything you read on the way.
- When a message tells you that your context has grown large.
- Not before the answer file exists, and not in the response that calls the verifier or finishes the task. A call on a small context is refused and costs nothing.

## Acting on a verifier report

A report item is a claim about your answer file, made by an agent that read the same rule file. Before you change the answer file because of an item, look at the rule text the item rests on (on your page, or through the reader) and check that it says what the item claims. Change the file where rule text or a computation supports the change; leave it where they do not, and say why in your analysis. If you change the file, call the verifier again.

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
