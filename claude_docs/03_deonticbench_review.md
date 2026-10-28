# 03 · DeonticBench Close Reading (paper + code + data)

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
  [06_initial_proposal.md](06_initial_proposal.md); the agentic follow-up is in
  [04_dar_review.md](04_dar_review.md).

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
- **USCIS hard[10]** (`APR052022_01B5203`, Dismissed): asserts the AAO's **adjudicative conclusion**
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

The thesis direction built on the above is in [06_initial_proposal.md](06_initial_proposal.md),
together with what DAR ([04](04_dar_review.md)) already addresses and what it leaves open.

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
