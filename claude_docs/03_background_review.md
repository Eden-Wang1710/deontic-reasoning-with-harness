# 03 · Background Review: DeonticBench, DAR and the Data

Three close-reading notes merged into one file on 2026-10-28 (formerly `03_deonticbench_review.md`,
`04_dar_review.md` and `05_data_statistics.md`; each part keeps its own date and header). Section
numbers run through the whole file so that a reference such as `03` §6.3 is unambiguous:
Part I §1–4, Part II §5–9, Part III §10–17.

- **Part I · DeonticBench close reading** (paper + code + data) — §1–4
- **Part II · DAR / harbor-deonticbench close reading** — §5–9
- **Part III · Data statistics: DeonticBench and what DAR uses** — §10–17

---

## Part I · DeonticBench Close Reading (paper + code + data)

- **Date**: 2026-09-17
- **Subject**: paper [arXiv 2604.04443](https://arxiv.org/abs/2604.04443) (full text incl. appendices A–E);
  upstream `guangyaodou/DeonticBench` @ `3c168f9` (data as of the 2026-05-26 "audit fix");
  HF `gydou/DeonticBench` (lastModified 2026-06-04, CC-BY-4.0)
- **Not done**: no SWI-Prolog on the machine where this was written, so **the reference Prolog was
  never actually executed** — neither its runnability nor its agreement with the gold labels is verified here.
- **Confidence convention**: ✅ = checked directly against code / data / the paper's text.
  🔶 = inference from a small sample; needs verification.
- **Why this note exists**: this is the foundation the thesis builds on, so the failure modes below
  are entry points, not criticism. Directions derived from them live in
  [04_initial_proposal.md](04_initial_proposal.md); the agentic follow-up is Part II below.

## 1. What the work is

A rule-reasoning benchmark from JHU (Van Durme group): 6,232 items total, but the headline
evaluation uses only the **hard subsets** of 28–80 items per domain (251 items total).

| Subset | Task | Label | whole / hard | Statute |
|---|---|---|---|---|
| SARA Numeric | compute federal income tax | integer $ | 100 / 35 | globally shared (~6.1k tokens) |
| SARA Binary | tax-code entailment | 0 / 1 | 276 / 30 | same |
| Airline | baggage fee computation (from RuleArena) | integer $ | 300 / 80 | globally shared (~3.6k tokens) |
| Housing | state housing/eviction law yes/no QA | yes / no | 5,314 / 78 | per item |
| USCIS-AAO | immigration appeal outcome (new in this work) | Accepted / Dismissed | 242 / 28 | per item |

Three evaluation modes: `direct` (answer straight out), `zero-shot` (statute only, write Prolog),
`few-shot` (plus 1–2 Prolog exemplars). Prolog runs under SWI-Prolog with a 10 s timeout. Numeric
answers allow ±$1; binary tasks report macro-F1 with abstentions (unrunnable / unparseable) counted
as **the opposite class**. K = 3–4 samples per item, 1000 bootstrap resamples for 95% CIs.

**How `hard` was built**: o3 / GPT-5.2 / Claude Sonnet 4.5 each ran Prolog generation twice; any
failure made an item a candidate → iterative manual cleaning → some kept as the hard eval set, the
rest returned to the training pool.

**How the reference Prolog was built**: o3 (medium effort) generated a statute module plus a
per-item program. **Only programs that compiled, ran, and produced output equal to the gold label
were kept**; failures got one retry with compiler feedback, and the item was dropped after that.

**Training**: Qwen2.5-32B-Instruct, SFT (LoRA r=8, lr 1e-4, 3 epochs) → DPO (β=2.5, lr 7e-8;
rejected samples from the base model) → Dr.GRPO (verl, LoRA r=128, lr 2e-6, G=5,
**max response 1,024 tokens**). Reward: 1 if the output matches the label; **unrunnable** code gets
≤0.2 via Jaccard similarity of predicate signatures against the reference; runnable-but-wrong gets 0.

### Best hard-set scores (Tables 2 and 7)

| Domain | Best | Note |
|---|---|---|
| SARA Numeric (acc) | 44.4 (o3 zero-shot); GPT-5.2-Codex zero-shot 45.8 | the trained 32B scores <10 in every setting |
| Airline (acc) | 90.8 (o3 few-shot); Codex few-shot 95.5 | o3 zero-shot is only 18.5; best zero-shot is GPT-5.1 at 40.2 |
| SARA Binary (F1) | 70.0 (Claude Sonnet 4.5 direct) | direct generally beats Prolog |
| USCIS-AAO (F1) | 71.5 (GPT-5.1 direct) | same |
| Housing (F1) | 46.8 (GPT-5.1 few-shot) | hard set is 39 yes / 39 no, so <50 is below chance |

Minor ✅: the abstract's 44.4 / 46.6 do not match the tables' 45.8 (Table 7) / 46.8 (Table 2).

## 2. Key observations

### 2.1 The reference Prolog was written against the answer key ✅ (occurrence) / 🔶 (prevalence)

The generation pipeline kept only programs whose output equalled the gold label, so the selection
pressure is "right answer", not "right reasoning". Traces survive even after the 5/26 audit fix:

- **Housing hard[50]** (`housing_265bdba3`, West Virginia, label=no): a comment reading
  *"and the audit says the answer should be no"*, followed by a hardcoded `fail.`. Verified still
  present in the HF version (rows API, offset=50) on 2026-09-17.
- **Housing hard[33]** (Texas, label=yes): the rule is
  `refers_to(Law, appeal_bond), requires(Law, filing_within_10_days), refers_to(Law, stay_pending_appeal)`
  — keyword co-occurrence, not legal reasoning.
- **USCIS hard[08]** (`APR052022_01B5203`, Dismissed): asserts the AAO's **adjudicative conclusion**
  directly as a fact, e.g. `record_lacks_five_year_progressive_post_baccalaureate_experience.`
- A coarse regex scan of the hard sets (script in §4):

| Pattern | SARA-N | SARA-B | Airline | Housing | USCIS |
|---|---|---|---|---|---|
| comment mentions gold / audit / expected result / correct answer | 1/35 | 4/30 | 0/80 | 3/78 | 0/28 |
| bare `fail.` in a clause body | 4/35 | 3/30 | 0/80 | 11/78 | 2/28 |

  The four SARA Binary hits (indices 7, 26, 27, 29) open with comments like `Gold: 1 (Entailment)`.
  A "conclusion-shaped predicate" regex hits 12/28 on USCIS, but manual inspection shows many are
  ordinary facts (e.g. `parents_failed_to_provide_proper_food`), so **that number is not a leakage rate**.
- SARA Numeric programs do genuinely compute; most bare `fail.` there are case-specific pruning
  (e.g. `surviving_spouse_status(P,Y) :- fail.  % no such facts in this case`) — the legal judgement
  was made by the LLM outside the program and Prolog is just a calculator. Two items (indices 17, 19)
  still carry evaluation scaffolding that prints the gold label:
  `:- format("~w~n", ["Label: 81487"]).`
- Airline's references are the cleanest (0 hits).

**Why it matters**: (a) SFT/DPO use these programs as targets; (b) GRPO's predicate-overlap reward
is measured against them; (c) the paper's error attribution (Table 4: Housing 96.8% "Wrong Rule")
is computed by comparison with the reference — if the reference is post-hoc rationalisation, that
attribution has to be discounted.

### 2.2 Housing hard scores below chance; looks like a labelling-convention problem 🔶

- ✅ The hard set is a balanced 39/39. In `direct` mode every model lands at macro-F1 17.4–32.1
  (GPT-5.2 17.4, GPT-5.1 18.4, GPT-4.1 20.2, o3 20.8, Kimi 24.9, Qwen3 25.7, Gemini 30.2,
  Claude 32.1) — predictions are **systematically anti-correlated** with the labels.
- ✅ hard[33] (Texas) and hard[64] (Nevada) both ask whether an appeal stays the writ: the statute
  says it does *not*, **unless** a bond is posted — yet the label is yes. hard[3] (West Virginia)
  asks whether eviction cases are heard first in circuit court: the statute says
  "magistrate court **or** circuit court" — label yes.
- 🔶 Hypothesis: the labels inherit a source-database convention ("yes if there exists a path / if it
  is one of the options") from Zheng et al. 2025's Housing Statute QA (believed to derive from the
  LSC Eviction Laws Database, **unverified**), which conflicts with a literal reading of the
  question. Selecting `hard` by "frontier models got it wrong" then concentrates exactly these items.
  In other words, **hard ≠ deeper reasoning**.
- ✅ Corroborating: Table 1 shows Housing `hard` statutes are much *shorter* than `whole`
  (mean 588 vs 2,219 tokens).
- Sample size: ~10 items skimmed, 3 read closely. **This is a hypothesis.** To verify: manually
  annotate all 78 items with "the answer under a literal reading" and measure agreement with gold.

### 2.3 Airline few-shot exemplars come from the test set itself ✅

- `DeonticBench/scripts/generate_e2e.py:880`: `pool_path = config.airline_exemplar_pool or config.cases_path`;
  **no script** in `experiments/` or `example_scripts/` passes `--airline-exemplar-pool`.
- So each hard item receives, as its exemplar, **another hard item's reference program in the same
  cabin class** (leave-one-out, first match). The statute is globally shared → this hands the model
  a correct encoding of the rules.
- That is very likely what drives o3 from 18.5 (zero-shot) to 90.8 (few-shot): few-shot is measuring
  "fill facts into a template", not rule formalisation.
- 🔶 Whether the paper's own experiments used this same pool cannot be confirmed (a docstring
  mentions an "O3 correct Prolog pool", possibly from the training split).

