# DeonticReasoningWithHarness

Master's thesis on LLM **deontic reasoning** (applying explicit rules and policies to case facts),
built on two papers by a senior PhD student in the same group, Guangyao Dou (JHU, Van Durme):
**DeonticBench** (arXiv 2604.04443, vendored at `DeonticBench/`) and **DAR: Deontic Reasoning with
Agentic Harnesses** (arXiv 2606.05009, vendored at `harbor-deonticbench/`).

The owner works on two lab cluster machines (0238, a DGX Spark with Docker; 601-606, the GPU
nodes), so **this git repo is the only shared state** — anything worth keeping must be committed
and pushed. See `claude_docs/02_setup_and_sync.md` for per-machine status.

## Docs: `claude_docs/`

All project documentation lives in `claude_docs/`. Read the relevant file before acting; do not rely
on this summary alone.

| File | What it contains | Read it when |
|---|---|---|
| `claude_docs/01_project_overview.md` | What the thesis is and what it builds on, repo layout, references, repo visibility | Starting a session; unsure what belongs where |
| `claude_docs/02_setup_and_sync.md` | The two cluster machines (0238 DGX Spark, 601-606 GPU nodes) and their verified state, project env setup, sync workflow between them, regenerating `datasets/`, pulling both subtrees | Setting up or running anything on either machine; syncing; updating either subtree |
| `claude_docs/03_deonticbench_review.md` | Close reading of DeonticBench: task/eval/training summary, headline results, verified problems (✅) vs. hypotheses (🔶), a script reproducing the reference-Prolog scan | Designing experiments; before trusting any number from that paper or any reference program |
| `claude_docs/04_dar_review.md` | Close reading of DAR: what the harness actually does, grading differences vs. DeonticBench, gaps in the released code, what DAR leaves open | Working with `harbor-deonticbench/`; comparing the two regimes |
| `claude_docs/05_confidence_proposal.md` | The thesis proposal (reviewed with the PhD senior and advisor): self-verification (rubric-based + Prolog symbolic) and confidence calibration inside the DAR harness, evaluated by accuracy, ECE/Kuiper and break-even price; data, models, extensions (probes, harness distillation), open items, and the decision log | Writing the thesis; designing or running any verification or confidence experiment; recording any project-level decision |
| `claude_docs/06_data_statistics.md` | Measured sizes: case counts per domain/split, label balance, statute and exemplar token counts, full prompt length per DeonticBench mode, and exactly which data DAR uses (hard split, 4 subdomains, 173 × 3 tasks) with the agent's starting context per framing | Sizing any experiment; estimating context or cost; choosing splits |
| `claude_docs/07_native_trajectories.md` | Log of pass-1 ("native") DAR trajectories per model: how each model was served (vLLM settings), harness configuration, runs, scores, and observed failure modes | Running or analysing a baseline trajectory; choosing serving/harness settings for a new model |
| `claude_docs/08_verbalized_confidence.md` | Self-verbalised confidence baseline on the native trajectories: method (in-context vs. answer-only elicitation), calibration results (ECE, Kuiper, Brier, AUROC, selective accuracy) per harness and subdomain, observations | Running or comparing any confidence estimate; before designing the rubric → confidence mapping |

### Doc conventions

- Every new markdown doc goes in `claude_docs/`, named `NN_snake_case.md` with a two-digit prefix.
  Use the next free number; do not renumber existing files (links depend on them).
- When you add, rename or substantially change a doc, update the table above in the same change.
- **All docs are written in English**, with a `# NN · Title` heading. `README.md` and `CLAUDE.md`
  stay at the repo root and are the only markdown files there. (The owner's working language is
  Chinese — respond in Chinese in conversation, but write every file in English.)
- Mark claims as verified (✅) or hypothesis (🔶) when recording findings, and date them.
- Project-level decisions go in the decision log in `05`, not scattered across docs.

## Our code: `tools/`

