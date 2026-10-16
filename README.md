# Deontic Reasoning with Harness

Master's thesis work on **deontic reasoning** with LLMs — answering questions by applying explicit
rules and policies (statutes, contracts, regulations) to case-specific facts.

Built on two papers by Guangyao Dou (JHU, Van Durme group), both vendored here as git subtrees:

- **DeonticBench** ([paper](https://arxiv.org/abs/2604.04443) · [code](https://github.com/guangyaodou/DeonticBench) · [data](https://huggingface.co/datasets/gydou/DeonticBench)) — the benchmark: statute in the prompt, one-shot answer or Prolog program.
- **DAR: Deontic Reasoning with Agentic Harnesses** ([paper](https://arxiv.org/abs/2606.05009) · [code](https://github.com/guangyaodou/harbor-deonticbench)) — the agentic follow-up: the statute is a file in a container and the agent reads it on demand.

## Where things are

| Path | What it is |
|---|---|
| [`claude_docs/`](claude_docs/) | All project documentation, numbered |
| ├ [`01_project_overview.md`](claude_docs/01_project_overview.md) | What the thesis is, repo layout, references, visibility |
| ├ [`02_setup_and_sync.md`](claude_docs/02_setup_and_sync.md) | Per-machine setup, multi-machine sync, regenerating datasets, pulling upstream |
| ├ [`03_deonticbench_review.md`](claude_docs/03_deonticbench_review.md) | Close reading of the DeonticBench paper, code and data |
| ├ [`04_dar_review.md`](claude_docs/04_dar_review.md) | Close reading of DAR and the Harbor harness |
| ├ [`05_confidence_proposal.md`](claude_docs/05_confidence_proposal.md) | Thesis proposal (self-verification and confidence calibration in the DAR harness) and the decision log |
| ├ [`06_data_statistics.md`](claude_docs/06_data_statistics.md) | Measured dataset sizes, prompt lengths, and what DAR uses |
| ├ [`07_native_trajectories.md`](claude_docs/07_native_trajectories.md) | Pass-1 DAR trajectories per model: serving, harness config, runs, scores, failure modes |
| ├ [`08_verbalized_confidence.md`](claude_docs/08_verbalized_confidence.md) | Self-verbalised confidence baseline and calibration metrics on those trajectories |
| └ [`09_repo_additions.md`](claude_docs/09_repo_additions.md) | Map of every file we added outside the vendored subtrees |
| [`tools/`](tools/) | Project-owned scripts (outside the subtrees) |
| [`CLAUDE.md`](CLAUDE.md) | Context auto-loaded by Claude Code; indexes `claude_docs/` |
| [`DeonticBench/`](DeonticBench/) | Upstream benchmark (prompt-based eval), git subtree |
| [`harbor-deonticbench/`](harbor-deonticbench/) | Upstream agentic harness (Harbor + meta-harness), git subtree |

Licensing: `harbor-deonticbench` is Apache-2.0. `DeonticBench` ships no LICENSE file (only the
HuggingFace dataset is CC-BY-4.0); it is vendored here as in-group code with the author's
knowledge, not as a redistribution.