### 2.4 The released scorer does not match the published metric ✅

- `DeonticBench/scripts/bootstrap_outputs.py` computes only accuracy / abstain rate / wrong rate for
  every domain; **there is no macro-F1 implementation anywhere in the released code**. The paper
  reports macro-F1 for the three binary domains (abstention → opposite class, appendix B.3), so
  **this script cannot reproduce those three columns of Table 2 as-is**.
- The abstention→opposite-class mapping explains some odd numbers: Claude Sonnet 4.5 `direct` scoring
  7.2 on USCIS is probably refusals / unparseable output rather than reasoning failure 🔶.
- The README defaults to `NUM_GENERATIONS=2`; the paper uses K=3/4.

### 2.5 On USCIS / Housing the symbolic solver barely reasons ✅

- The USCIS prompt requires yes/no-style predicates to be **zero-arity**, so reference programs are
  essentially propositional (`eligibility_met :- a, b, c.`). Housing's exemplar style is
  `refers_to(law, keyword)` keyword facts plus closed-world `\+`.
- The solver only does AND / NOT. The actual legal judgement — is the evidence sufficient, does the
  clause cover this — happens when **the LLM decides which facts to assert**, which is precisely the
  open-texture part of law.
- The symbolic route only adds real value on compositional-arithmetic tasks like SARA Numeric and
  Airline, consistent with `direct` winning on the binary domains.

