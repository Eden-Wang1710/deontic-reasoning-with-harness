# 07 · What we added to the repo (file map)

The two vendored subtrees (`DeonticBench/`, `harbor-deonticbench/`) are upstream code. Everything
else in the repository, plus a few deliberate edits inside the subtrees, is ours. This file is the
map of those additions: what each file is for and where it is used. Update it whenever a file is
added, renamed or removed outside the subtrees.

Legend: ✅ tracked in git · ⬜ gitignored, exists only on the machine that produced it · ✏️ edit
inside a subtree (will conflict on `git subtree pull`).

## 1. Repository tree (our files only)

```
deontic-reasoning-with-harness/
├── README.md                     ✅ public landing page: what the thesis is, where things are
├── CLAUDE.md                     ✅ auto-loaded context for Claude Code; indexes claude_docs/,
│                                    repo rules (English files, no artifacts/keys in git)
├── .gitignore                    ✅ root ignore list: outputs/, bootstrap_results/, *.log,
│                                    weights, DeonticBench whole splits, tools/db_stats.json
│
├── claude_docs/                  ✅ all project documentation, NN_snake_case.md, English
│   ├── 01_project_overview.md       thesis in one page, repo layout, references
│   ├── 02_setup_and_sync.md         machines 0238 / 601-606, env setup, sync, subtree pulls
│   ├── 03_deonticbench_review.md    close reading of DeonticBench (verified problems vs hypotheses)
│   ├── 04_dar_review.md             close reading of DAR and the Harbor harness
│   ├── 05_data_statistics.md        measured case counts, label balance, prompt/statute lengths
│   ├── 06_initial_proposal.md       initial thesis proposal + §8 decision log (every project decision)
│   ├── 07_repo_additions.md         this file
│   ├── 08_native_trajectories.md    pass-1 trajectory log per model: serving, harness config,
│   │                                runs, scores, failure modes, deviations from the paper
│   ├── 09_verbalized_confidence.md  self-verbalised confidence baseline on those trajectories
│   └── 10_solver_verifier_harness.md  the solver-verifier harness: design, protocol, runs 1-3
│
├── paper_review_gpt/             ✅ GPT-written Chinese summaries of related papers (owner's;
│   ├── NN_snake_case.md             do not edit; exception to the English-only rule)
│   └── 11_selfcompact.md            SelfCompact: rubric-gated context compaction (added 2026-10-22)
│
├── tools/                        ✅ project-owned scripts; nothing of ours goes in the subtrees
│   ├── serve_qwen35_35b.sh          start vLLM for Qwen3.5-35B-A3B on 0238 (bf16, thinking
│   │                                parser, qwen3_xml tool parser, 16k max_new_tokens)
│   ├── run_dar_qwen35_35b.sh        launch a DAR run against that server; AGENT=t2|t2fix|kira,
│   │                                SUBSET=sara|all; writes harbor-deonticbench/jobs/...
│   ├── dar_terminus2_local.py       Terminus2 subclass with a truthful truncation marker
│   │                                (AGENT=t2fix): same 10 KB limit, idempotent limiter
│   ├── dar_terminus2_verifier.py    Terminus2RubricVerifier (AGENT=t2verify): t2fix whose
│   │                                completion confirmation runs a verifier sub-agent in a
│   │                                fresh context with shell access; ≤3 rounds, then finish
│   │                                with a confidence; logs under <trial>/agent/rubric_verifier/
│   ├── skills/rubric_verifier/SKILL.md  the verifier's instructions (five rubrics, report
│   │                                schema); hand-written v1, the object of later optimisation
│   ├── dar_kira_local.py            baseline_kira subclass (AGENT=kira): reshapes Qwen's
│   │                                non-conforming execute_commands tool arguments
│   ├── dar_trajectory_dossiers.py   per-trial dossiers of SARA-numeric trajectories (what
│   │                                statute lines the model saw, its reads, final reasoning);
│   │                                source of the error taxonomy in claude_docs/08 §1.5.1
│   ├── dar_job_summary.py           score a Harbor job dir: accuracy / macro-F1 per subdomain,
│   │                                timing, tokens, 600 s re-scoring; --trials for per-trial lines
│   ├── verbalized_confidence.py     elicit self-verbalised confidence on finished trajectories
│   │                                (t2 / kira; in-context / answer-only / lastturn, the latter
│   │                                re-samples the answer step) and compute ECE, Kuiper, Brier,
│   │                                AUROC, selective accuracy
│   ├── db_direct_confidence.py      the same for the prompt-based direct run: joint (answer +
│   │                                confidence in one generation) / posthoc; reuses the stored
│   │                                prompts of source.json and verbalized_confidence's metrics
│   ├── db_direct_score.py           score a DeonticBench prompt-based direct run under both
│   │                                grading rules (DAR exact match / DeonticBench ±1, macro-F1),
│   │                                bootstrap CI; reuses DeonticBench's parser (needs pandas)
│   ├── deonticbench_stats.py        dataset statistics behind claude_docs/05
│   └── db_stats.json             ⬜ output of deonticbench_stats.py (regenerable)
│
├── outputs/                      ⬜ our run artifacts outside the subtrees
│   └── verbalized_confidence/<run>/ per-trial JSON + summary.json from verbalized_confidence.py
│
├── DeonticBench/                 upstream subtree (prompt-based benchmark)
│   ├── data/*/whole.json         ⬜ fetched per machine from HuggingFace (too large for GitHub)
│   └── outputs/                  ⬜ generate_e2e.py outputs (prompt-based direct runs)
│
└── harbor-deonticbench/          upstream subtree (DAR: Harbor + meta-harness)
    ├── datasets/                 ✏️✅ generated Harbor tasks, tracked here on purpose
    │                                (upstream gitignores them; see claude_docs/02 §5)
    ├── .gitignore                ✏️ the `/datasets/` line commented out, with a note
    └── jobs/qwen3.5-35b-a3b/     ⬜ Harbor job dirs: <framing>/, t2fix-<framing>/, kira-<framing>/,
                                     t2verify-<framing>/
```

