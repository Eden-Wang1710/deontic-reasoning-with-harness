# 01 · Project Overview

## What this is

My **master's thesis**: improving LLM **deontic reasoning** — answering questions by applying
explicit rules and policies (statutes, contracts, regulations) to case-specific facts.

The starting point is two consecutive papers by **Guangyao Dou** (JHU, Van Durme group), a senior
PhD student in the group. Both codebases are vendored into this repo:

| | Work | Setup | Path here |
|---|---|---|---|
| ① | **DeonticBench** (arXiv 2604.04443) | The benchmark. Statute goes in the prompt; the model emits an answer or a Prolog program in **one shot**; an external runner executes the Prolog and scores it. | `DeonticBench/` |
| ② | **DAR: Deontic Reasoning with Agentic Harnesses** (arXiv 2606.05009) | Makes ① **agentic**. The statute is a *file* in a container; the agent reads/greps it on demand and can run `swipl` itself, iterating over multiple turns. | `harbor-deonticbench/` |

②'s headline finding: agentic harnesses can push the frontier, **but gains are not uniform** —
weaker models often *degrade* on numerical tasks while consuming far more tokens.

## What the thesis does

**Add self-verification and confidence calibration to the DAR harness (②).** After the agent
completes a trajectory, the same model checks it against a rubric set (tool use, provision
coverage, grounded reasoning) and against Prolog queries on its intermediate conclusions; the
feedback drives answer revision, and the rubric assessments are mapped to a calibrated confidence
that decides whether to accept the answer or send the case to a human. Evaluated by accuracy before
vs. after verification, calibration (ECE, Kuiper) and break-even price on SARA-Numeric. Extensions:
hidden-state probes and harness distillation into Qwen. The proposal, reviewed with the PhD senior
and the advisor, is restated in [05_confidence_proposal.md](05_confidence_proposal.md), which
doubles as the decision log.

The direction is grounded in two close-reading notes:

- [03_deonticbench_review.md](03_deonticbench_review.md) — ①: paper, code and data
- [04_dar_review.md](04_dar_review.md) — ②: paper and code

Everything marked ✅ in those notes (reference Prolog written against the answer key; Airline
few-shot exemplars drawn from the test set itself; the released scorer not reproducing the
published metric; the two papers scoring numeric answers differently; DAR's two custom agents
missing from the released code) is a **concrete entry point for improvement**, not a complaint.
This is in-group work — the author is reachable, and missing pieces can simply be asked for.

## Repo layout

| Path | Contents |
|---|---|
| `README.md` | Short English pointer for the GitHub landing page. |
| `CLAUDE.md` | Context auto-loaded by Claude Code: index of `claude_docs/` plus the easy-to-get-wrong list. |
| `claude_docs/` | All project documentation, numbered `NN_snake_case.md`. |
| `DeonticBench/` | ①'s upstream code (scripts, `hard` / `smoke` data, statutes, prompts). **git subtree** (`guangyaodou/DeonticBench` @ `3c168f9`, squashed). Plain files; editing them is fine. |
| `harbor-deonticbench/` | ②'s upstream code (Harbor 0.3.0 + meta-harness + DeonticBench adapters + scorer). **git subtree** (`guangyaodou/harbor-deonticbench` @ `2fe3aa4`, squashed, vendored 2026-09-17). Apache-2.0. |

Only `README.md` and `CLAUDE.md` live at the repo root (GitHub renders the first on the landing
page; Claude Code auto-loads the second only from the root). Every other doc goes in `claude_docs/`.

Where my own code will live is decided once the direction is fixed (see `05`). The rule is: keep it
**out of the two subtree directories**, or every future `git subtree pull` will conflict.

## References

**① DeonticBench**
- Paper: [arXiv 2604.04443](https://arxiv.org/abs/2604.04443)
- Code: <https://github.com/guangyaodou/DeonticBench> · [project page](https://guangyaodou.github.io/DeonticBench/)
- Data: [gydou/DeonticBench on HuggingFace](https://huggingface.co/datasets/gydou/DeonticBench) (CC-BY-4.0; 5 configs × `whole` / `hard`)

**② DAR**
- Paper: [DAR: Deontic Reasoning with Agentic Harnesses (arXiv 2606.05009)](https://arxiv.org/abs/2606.05009) —
  Guangyao Dou, William Jurayj, Nils Holzenberger, Benjamin Van Durme; submitted 2026-06-03
- Code: <https://github.com/guangyaodou/harbor-deonticbench> · [project page](https://guangyaodou.github.io/harbor-deonticbench/)

**Upstream dependencies**
- [Harbor](https://github.com/harbor-framework/harbor) — agent evaluation framework (Apache-2.0)
- [meta-harness](https://github.com/stanford-iris-lab/meta-harness) — Stanford IRIS Lab, Kira agents

## Visibility

This repo is **public** (owner's decision, 2026-10-14). Consequences:

- `guangyaodou/DeonticBench` ships no LICENSE file (only the HF dataset is explicitly CC-BY-4.0);
  `harbor-deonticbench` is Apache-2.0. The vendored copies are in-group code kept here with the
  author's knowledge; if licensing ever becomes a question, ask the author directly.
- Never commit API keys, run artifacts, model weights, or anything the author has not released
  (e.g. unreleased agents obtained privately). Unfinished thesis text is fine to keep here.