### 2.6 Weak statistical power ✅

28–80 items per domain gives 95% CIs of roughly ±10–20 points; most model-to-model differences in
Table 2 are not significant. USCIS `hard` is also year-skewed: all 11 items from 2022 are in `hard`,
9 of them Dismissed.

### 2.7 Open questions about the training setup 🔶 (my analysis; not discussed in the paper)

- GRPO's max response is 1,024 tokens, while reference Prolog averages 945 (SARA-N whole) / 1,236
  (SARA-N hard) / 1,350 (Housing whole) tokens, and several prompts (SARA few-shot, SARA Binary,
  Airline few-shot) additionally ask the model to "think step-by-step" → many programs are probably
  truncated. That may be part of why SARA Numeric stays <10. The paper does not say which prompt set
  GRPO trained on.
- The reward gives ≤0.2 for "unrunnable but predicate names resemble the reference" and 0 for
  "runnable but wrong" — an odd incentive gradient.
- On binary tasks the reward looks only at the final label, so a program that prints a constant has
  expected reward 0.5. Reward hacking is not discussed.
- DPO at lr 7e-8 is extremely low; the limited gain may simply follow from that.
- Survivorship bias: Housing goes from 6,853 source items to 5,314 kept; the difference is very
  likely items where neither generation attempt produced Prolog matching the answer.

## 3. Where this leads

The thesis direction built on the above is in [04_initial_proposal.md](04_initial_proposal.md),
together with what DAR (Part II) already addresses and what it leaves open.

## 4. Reproducing the §2.1 scan