Machine-local files that are not in the repository at all (0238): model weights under
`~/models/`, vLLM and run logs under `~/vllm_logs/`, the `myenv` conda environment, the Harbor
`uv` environment inside `harbor-deonticbench/.venv/`.

## 2. How the `tools/` pieces fit together

```
serve_qwen35_35b.sh ──► vLLM at :9009 ◄── run_dar_qwen35_35b.sh ──► harbor run ──► jobs/<...>/
                                   ▲            │ AGENT=t2    stock terminus-2
                                   │            │ AGENT=t2fix dar_terminus2_local.py
                                   │            └ AGENT=kira  dar_kira_local.py
                                   │
          verbalized_confidence.py ┘ (reads jobs/<...>/, writes outputs/verbalized_confidence/)
          dar_job_summary.py         (reads jobs/<...>/, prints the tables in claude_docs/08)
```

- Agent subclasses are loaded by Harbor's `--agent-import-path module:Class` with `tools/` on
  `PYTHONPATH`; the run script sets that. They inherit prompts, parsing and the loop from upstream
  and override one method each, so a `git subtree pull` never touches them.
- DeonticBench's own prompt-based runs use its `scripts/generate_e2e.py` directly (no wrapper), with
  the model served by the same `serve_qwen35_35b.sh`; `db_direct_score.py` scores the resulting
  `DeonticBench/outputs/<domain>/direct/vllm/<model>/source.json`.

## 3. Conventions for new files

- Scripts go in `tools/`; a larger body of thesis code gets its own top-level directory.
- Never put our files inside `DeonticBench/` or `harbor-deonticbench/`; the only in-subtree edits
  are the two marked ✏️ above.
- Run artifacts (`outputs/`, `jobs/`, `bootstrap_results/`), logs, weights and `whole` splits are
  gitignored; the docs in `claude_docs/` carry the numbers.
- Add a line here, and a row in the `CLAUDE.md` table for docs, in the same commit that adds the file.
