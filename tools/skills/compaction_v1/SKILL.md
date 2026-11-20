---
name: compaction-v1
description: Compaction of the solver-reader-verifier-compaction harness (v0.1). Runs as a branch of the solver's conversation, without thinking. Writes a short state page that replaces the solver's earlier steps and names the rule-document lines the remaining work needs; the harness adds the answer file, the verifier's report and the named lines verbatim. v1 (2026-11-20).
---

# Compaction (v1)

For this one reply you write a **state page** for yourself. The conversation above has grown long. After this reply, the messages between your first reply and your latest steps are removed and your page stands in their place. You continue the task from the first message, the page and your latest steps, so the page must hold everything from the removed part that the rest of the task needs, and nothing else.

The harness adds these to the page by itself, verbatim; do **not** copy them:

- the answer file as it is on disk, if there is one;
- the verifier's report, if it has run;
- the lines of the rule document you name in `keep_source`.

The first message (the task, with the reader's survey result) stays as it is.

## What the page holds

Write it as short lines under these headings; leave a heading out when there is nothing under it.

- `Established`: what you have determined, each with the provision or the computation it rests on and the value: which units are engaged and why, how each case fact is used, intermediate and final figures. Give numbers exactly as computed.
- `Rejected`: what you considered and ruled out, with the reason, so that you do not redo it.
- `Open`: what is undecided or unchecked, and findings of the verifier you have not yet decided on.
- `Next`: the next step you were about to take.

Write state, not history: "X is 5,300 (computed from A and B under provision P)", not "I ran a command and then looked at P". Do not restate the task. Do not quote rule text: name the lines in `keep_source`.

## Reply format

Exactly one JSON object, nothing before or after it. The solver's response format does not apply to this reply. No commands.

    {"page": "Established\n- ...\n- ...\nOpen\n- ...\nNext\n- ...",
     "keep_source": [{"lines": "120-131", "label": "<number and heading of the provision>"}]}

- `page`: a JSON string; at most about 600 words.
- `keep_source`: line ranges of the rule document (`"a-b"`, 1-based, inclusive) that were delivered to you after the first message and that the remaining work still needs. Leave out what the first message already contains and what you no longer need. An empty list if none.