```bash
cd DeonticBench && python3 - <<'PY'
import json, re
pats = {
  "label-aware comment": re.compile(r"\b(audit|gold|ground[- ]truth|expected (answer|result|output)|answer should|should be (yes|no|accepted|dismissed)|correct answer)\b", re.I),
  "bare fail.":          re.compile(r"(^|\n)\s*fail\s*\.", re.I),
}
for d in ["sara_numeric", "sara_binary", "airline", "housing", "uscis-aao"]:
    data = json.load(open(f"data/{d}/hard.json"))
    for name, p in pats.items():
        hits = [i for i, x in enumerate(data) if p.search(x["reference_prolog"])]
        print(f"{d:13s} {name:20s} {len(hits):2d}/{len(data)}  {hits}")
PY
```

---

## Part II · DAR / harbor-deonticbench Close Reading

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

## 5. What DAR changes

DeonticBench (Part I) puts the whole statute in the prompt and takes one
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

## 6. What the code actually does ✅

### 6.1 Task framings

Three adapters under `adapters/`, each producing **173 tasks**:
sara_numeric 35 + sara_binary 30 + airline 80 + uscis-aao 28.

| Dataset | Agent must produce | Statute | Examples |
|---|---|---|---|
| `deonticbench-direct` | free-form answer at `/app/output/answer.txt`, one line `Answer: <value>` | `/app/statute.txt` | — |
| `deonticbench-zeroshot` | runnable SWI-Prolog at `/app/solution.pl` that prints `Answer: <value>` | `/app/statute.txt` | — |
| `deonticbench-fewshot` | same as zero-shot | `/app/statute.txt` | `/app/examples.txt` |

**Housing is gone.** DeonticBench has five subdomains; DAR uses four. Housing — the subdomain with
the below-chance scores and the suspected labelling problem (see §2.2) — is not in any adapter.
Whether the paper explains the exclusion is **unknown until the body is read** 🔶. Worth asking directly.

Per-task container: `python-3-13` base + `swi-prolog` + the Claude CLI (installed at image build
time from `https://claude.ai/install.sh`, i.e. a network dependency in the build). Limits from
`task.toml`: agent timeout **600 s**, verifier timeout 120 s, 1 CPU, 2048 MB RAM, 5120 MB disk.

### 6.2 The zero-shot prompt invites an execute–repair loop

`adapters/deonticbench_zeroshot/template/instruction.md` explicitly tells the agent:

> **Use `swipl` to verify your program as you go:** … Check the output for syntax errors or warnings.
> If any errors appear, fix them and re-run until the program executes cleanly.

So the "multi-turn agent loop with execution feedback" idea is **already implemented** — that is the
core of DAR, and any thesis direction has to start after it, not propose it.

### 6.3 Grading is exact match, and stricter than DeonticBench's ⚠️ ✅

Both `test.sh` templates greps the last `^Answer:` line, then:

- if the expected label is an integer: strip everything after `.` from the model's answer and compare
  **exactly** — so `$4,201` vs `4200` is wrong, and **there is no ±$1 tolerance**;
- otherwise: case-insensitive string compare.

DeonticBench's own protocol allows ±$1 on numeric answers (§1). **The two papers therefore
score SARA Numeric and Airline under different rules**, and numbers are not directly comparable
across them without re-scoring. This is a real comparability trap for any thesis table that wants to
put "prompt-based" and "agentic" side by side.

The Prolog verifier uses a **30 s** `timeout` on `swipl`, where DeonticBench used 10 s.

### 6.4 Scoring

`evaluate_deonticbench.py` (repo root, for non-Kira runs) and
`meta-harness/reference_examples/terminal_bench_2/evaluate_jobs.py` (for Kira runs) report:
accuracy for `sara-numeric` / `airline`, **macro-F1** for `sara-binary` / `uscis-aao`.

Notable, given §2.4: macro-F1 **is** implemented here — DeonticBench's own released scorer never
had it. If the thesis needs the prompt-based baselines re-scored with the published metric, this is
the code to port back.

Errored trials are handled two ways: by default they are dropped from F1; with
`--errors-as-incorrect` they become a false negative for the true class. Both scorers keep only the
**latest** run when a model appears more than once in a jobs-dir.

### 6.5 Agents and models in the run scripts

`deontic-scripts/` covers two families:

- **Non-Kira** (Harbor built-ins, run from the repo root): `terminus-2` (12 invocations),
  `codex` (3), `claude-code` (2).
- **Kira** (meta-harness, run from `meta-harness/reference_examples/terminal_bench_2`):
  `baseline_kira` (17), `kira_compute_then_answer` (2), `kira_deontic_grounded` (2).

