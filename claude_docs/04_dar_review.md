# 04 · DAR / harbor-deonticbench Close Reading

- **Date**: 2026-09-17
- **Subject**: paper [arXiv 2606.05009](https://arxiv.org/abs/2606.05009) —
  *DAR: Deontic Reasoning with Agentic Harnesses*, Guangyao Dou, William Jurayj, Nils Holzenberger,
  Benjamin Van Durme, submitted 2026-06-03; upstream `guangyaodou/harbor-deonticbench` @ `2fe3aa4`
  (vendored here as a subtree on 2026-09-17)
- **Read so far**: title / authors / abstract, the repo README, and the deontic-specific code
  (`adapters/deonticbench_*`, `deontic-scripts/`, `evaluate_deonticbench.py`, `meta-harness/`).
  **The paper body is not yet read** — no result from any table is recorded below, and nothing here
  should be cited as "the paper says" beyond the abstract.
- **Also not done**: nothing has been *run*. No Docker on this machine (see `02`), so no task has
  ever been executed and no number reproduced.
- **Confidence convention**: ✅ = checked directly against the vendored code. 🔶 = inference.

## 1. What DAR changes

DeonticBench ([03](03_deonticbench_review.md)) puts the whole statute in the prompt and takes one
shot at an answer. The abstract's framing of the problem: the ruleset is long and cross-referenced,
so a model reasoning over a fixed prompt "may still fail to locate the rules needed for a particular
reasoning step".

DAR makes it agentic — the statute is a **file** in a container and the model interacts with it on
demand, using general-purpose tools (read, search, write code, execute). Verbatim from the abstract,
the headline result is that agentic harnesses "can push the frontier on deontic reasoning tasks, but
improvements are not uniform: weaker models often degrade on numerical tasks while consuming far
more tokens."

The implementation is a fork of [Harbor](https://github.com/harbor-framework/harbor) (0.3.0,
Apache-2.0) plus [meta-harness](https://github.com/stanford-iris-lab/meta-harness), with adapters
that repackage DeonticBench as Harbor tasks.

## 2. What the code actually does ✅

### 2.1 Task framings

Three adapters under `adapters/`, each producing **173 tasks**:
sara_numeric 35 + sara_binary 30 + airline 80 + uscis-aao 28.

| Dataset | Agent must produce | Statute | Examples |
|---|---|---|---|
| `deonticbench-direct` | free-form answer at `/app/output/answer.txt`, one line `Answer: <value>` | `/app/statute.txt` | — |
| `deonticbench-zeroshot` | runnable SWI-Prolog at `/app/solution.pl` that prints `Answer: <value>` | `/app/statute.txt` | — |
| `deonticbench-fewshot` | same as zero-shot | `/app/statute.txt` | `/app/examples.txt` |

**Housing is gone.** DeonticBench has five subdomains; DAR uses four. Housing — the subdomain with
the below-chance scores and the suspected labelling problem (see `03` §2.2) — is not in any adapter.
Whether the paper explains the exclusion is **unknown until the body is read** 🔶. Worth asking directly.

Per-task container: `python-3-13` base + `swi-prolog` + the Claude CLI (installed at image build
time from `https://claude.ai/install.sh`, i.e. a network dependency in the build). Limits from
`task.toml`: agent timeout **600 s**, verifier timeout 120 s, 1 CPU, 2048 MB RAM, 5120 MB disk.

### 2.2 The zero-shot prompt invites an execute–repair loop

`adapters/deonticbench_zeroshot/template/instruction.md` explicitly tells the agent:

> **Use `swipl` to verify your program as you go:** … Check the output for syntax errors or warnings.
> If any errors appear, fix them and re-run until the program executes cleanly.

So the "multi-turn agent loop with execution feedback" idea is **already implemented** — that is the
core of DAR, and any thesis direction has to start after it, not propose it.

### 2.3 Grading is exact match, and stricter than DeonticBench's ⚠️ ✅

Both `test.sh` templates greps the last `^Answer:` line, then:

- if the expected label is an integer: strip everything after `.` from the model's answer and compare
  **exactly** — so `$4,201` vs `4200` is wrong, and **there is no ±$1 tolerance**;
- otherwise: case-insensitive string compare.

DeonticBench's own protocol allows ±$1 on numeric answers (`03` §1). **The two papers therefore
score SARA Numeric and Airline under different rules**, and numbers are not directly comparable
across them without re-scoring. This is a real comparability trap for any thesis table that wants to
put "prompt-based" and "agentic" side by side.

The Prolog verifier uses a **30 s** `timeout` on `swipl`, where DeonticBench used 10 s.

### 2.4 Scoring

`evaluate_deonticbench.py` (repo root, for non-Kira runs) and
`meta-harness/reference_examples/terminal_bench_2/evaluate_jobs.py` (for Kira runs) report:
accuracy for `sara-numeric` / `airline`, **macro-F1** for `sara-binary` / `uscis-aao`.

Notable, given `03` §2.4: macro-F1 **is** implemented here — DeonticBench's own released scorer never
had it. If the thesis needs the prompt-based baselines re-scored with the published metric, this is
the code to port back.

Errored trials are handled two ways: by default they are dropped from F1; with
`--errors-as-incorrect` they become a false negative for the true class. Both scorers keep only the
**latest** run when a model appears more than once in a jobs-dir.

### 2.5 Agents and models in the run scripts

`deontic-scripts/` covers two families:

- **Non-Kira** (Harbor built-ins, run from the repo root): `terminus-2` (12 invocations),
  `codex` (3), `claude-code` (2).
- **Kira** (meta-harness, run from `meta-harness/reference_examples/terminal_bench_2`):
  `baseline_kira` (17), `kira_compute_then_answer` (2), `kira_deontic_grounded` (2).

Models referenced: `openai/gpt-5.1-2025-11-13`, `openai/gpt-5.2-2025-12-11`, `openai/o3-2025-04-16`,
`openrouter/anthropic/claude-sonnet-4.5`, `openrouter/moonshotai/kimi-k2-0905`,
`openrouter/qwen/qwen3-235b-a22b-2507`, `openrouter/qwen/qwen3.5-122b-a10b`, plus self-hosted vLLM
and Together-hosted Qwen.

## 3. Gaps in the released code ✅

Three things are referenced but not shipped. All are cheap to resolve by asking the author.

1. **`kira_deontic_grounded` and `kira_compute_then_answer` do not exist.**
   `meta-harness/reference_examples/terminal_bench_2/agents/` contains only `baseline_kira.py` and
   `baseline_terminus2.py`. The two names appear in `README.md`,
   `deontic-scripts/kira_gpt_run_commands.txt` and `deontic-scripts/kira_openrouter_run_commands.txt`,
   but `find` turns up no implementation anywhere in the repo. These are the **deontic-specific**
   agents — i.e. the most interesting part of the harness contribution — so this is the single most
   important thing to obtain.
2. **`deonticbench-cc-*` datasets cannot be generated.** `README.md:50` and
   `deontic-scripts/vllm_run_commands.txt` (lines 39, 71) use `datasets/deonticbench-cc-direct`, but
   there is no `adapters/deonticbench_cc_*` and `deontic_adapters_scripts.txt` never mentions them.
3. **`meta-harness/` is documented as a submodule but committed as plain files** (no `.gitmodules`).
   Harmless — noted so the README's `--recurse-submodules` step is not chased.

## 4. Where this leaves the thesis

DAR already does the obvious agentic upgrade — tool access, on-demand statute reading, and an
execute-and-repair loop. What it does **not** resolve, and what therefore stays open:

- The reference-Prolog contamination from `03` §2.1 is untouched: DAR changes how the model reaches
  an answer, not how the data was built. Few-shot exemplars still come from DeonticBench's reference
  programs.
- The `03` §2.3 Airline exemplar leakage carries over into `deonticbench-fewshot` 🔶 — the adapter
  takes examples from upstream, so whatever pool upstream used is what gets mounted at
  `/app/examples.txt`. **Needs checking against `adapters/deonticbench_fewshot/adapter.py`.**
- Housing is dropped rather than fixed.
- Statistical power is unchanged and slightly worse: 173 items instead of 251.
- The abstract's own negative finding — weaker models degrade on numerical tasks while burning far
  more tokens — is a stated open problem, not a solved one.

The thesis direction built on these is in [06_initial_proposal.md](06_initial_proposal.md).

## 5. To do on the next pass

- [ ] Read the paper body: the harness list, the model list, the main table, and the stated reason
      for dropping Housing.
- [ ] Check `adapters/deonticbench_fewshot/adapter.py` for where `/app/examples.txt` comes from
      (does the Airline leakage from `03` §2.3 survive into DAR?).
- [ ] Ask the author for `kira_deontic_grounded`, `kira_compute_then_answer` and the `cc-*` adapters.
- [ ] On a machine with Docker: run one task end to end and confirm the pipeline works before
      trusting any of the above operationally.
