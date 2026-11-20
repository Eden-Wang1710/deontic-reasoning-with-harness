# 05 · What we added to the repo (file map)

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
├── .gitignore                    ✅ root ignore list: bootstrap_results/, wandb/, stray *.log,
│                                    weights, DeonticBench whole splits, harbor-deonticbench/jobs/
│                                    (ignored again since 2026-10-31); re-includes outputs/ and DeonticBench/outputs/
│
├── claude_docs/                  ✅ all project documentation, NN_snake_case.md, English
│   ├── 01_project_overview.md       thesis in one page, repo layout, references
│   ├── 02_setup_and_sync.md         machines 0238 / 601-606, env setup, sync, subtree pulls
│   ├── 03_background_review.md      close readings of DeonticBench (Part I) and DAR (Part II),
│   │                                measured data statistics (Part III)
│   ├── 04_initial_proposal.md       initial thesis proposal + §8 decision log (every project decision)
│   ├── 05_repo_additions.md         this file
│   ├── 06_native_trajectories.md    pass-1 trajectory log per model: serving, harness config,
│   │                                runs, scores, failure modes, deviations from the paper
│   ├── 07_verbalized_confidence.md  self-verbalised confidence baseline on those trajectories
│   ├── 08_solver_verifier_harness.md  the solver-verifier harness: design, protocol, runs 1-3
│   ├── 09_reader_verifier_harness.md  the reader-verifier harness (v1 of reader-verifier-compactor):
│   │                            sub-agents as tools, completion gate, reader/verifier skills, runs;
│   │                            §10 = version 2, the compactor (design, replay debugging, smokes)
│   └── figures/                     reader.{svg,png}, compactor.{svg,png}: overview figures of the two
│                                sub-agents (from tools/make_harness_figures.py)
│
├── paper_review_gpt/             ✅ GPT-written Chinese summaries of related papers (owner's;
│   ├── NN_snake_case.md             do not edit; exception to the English-only rule)
│   └── 11_selfcompact.md            SelfCompact: rubric-gated context compaction (added 2026-10-22)
│
├── tools/                        ✅ project-owned scripts; nothing of ours goes in the subtrees
│   ├── serve_qwen35_35b.sh          start vLLM for Qwen3.5-35B-A3B on 0238 (bf16, thinking
│   │                                parser, qwen3_xml tool parser, 16k max_new_tokens); MODEL_DIR,
│   │                                SERVED_NAME, MAX_NUM_SEQS and LOG serve another local model
│   │                                (Qwen3.8-27B, 2026-11-17, 06)
│   ├── run_dar_qwen35_35b.sh        launch a DAR run against that server; AGENT=t2|t2fix|t2verify{,2,3}|t2rv|t2rvc|t2sc|t2srvc|kirasrvc|kira,
│   │                                SUBSET=sara|numeric|binary|all, MODEL=<served name>; writes
│   │                                harbor-deonticbench/jobs/<MODEL>/...
│   ├── dar_terminus2_local.py       Terminus2 subclass with a truthful truncation marker
│   │                                (AGENT=t2fix): same 10 KB limit, idempotent limiter
│   ├── dar_terminus2_verifier.py    Terminus2RubricVerifier (AGENT=t2verify): t2fix whose
│   │                                completion confirmation runs a verifier sub-agent in a
│   │                                fresh context with shell access; ≤3 rounds, then finish
│   │                                with a confidence; logs under <trial>/agent/rubric_verifier/
│   │                                also holds SubagentClock, the wall-clock limit with one
│   │                                no-thinking fallback used by the reader and the verifier
│   │                                (claude_docs/09 §10.9)
│   ├── skills/rubric_verifier/SKILL.md  the verifier's instructions (five rubrics, report
│   │                            schema); hand-written v1; v2 and v3 (run-2, run-3) alongside
│   ├── skills/rubric_verifier_v4/SKILL.md  verifier of the reader-verifier harness: four rubrics
│   │                            (tool use, provision coverage, provision selection, grounded
│   │                            reasoning); text that reached the solver through a reader result
│   │                            counts as coverage
│   ├── skills/reader_v1/SKILL.md    the reader's instructions: goal = coverage, three-step search,
│   │                            return line ranges + labels, never an answer; the harness extracts the text
│   ├── skills/solver_v1/SKILL.md    the solver's instructions for the reader-verifier harness (how to
│   │                            call the two sub-agents, finishing protocol, answer-file format);
│   │                            filled into the {skill} slot of the multiagent template; rv-smoke1 only
│   ├── skills/solver_v2/SKILL.md    default since 2026-11-01: opens with "read the rule document through
│   │                            the reader, never yourself", first response = broad reader call,
│   │                            field position stated before anything else
│   ├── templates/                   solver prompt templates swapped in by derivation_record=true:
│   │                            terminus-json-plain-derivation{,-v2}.txt (runs 2, 3),
│   │                            terminus-json-multiagent-v1.txt (rv-smoke1), -v2.txt (t2rv default: a
│   │                            second example response that is a sub-agent call), -v3.txt (t2rvc:
│   │                            v2 plus the compactor call)
│   ├── skills/solver_v3/SKILL.md    solver skill of AGENT=t2rvc: v2 plus the compactor, when to call it,
│   │                            and "a report item is a claim to check against rule text"
│   ├── skills/compactor_v1/SKILL.md the compactor's instructions: select rule line ranges (by the
│   │                            outline in its packet) and solver steps, never write or judge
│   ├── dar_terminus2_compactor.py   Terminus2ReaderVerifierCompactor (AGENT=t2rvc): t2rv plus a compactor
│   │                            sub-agent; the harness snaps ranges to outline boundaries, restores
│   │                            cited sections, rebuilds the solver context as task + one state page;
│   │                            logs under <trial>/agent/compactor/ (claude_docs/09 §10)
│   ├── dar_cost_ledger.py           CostLedgerMixin, a base of every harness above (since 2026-11-11): logs
│   │                            every model call of a trial to <trial>/agent/llm_calls.jsonl (role,
│   │                            tokens, how the reply ended, prefix shared with earlier prompts) and
│   │                            stops the trial at cost_budget_usd; writes agent/cost_budget.json
│   │                            (claude_docs/09 §10.12)
│   ├── dar_terminus2_checks.py      Terminus2SolverChecks (AGENT=t2sc, 2026-11-17): the solver-checks
│   │                            harness. Rule document in the solver's first message when it is small,
│   │                            verifier = a list of check skills, each a sub-agent with one report
│   │                            format; the harness validates the shape, searches the rule document
│   │                            for every quote, merges the reports and gates task_complete; rebuttal,
│   │                            answer-format guard; logs under <trial>/agent/rubric_verifier/
│   │                            (claude_docs/10)
│   ├── skills/solver_v4/SKILL.md    solver skill of AGENT=t2sc: go through every top-level unit and
│   │                            every case fact, four-section answer file, correct or rebut a finding
│   ├── skills/check_report_v1/FORMAT.md  ground rules and report format shared by all checks (default);
│   │                            check_report_v2/ reads the case text as complete (10 §6.3), passed with
│   │                            --ak check_format_path=<abs path>
│   ├── skills/check_<name>_v1/SKILL.md  the checks: section_table, fact_table, grounded_reasoning
│   │                            (default list) and process (optional, reads the solver trajectory)
│   ├── templates/terminus-json-multiagent-v4.txt  solver template of AGENT=t2sc ({rule_document} slot
│   │                            before the skill)
│   ├── dar_terminus2_srvc.py        Terminus2SRVC (AGENT=t2srvc, 2026-11-20; "solver-reader-verifier-compaction
│   │                            terminus-2-harness v0.1"): the
│   │                            solver-reader-verifier-compaction harness v0.1. Reader survey before
│   │                            the first step; reader queries and compaction as branches of the
│   │                            solver's conversation (prefix read from the prompt cache); direct line
│   │                            ranges without a model call; "write_file" field; one verifier round
│   │                            of the rubric table with kept thinking; budget at the with-cache
│   │                            price; logs under <trial>/agent/{reader,compaction,write_file,
│   │                            rubric_verifier}/ (claude_docs/11 §9)
│   ├── dar_kira_srvc.py             KiraSRVC (AGENT=kirasrvc, 2026-11-20; "solver-reader-verifier-compaction
│   │                            terminus-kira-harness v0.1"): the same harness on KIRA's
│   │                            loop. Sub-agents and write_file are native tools; branches carry the
│   │                            tool definitions with tool_choice="none"; harness messages reworded
│   │                            from JSON fields to tool calls (claude_docs/11 §11)
│   ├── skills/solver_v5/SKILL.md    solver skill of AGENT=t2srvc (outline first, reader on request,
│   │                            write_file, one verifier round); solver_v5_tools/ = the same for
│   │                            tool calls (AGENT=kirasrvc)
│   ├── skills/reader_v2/SKILL.md    reader skill: survey (outline + passages) and query, line ranges only
│   ├── skills/compaction_v1/SKILL.md  compaction skill: the state page that replaces the middle steps
│   ├── skills/rubric_<name>_v1/SKILL.md  the rubric table: section_table, fact_table,
│   │                            grounded_reasoning; shared report format skills/rubric_report_v3/FORMAT.md
│   ├── templates/terminus-json-multiagent-v5.txt  solver template of AGENT=t2srvc;
│   │                            terminus-kira-srvc.txt = solver template of AGENT=kirasrvc
│   ├── sc_run_report.py             summary of a t2sc or t2srvc job: accuracy, finish reasons, rounds, check status
│   ├── sc_check_stats.py            what the checks of a t2sc job cost and tell apart: cap waste, rounds, FAIL on right vs wrong files, solver context (11 §2)
│   │                            by round, quote checks, repairs and fallbacks, tokens per check,
│   │                            first report vs reward; --trials, --findings (10 §4)
│   ├── pricing/price_card.json      the fixed price card of the inference-cost metric: USD per million
│   │                            input / output / cache-read tokens (0.14 / 1.00 / 0.05); next to it the
│   │                            OpenRouter responses it was read from, fetched 2026-11-11
│   ├── pricing/price_card_qwen3.8-27b.json  OpenRouter price of qwen/qwen3.8-27b fetched 2026-11-17
│   │                            (0.15 / 1.875 / 0.0375), for reporting only; the cost budget of a run
│   │                            always uses price_card.json (10 §7.3)
│   ├── inference_cost.py            inference cost of job dirs: tokens and cents per task and per role,
│   │                            without a cache and with a simulated prompt cache inside each trial,
│   │                            as a multiple of a baseline job; reads the ledger, or the debug logs
│   │                            and sub-agent trajectories of runs made before it (09 §10.12);
│   │                            --json wrote outputs/cost1_numeric_inference_cost.json (the three
│   │                            reruns of 2026-11-12, per trial and per role)
│   │                            and outputs/sc_run1_{numeric,binary}_inference_cost.json (10 §7);
│   │                            outputs/srvc_run1_{numeric,binary}_inference_cost.json and
│   │                            outputs/kirasrvc_smoke2_inference_cost.json (11 §10, §11);
│   │                            outputs/kirasrvc_run1_{numeric,binary}_inference_cost.json (11 §11.1);
│   │                            outputs/sc_run1_numeric_qwen38_27b_inference_cost.json (both models,
│   │                            Qwen3.5 card) and ..._own_card.json (Qwen3.8-27B price) (10 §7.3);
│   │                            outputs/db_direct_qwen35_inference_cost.json from db_direct_cost.py (10 §7.4)
│   ├── rv_run_report.py             aggregate numbers of a t2rv/t2rvc numeric run vs a baseline run:
│   │                            accuracy with intervals, paired comparison, time per role, time-limit
│   │                            fallbacks, compactions, what follows the first verifier report;
│   │                            --trials-json writes outputs/rvc_run1_numeric_trials.json (09 §10.10)
│   ├── rv_case_matrix.py            per-task matrix for error attribution: the four direct-prompt generations,
│   │                            the t2fix / run-3 / t2rv / t2rvc rewards and the t2rvc verifier rounds;
│   │                            needs harbor-deonticbench/.venv/bin/python (09 §10.11)
│   ├── replay_compactor.py          debugging aid: replays one compactor call on a finished t2rv trial
│   │                            (state right after verifier round R), live or with a canned result
│   ├── make_harness_figures.py      writes claude_docs/figures/{reader,compactor}.svg
│   ├── dar_terminus2_multiagent.py  Terminus2ReaderVerifier (AGENT=t2rv): run-3 agent whose solver
│   │                            calls reader/verifier via a "subagent" JSON field; task_complete
│   │                            accepted only for an answer-file hash the verifier saw; logs under
│   │                            <trial>/agent/reader/ and rubric_verifier/
│   ├── dar_kira_local.py            baseline_kira subclass (AGENT=kira): reshapes Qwen's
│   │                                non-conforming execute_commands tool arguments
│   ├── dar_trajectory_dossiers.py   per-trial dossiers of SARA-numeric trajectories (what
│   │                                statute lines the model saw, its reads, final reasoning);
│   │                                source of the error taxonomy in claude_docs/06 §1.5.1
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
│   ├── db_direct_cost.py            inference cost of a prompt-based direct run on the price card,
│   │                                estimated from the token totals in source.json (no new generations)
│   ├── deonticbench_stats.py        dataset statistics behind claude_docs/03 Part III
│   └── db_stats.json             ✅ output of deonticbench_stats.py (regenerable; committed since 2026-10-27)
│
├── outputs/                      ✅ our run artifacts outside the subtrees (committed since 2026-10-27)
│   └── verbalized_confidence/<run>/ per-trial JSON + summary.json from verbalized_confidence.py
│
├── DeonticBench/                 upstream subtree (prompt-based benchmark)
│   ├── data/*/whole.json         ⬜ fetched per machine from HuggingFace (too large for GitHub)
│   └── outputs/                  ✅ generate_e2e.py outputs (committed since 2026-10-27) (prompt-based direct runs)
│
└── harbor-deonticbench/          upstream subtree (DAR: Harbor + meta-harness)
    ├── datasets/                 ✏️✅ generated Harbor tasks, tracked here on purpose
    │                                (upstream gitignores them; see claude_docs/02 §5)
    ├── .gitignore                ✏️ the `/datasets/` line commented out, with a note
    └── jobs/qwen3.5-35b-a3b/     ⬜ Harbor job dirs: <framing>/, t2fix-<framing>/, kira-<framing>/, t2verify-<framing>/,
                                     t2rv-<framing>/, t2rvc-<framing>/ (tracked 2026-10-27..31 for the machine switch, ignored again since;
                                     the jobs up to rv-smoke1 are in history at commit 0dc69015)
```

Machine-local files that are not in the repository at all (0238): model weights under
`~/models/`, vLLM and run logs under `~/vllm_logs/`, the `myenv` conda environment, the Harbor
`uv` environment inside `harbor-deonticbench/.venv/`.

## 2. How the `tools/` pieces fit together

```
serve_qwen35_35b.sh ──► vLLM at :9009 ◄── run_dar_qwen35_35b.sh ──► harbor run ──► jobs/<...>/
                                   ▲            │ AGENT=t2        stock terminus-2
                                   │            │ AGENT=t2fix     dar_terminus2_local.py
                                   │            │ AGENT=t2verify* dar_terminus2_verifier.py (+ skills/rubric_verifier*, templates/)
                                   │            │ AGENT=t2rv      dar_terminus2_multiagent.py (+ skills/solver_v1, reader_v1, rubric_verifier_v4, templates/)
                                   │            │ AGENT=t2rvc     dar_terminus2_compactor.py (+ skills/solver_v3, compactor_v1, reader_v1, rubric_verifier_v4)
                                   │            │ AGENT=t2sc      dar_terminus2_checks.py (+ skills/solver_v4, check_report_v1|v2, check_*_v1, templates/)
                                   │            │ AGENT=t2srvc    dar_terminus2_srvc.py (+ skills/solver_v5, reader_v2, compaction_v1, rubric_*_v1, rubric_report_v3)
                                   │            │ AGENT=kirasrvc  dar_kira_srvc.py (the same skills, solver_v5_tools, templates/terminus-kira-srvc.txt)
                                   │            └ AGENT=kira      dar_kira_local.py
                                   │
          verbalized_confidence.py ┘ (reads jobs/<...>/, writes outputs/verbalized_confidence/)
          dar_job_summary.py         (reads jobs/<...>/, prints the tables in claude_docs/06)
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
- Small run summaries (`outputs/`, `DeonticBench/outputs/`) are committed since 2026-10-27. Full
  Harbor job dirs (`harbor-deonticbench/jobs/`), `bootstrap_results/`, `wandb/`, weights and `whole`
  splits are gitignored (jobs again since 2026-10-31, see `02` §4). The docs in `claude_docs/` carry the numbers.
- Add a line here, and a row in the `CLAUDE.md` table for docs, in the same commit that adds the file.