Models referenced: `openai/gpt-5.1-2025-11-13`, `openai/gpt-5.2-2025-12-11`, `openai/o3-2025-04-16`,
`openrouter/anthropic/claude-sonnet-4.5`, `openrouter/moonshotai/kimi-k2-0905`,
`openrouter/qwen/qwen3-235b-a22b-2507`, `openrouter/qwen/qwen3.5-122b-a10b`, plus self-hosted vLLM
and Together-hosted Qwen.

## 7. Gaps in the released code ✅

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

## 8. Where this leaves the thesis

DAR already does the obvious agentic upgrade — tool access, on-demand statute reading, and an
execute-and-repair loop. What it does **not** resolve, and what therefore stays open:

- The reference-Prolog contamination from §2.1 is untouched: DAR changes how the model reaches
  an answer, not how the data was built. Few-shot exemplars still come from DeonticBench's reference
  programs.
- The §2.3 Airline exemplar leakage carries over into `deonticbench-fewshot` 🔶 — the adapter
  takes examples from upstream, so whatever pool upstream used is what gets mounted at
  `/app/examples.txt`. **Needs checking against `adapters/deonticbench_fewshot/adapter.py`.**
- Housing is dropped rather than fixed.
- Statistical power is unchanged and slightly worse: 173 items instead of 251.
- The abstract's own negative finding — weaker models degrade on numerical tasks while burning far
  more tokens — is a stated open problem, not a solved one.

The thesis direction built on these is in [04_initial_proposal.md](04_initial_proposal.md).

## 9. To do on the next pass

- [ ] Read the paper body: the harness list, the model list, the main table, and the stated reason
      for dropping Housing.
- [ ] Check `adapters/deonticbench_fewshot/adapter.py` for where `/app/examples.txt` comes from
      (does the Airline leakage from §2.3 survive into DAR?).
- [ ] Ask the author for `kira_deontic_grounded`, `kira_compute_then_answer` and the `cc-*` adapters.
- [ ] On a machine with Docker: run one task end to end and confirm the pipeline works before
      trusting any of the above operationally.

---

## Part III · Data Statistics: DeonticBench and What DAR Uses

- **Date**: 2026-10-14, computed on 0238. ✅ everything in this file was measured directly with
  `tools/deonticbench_stats.py` (DeonticBench side) and an ad-hoc pass over
  `harbor-deonticbench/datasets/` (DAR side), unless marked 🔶.
- **Tokens** are `o200k_base` (GPT-4o / o-series tokenizer). Qwen and Claude tokenizers differ by
  roughly ±10% 🔶; treat token numbers as order-of-magnitude.
- **Purpose**: know the sizes before designing experiments. For what the data *means* and its known
  problems, see Parts I and II.

## 10. Where the data lives

| What | Path | In git? |
|---|---|---|
| `hard` and `smoke` splits, 5 domains | `DeonticBench/data/<domain>/{hard,smoke}.json` | ✅ yes (vendored) |
| `whole` splits | `DeonticBench/data/<domain>/whole.json` | ❌ gitignored; `python DeonticBench/scripts/download_hf_data.py --splits whole` (HF `gydou/DeonticBench`, ~105 MB total, housing alone 84 MB) |
| Shared statutes for SARA (9 IRC sections) and Airline (1 policy file) | `DeonticBench/statutes/{sara,airline}/` | ✅ yes; **not** on HF |
| Few-shot exemplars (Prolog) | `DeonticBench/prompts/case_exemplars.py` | ✅ yes |
| DAR Harbor tasks, 3 framings × 173 | `harbor-deonticbench/datasets/deonticbench-{direct,zeroshot,fewshot}/<task>/` | ✅ yes (our deliberate deviation, see [02](02_setup_and_sync.md) §5) |

Record schema: `id, text, question, label, reference_prolog`, plus `statutes` (per case for
Housing and USCIS; one shared copy embedded in every record of `whole` for SARA/Airline), `state`
(Housing), `case_number` (USCIS). `hard` records for SARA/Airline carry **no** `statutes` field;
the scripts load `statutes/` from the repo instead.

Checks ✅:
- `hard ⊂ whole` holds for all five domains, by `id`; `smoke` = first 5 of `hard`.
- `reference_prolog` of every `hard` case is byte-identical to its copy in `whole`.
- The statutes embedded in `whole` are the same sections as `statutes/` in the repo, with trivial
  whitespace/joining differences (27,316 vs 27,172 chars for SARA; 19,612 vs 19,583 for Airline).

