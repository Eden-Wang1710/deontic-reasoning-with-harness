# 06 · Data Statistics: DeonticBench and What DAR Uses

- **Date**: 2026-10-14, computed on 0238. ✅ everything in this file was measured directly with
  `tools/deonticbench_stats.py` (DeonticBench side) and an ad-hoc pass over
  `harbor-deonticbench/datasets/` (DAR side), unless marked 🔶.
- **Tokens** are `o200k_base` (GPT-4o / o-series tokenizer). Qwen and Claude tokenizers differ by
  roughly ±10% 🔶; treat token numbers as order-of-magnitude.
- **Purpose**: know the sizes before designing experiments. For what the data *means* and its known
  problems, see [03](03_deonticbench_review.md); for the harness, [04](04_dar_review.md).

## 1. Where the data lives

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

## 2. Case counts and labels

| Domain | smoke | hard | whole | Label | Label balance (hard → whole) |
|---|---:|---:|---:|---|---|
| SARA Numeric | 5 | 35 | 100 | integer $ | all distinct; `whole` has 5 cases with label 0 |
| SARA Binary | 5 | 30 | 276 | 0 / 1 | 16/14 → 138/138 |
| Airline | 5 | 80 | 300 | integer $ | nearly all distinct |
| Housing | 5 | 78 | 5,314 | yes / no | 39/39 → **1,775 yes / 3,539 no** (whole is 2:1 imbalanced; `smoke` is all yes) |
| USCIS-AAO | 5 | 28 | 242 | Accepted / Dismissed | 14/14 → 121/121 |
| **Total** | 25 | **251** | **6,232** | | |

## 3. Shared context: statutes and exemplars

| Text | Chars | Tokens |
|---|---:|---:|
| `statutes/sara/` (§1, §2, §63, §68, §151, §152, §3301, §3306, §7703) | 27,172 | **6,097** |
| `statutes/airline/section1_non_textual` | 19,583 | **3,597** |
| Exemplar `EXEMPLAR_SARA_V1` (SARA Numeric few-shot; DeonticBench prepends `DISCARDED_SARA_EXAMPLE`, 2,640 chars, as a second example) | 2,648 | ~740 |
| Exemplar `EXEMPLAR_SARA_BINARY` | 1,341 | ~410 |
| Exemplar `EXAMPLE_AIRLINE_2_ONLY` (DAR few-shot); DeonticBench few-shot instead retrieves another test case of the same cabin class | 4,091 | ~1,300 |
| Exemplar `EXAMPLE_USCIS_ONE_SHOT` | 4,703 | ~1,110 |
| Exemplar `EXAMPLE_LEGAL_IR_ONE_SHOT` (Housing) | 1,895 | ~530 |

## 4. Per-case field lengths (tokens, `hard` / `whole`)

| Domain | `text` (case facts) mean | `statutes` per case mean (max) | `reference_prolog` mean (max) |
|---|---|---|---|
| SARA Numeric | 88 / 81 | shared, 6.1k | 1,444 (5,381) / 1,166 |
| SARA Binary | 52 / 46 | shared, 6.1k | 642 (1,838) / 405 |
| Airline | 197 / 187 | shared, 3.6k | 1,031 (1,389) / 879 |
| Housing | — (no `text`; question 23) | 585 (3,769) / **2,205 (37,794)** | 698 / 1,353 |
| USCIS-AAO | 408 / 395 | 368 (726) / 434 (1,560) | 1,263 / 975 |

Housing `whole` is the outlier: median statute 1,011 tokens but a long tail up to 38k tokens.

## 5. DeonticBench prompt length per mode (what the LLM actually receives)

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

## 6. What DAR uses

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
grader, see [04](04_dar_review.md) §2.3).

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
recorded in the trajectory `metrics` ([04](04_dar_review.md)).

Differences from DeonticBench's prompts worth remembering ✅:
- DAR few-shot Airline uses the **fixed** exemplar `EXAMPLE_AIRLINE_2_ONLY`; DeonticBench few-shot
  Airline retrieves a same-cabin-class exemplar **from the evaluated cases file** (the
  test-set-leak noted in `CLAUDE.md`).
- DAR few-shot SARA Numeric uses `EXEMPLAR_SARA_V1` alone; DeonticBench prepends
  `DISCARDED_SARA_EXAMPLE` as well (two exemplars).
- DAR's grader: exact integer match / case-insensitive string; DeonticBench: ±$1 on numeric.

## 7. Implications for the thesis ([05](05_confidence_proposal.md))

- **SARA via DAR is 65 cases** (35 numeric + 30 binary) per framing. Going to `whole` (100 + 276)
  requires generating Harbor tasks for it; the adapter would need a `data_file` switch and the
  `whole` statutes are already embedded per record, so this is mechanical 🔶.
- For the verifier, the inputs it must read are bounded: statute ≤ 6.1k tokens, case ≤ 300 tokens,
  reference program ≤ 5.4k tokens. The trajectory is the only unbounded part.
- Housing `whole` is the only large set (5.3k), but it is label-imbalanced, has the suspected
  label-convention problem ([03](03_deonticbench_review.md)), and DAR does not support it. It is
  not a drop-in auxiliary dataset.

## 8. Reproducing

```bash
# whole splits (gitignored) — once per machine
python DeonticBench/scripts/download_hf_data.py --splits whole
# DeonticBench side
python tools/deonticbench_stats.py DeonticBench/        # writes tools/db_stats.json (gitignored)
```

The DAR-side table was produced by tokenising `instruction.md`, `environment/statute.txt` and
`environment/examples.txt` of every task under `harbor-deonticbench/datasets/`.