Small project-owned scripts live in `tools/` at the repo root (e.g. `tools/deonticbench_stats.py`),
outside both subtrees. Larger thesis code gets its own top-level directory when it exists.

## Paper summaries: `paper_review_gpt/`

`paper_review_gpt/` holds summaries of related papers that the owner generates **with GPT**, one
file per paper, named `NN_snake_case.md` with a two-digit reading-order prefix (e.g. `03_trustworthy_tax_reasoning.md`,
`04_calibrating_llm_judges.md`). They are Chinese technical reports written by GPT, not Claude-authored docs.

- Read them for background on related work, but treat their claims as secondary — check the
  original paper before relying on a number or a detail.
- Do not edit, translate, rename or move them unless asked; they are an exception to the
  "English only" and "docs go in `claude_docs/`" rules above.
- Findings about a paper that matter to the thesis still go in `claude_docs/` (e.g. `05`), in English.

## Repo facts

- `DeonticBench/` (prompt-based eval) and `harbor-deonticbench/` (agentic eval) are both upstream
  repos vendored as squashed **git subtrees**. Plain files; editing them is fine. Never clone into
  them or convert them to submodules. Keep our own code **out** of both, or every `git subtree pull`
  will conflict.
- `harbor-deonticbench/datasets/` **is tracked here**, deliberately against upstream, which
  gitignores it. The `/datasets/` line in `harbor-deonticbench/.gitignore` is commented out with a
  note; expect a conflict there on `subtree pull` and keep our version. See `02`.
- `harbor-deonticbench/meta-harness/` is documented upstream as a submodule but is committed as
  plain files on `main`; there is no `.gitmodules`, so `--recurse-submodules` is a no-op here.
- Prolog eval needs `swipl` on PATH; the Harbor side needs Python ≥3.12, `uv` and a Docker daemon
  the user can reach. Check the per-machine tables in `02` first; 0238 is ARM (aarch64).
- API keys come from environment variables. Never write keys into files.

## Easy to get wrong (evidence in `claude_docs/03` and `04`)

- **The two papers grade numeric answers differently.** DeonticBench allows ±$1; DAR's `test.sh`
  does exact integer match. SARA Numeric / Airline numbers are not comparable across them without
  re-scoring.
- `DeonticBench/scripts/bootstrap_outputs.py` reports **accuracy only**. The paper's SARA Binary /
  Housing / USCIS numbers are macro-F1 with abstentions mapped to the opposite class, so the released
  script cannot reproduce them. `harbor-deonticbench/evaluate_deonticbench.py` *does* implement
  macro-F1 — port from there.
- Airline few-shot takes its exemplar from the evaluated cases file itself (another test case's
  reference program, same cabin class) unless `--airline-exemplar-pool` is passed.
- Reference Prolog programs were kept only if their output matched the gold label; some hardcode or
  rationalize the answer. Do not treat them as ground-truth reasoning, or as clean SFT targets,
  without filtering.
- Housing `hard` is balanced yes/no, yet every model scores below chance. Suspected label-convention
  mismatch — a hypothesis, not yet verified. **DAR drops Housing entirely**: 4 subdomains, 173 tasks.
- `kira_deontic_grounded` and `kira_compute_then_answer` are referenced by DAR's README and run
  scripts but **are not in the released code**; neither are the `deonticbench-cc-*` adapters.
- Hard sets are small (28–80 cases); always report confidence intervals.

## Conventions

- The owner writes in Chinese: **respond in Chinese** unless asked otherwise. All files — docs, code,
  comments, commit messages — are in English.
- Do not commit run artifacts (`outputs/`, `bootstrap_results/`, `jobs/`), model weights, or `whole`
  splits. `datasets/` is the deliberate exception (see above).
- The GitHub repo is **public** (owner's decision, 2026-10-14). Never commit API keys, run
  artifacts or anything the author of the vendored repos has not released.