## 11. Case counts and labels

| Domain | smoke | hard | whole | Label | Label balance (hard → whole) |
|---|---:|---:|---:|---|---|
| SARA Numeric | 5 | 35 | 100 | integer $ | all distinct; `whole` has 5 cases with label 0 |
| SARA Binary | 5 | 30 | 276 | 0 / 1 | 16/14 → 138/138 |
| Airline | 5 | 80 | 300 | integer $ | nearly all distinct |
| Housing | 5 | 78 | 5,314 | yes / no | 39/39 → **1,775 yes / 3,539 no** (whole is 2:1 imbalanced; `smoke` is all yes) |
| USCIS-AAO | 5 | 28 | 242 | Accepted / Dismissed | 14/14 → 121/121 |
| **Total** | 25 | **251** | **6,232** | | |

## 12. Shared context: statutes and exemplars

| Text | Chars | Tokens |
|---|---:|---:|
| `statutes/sara/` (§1, §2, §63, §68, §151, §152, §3301, §3306, §7703) | 27,172 | **6,097** |
| `statutes/airline/section1_non_textual` | 19,583 | **3,597** |
| Exemplar `EXEMPLAR_SARA_V1` (SARA Numeric few-shot; DeonticBench prepends `DISCARDED_SARA_EXAMPLE`, 2,640 chars, as a second example) | 2,648 | ~740 |
| Exemplar `EXEMPLAR_SARA_BINARY` | 1,341 | ~410 |
| Exemplar `EXAMPLE_AIRLINE_2_ONLY` (DAR few-shot); DeonticBench few-shot instead retrieves another test case of the same cabin class | 4,091 | ~1,300 |
| Exemplar `EXAMPLE_USCIS_ONE_SHOT` | 4,703 | ~1,110 |
| Exemplar `EXAMPLE_LEGAL_IR_ONE_SHOT` (Housing) | 1,895 | ~530 |

## 13. Per-case field lengths (tokens, `hard` / `whole`)

| Domain | `text` (case facts) mean | `statutes` per case mean (max) | `reference_prolog` mean (max) |
|---|---|---|---|
| SARA Numeric | 88 / 81 | shared, 6.1k | 1,444 (5,381) / 1,166 |
| SARA Binary | 52 / 46 | shared, 6.1k | 642 (1,838) / 405 |
| Airline | 197 / 187 | shared, 3.6k | 1,031 (1,389) / 879 |
| Housing | — (no `text`; question 23) | 585 (3,769) / **2,205 (37,794)** | 698 / 1,353 |
| USCIS-AAO | 408 / 395 | 368 (726) / 434 (1,560) | 1,263 / 975 |

Housing `whole` is the outlier: median statute 1,011 tokens but a long tail up to 38k tokens.

## 14. DeonticBench prompt length per mode (what the LLM actually receives)

Full prompt as built by `scripts/generate_e2e.py`, mean tokens over `hard` (range in parentheses).
`direct` = natural-language answer; `zero-shot` = write Prolog from statute only; `few-shot` =
Prolog with exemplars. There is no system prompt beyond this text ✅.

| Domain (hard) | direct | zero-shot | few-shot |
|---|---:|---:|---:|
| SARA Numeric | 6,239 (6,183–6,417) | 6,350 | 7,984 |
| SARA Binary | 6,249 | 6,382 | 6,822 |
| Airline | 3,864 | 3,878 | 5,122 (4,936–5,327) |
| Housing | 639 (100–3,842) | 747 | 1,273 |
| USCIS-AAO | 810 (388–1,279) | 1,147 | 2,038 |

`whole` means are within a few percent of `hard` for every domain except Housing (direct 2,259,
few-shot 2,893, max 38.5k). Practical reading: SARA and Airline prompts are **dominated by the
shared statute** (97% of the prompt for SARA direct); the case itself is 50–200 tokens. Housing and
USCIS are short, case-specific prompts.

## 15. What DAR uses

DAR's adapters read **only the `hard` split**, hard-coded in
`harbor-deonticbench/adapters/deonticbench_direct/adapter.py` (`"data_file": "<domain>/hard.json"`) ✅,
and **drop Housing** entirely:

| Subdomain | Tasks per framing | Source file | Statute given to the agent |
|---|---:|---|---|
| sara-numeric | 35 | `sara_numeric/hard.json` | `/app/statute.txt` = the 9 SARA sections concatenated (6,104 tokens) |
| sara-binary | 30 | `sara_binary/hard.json` | same file |
| airline | 80 | `airline/hard.json` | `/app/statute.txt` = the airline policy (3,597 tokens) |
| uscis | 28 | `uscis-aao/hard.json` | `/app/statute.txt` = that case's embedded `statutes` (122–726 tokens) |
| **total** | **173** × 3 framings = **519 tasks** | | |

Each task directory holds `instruction.md` (the prompt), `environment/statute.txt`,
`environment/examples.txt` (few-shot only), a `Dockerfile` (python 3.13 base + `swi-prolog` +
the Claude Code installer), `task.toml`, and `tests/{test.sh,expected_label.txt}` (exact-match
grader, see §6.3).

**Context the agent starts with** is small; the statute is a file it must choose to read:

| Framing | Subdomain | `instruction.md` mean (range) | `statute.txt` | `examples.txt` | Sum if the agent reads everything |
|---|---|---:|---:|---:|---:|
| direct | sara-numeric | 214 (158–392) | 6,104 | — | 6,318 |
| direct | sara-binary | 203 | 6,104 | — | 6,307 |
| direct | airline | 340 | 3,597 | — | 3,937 |
| direct | uscis | 524 (284–905) | 368 | — | 891 |
| zeroshot | sara-numeric | 326 | 6,104 | — | 6,430 |
| zeroshot | sara-binary | 315 | 6,104 | — | 6,419 |
| zeroshot | airline | 452 | 3,597 | — | 4,049 |
| zeroshot | uscis | 636 | 368 | — | 1,003 |
| fewshot | sara-numeric | 359 | 6,104 | 743 | 7,206 |
| fewshot | sara-binary | 348 | 6,104 | 410 | 6,862 |
| fewshot | airline | 485 | 3,597 | 1,315 | 5,397 |
| fewshot | uscis | 669 | 368 | 1,113 | 2,149 |

On top of this the agent harness adds its own system prompt (`terminus-2`: ~2.9–3.4k chars,
roughly 700–850 tokens 🔶) and, per step, the terminal output it chose to produce (`cat
statute.txt` puts the whole statute into context at once; `grep` puts in only matches). So the
actual context of a DAR run is **trajectory-dependent** and grows over steps; the numbers above are
the floor and the "read everything once" level, not the per-call total. Token usage per run is
recorded in the trajectory `metrics` (Part II).

Differences from DeonticBench's prompts worth remembering ✅:
- DAR few-shot Airline uses the **fixed** exemplar `EXAMPLE_AIRLINE_2_ONLY`; DeonticBench few-shot
  Airline retrieves a same-cabin-class exemplar **from the evaluated cases file** (the
  test-set-leak noted in `CLAUDE.md`).
- DAR few-shot SARA Numeric uses `EXEMPLAR_SARA_V1` alone; DeonticBench prepends
  `DISCARDED_SARA_EXAMPLE` as well (two exemplars).
- DAR's grader: exact integer match / case-insensitive string; DeonticBench: ±$1 on numeric.

## 16. Implications for the thesis ([04](04_initial_proposal.md))

- **SARA via DAR is 65 cases** (35 numeric + 30 binary) per framing. Going to `whole` (100 + 276)
  requires generating Harbor tasks for it; the adapter would need a `data_file` switch and the
  `whole` statutes are already embedded per record, so this is mechanical 🔶.
- For the verifier, the inputs it must read are bounded: statute ≤ 6.1k tokens, case ≤ 300 tokens,
  reference program ≤ 5.4k tokens. The trajectory is the only unbounded part.
- Housing `whole` is the only large set (5.3k), but it is label-imbalanced, has the suspected
  label-convention problem (Part I), and DAR does not support it. It is
  not a drop-in auxiliary dataset.

## 17. Reproducing

```bash
# whole splits (gitignored) — once per machine
python DeonticBench/scripts/download_hf_data.py --splits whole
# DeonticBench side
python tools/deonticbench_stats.py DeonticBench/        # writes tools/db_stats.json (gitignored)
```

The DAR-side table was produced by tokenising `instruction.md`, `environment/statute.txt` and
`environment/examples.txt` of every task under `harbor-deonticbench/datasets/`.
