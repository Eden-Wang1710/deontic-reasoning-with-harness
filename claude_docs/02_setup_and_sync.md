# 02 · Setup and Sync (Lab Cluster)

- **Date**: 2026-10-14. Rewritten from scratch; the earlier version (work Mac / personal Mac /
  "JHU cluster" TBD) is obsolete and survives only in git history.
- **Scope**: all work now happens on **two lab cluster machines**. No laptops are involved.
- ✅ = checked on the machine on the date given. 🔶 = not yet checked.

## 1. Machines

| Name | Hardware | Role | Status |
|---|---|---|---|
| **0238** (`spark-0238`) | NVIDIA DGX Spark: 1× GB10 (Grace Blackwell), 20 CPU cores, 119 GB unified memory, 3.7 TB NVMe, aarch64 ✅ | Docker available → runs the DAR harness (Harbor tasks in containers); small-model serving; all docs and code | Partially set up, see §2 |
| **601-606** | 4× A100 + 48× RTX 6000 🔶 (as described by the owner; verify on login) | GPU work: hosting Qwen3.5-35B-A3B with hidden-state access, probe training, distillation (see [05](05_confidence_proposal.md) §4.2, §5) | **Not yet set up.** Log in there, run Claude Code from the repo, and fill in §3 from that machine. |

Both machines clone the same GitHub repo. **The repo is the only shared state**: commit and push
on one machine before switching to the other; `git pull` first thing on the other. Nothing else is
synchronised automatically.

## 2. 0238 (DGX Spark) — current state ✅ 2026-10-14

| Item | State | Action needed |
|---|---|---|
| OS | Ubuntu 24.04.3 LTS, aarch64 (ARM) | Note: ARM. Docker images and Python wheels must be `arm64`; check Harbor's task images build/pull on ARM 🔶. |
| GPU | GB10, driver 580.82.09, CUDA 13.0 toolkit at `/usr/local/cuda` | — |
| Docker | 28.3.3; user added to `docker` group ✅ 2026-10-15. In a shell opened before that, use `sg docker -c "<cmd>"`; new logins get it directly. Base image `ghcr.io/laude-institute/t-bench/python-3-13:20250620` has an `arm64` manifest and a DAR task image builds and runs here ✅ (swipl 9.0.4, Python 3.13.1, tmux). | — |
| Python | miniconda3 at `~/miniconda3`, base = 3.13.11; env `myenv` (Python 3.12) has **vllm 0.20.1, torch 2.11.0+cu130, transformers 5.8.0, flashinfer 0.6.8** and CUDA works on the GB10 ✅ 2026-10-15. Used for model serving (`tools/serve_qwen35_35b.sh`). | A separate DeonticBench env is still to do, see §2.1 |
| `uv` | 0.13.1 at `~/.local/bin/uv` ✅ 2026-10-15; `harbor-deonticbench` synced (`uv sync --all-extras --dev`), `uv run harbor` works | — |
| `swipl` | ❌ not on the host; **not needed for Harbor runs** (it is inside the task image) | Install into the project env only for DeonticBench's own Prolog eval |
| `torch` | in `myenv` (see Python row) | — |
| `gh` | ❌ not installed | Optional |
| git identity | **not set** (`user.name` / `user.email` empty) | `git config --global user.name "Zixuan Wang"` and `git config --global user.email <email>` before the first commit |
| git remote | `https://github.com/Eden-Wang1710/deontic-reasoning-with-harness`; no credential helper configured | Pushing needs either a PAT with a credential helper, or switch the remote to SSH and add this machine's key to GitHub. **Do this before the first push.** |
| Repo path | `/home/blab-0238/Desktop/workspace/zwang544/deontic-reasoning-with-harness` | — |
| Model weights | `~/models/Qwen3.5-35B-A3B` (bf16, 72 GB) ✅ 2026-10-15; vLLM logs in `~/vllm_logs/` | Download others with `hf download <repo> --local-dir ~/models/<name>` (from `myenv`) |

### 2.1 Project environment on 0238 (to do)

```bash
# from the repo root
conda create -n deontic python=3.12 -y && conda activate deontic     # Harbor needs ≥3.12
conda install -c conda-forge swi-prolog -y                           # `swipl` for both repos
grep -v '^vllm' DeonticBench/requirements.txt | pip install -r /dev/stdin   # DeonticBench deps (skip vllm unless serving here)

# Harbor side
cd harbor-deonticbench && uv sync --all-extras --dev && cd ..
```

Upstream's README differs from this repo in two places (still true ✅):

- No `git submodule update` is needed: `harbor-deonticbench/meta-harness/` is committed as plain
  files; there is no `.gitmodules`.
- `harbor-deonticbench/datasets/` does not need generating: we track it in git (§5).

Update the table in §2 once each item is done.

## 3. 601-606 — to be written from that machine

Placeholder. On first login, record here with ✅ and a date:

- hostname(s), OS and architecture (x86_64 expected 🔶), login path (direct SSH or via a jump host,
  Slurm or not)
- GPUs actually visible (`nvidia-smi`), driver and CUDA version
- whether Docker (or Podman/Apptainer) is available without sudo — decides whether the DAR harness
  can run here or only on 0238
- Python / conda / `uv` / `swipl` availability
- scratch and home disk quotas, where model weights should live
- git identity and push auth, same checks as §2
- how to serve Qwen3.5-35B-A3B with hidden-state access (vLLM vs. plain `transformers`) 🔶

Then update the machine table in §1.

## 4. Sync workflow

1. `git pull` when starting on either machine.
2. Work. Keep our own code **outside** `DeonticBench/` and `harbor-deonticbench/` (subtree rule in
   `CLAUDE.md`).
3. Commit and `git push` when stopping, **before** switching machines. Small, frequent commits.
4. If both machines diverge, `git pull --rebase` on the later one; the docs are plain markdown and
   conflicts are easy to resolve.

**Not in git**, by design (regenerate or copy out of band with `rsync`/`scp` between the two
machines when needed):

- run artifacts: `outputs/`, `bootstrap_results/`, `wandb/`, `*.log`, `harbor-deonticbench/jobs/`
- model weights: `*.ckpt`, `*.safetensors`, `*.pt`, `*.bin`
- `DeonticBench/data/*/whole.json` (re-download: `python DeonticBench/scripts/download_hf_data.py --splits whole`)

**API keys** (`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `TOGETHER_API_KEY`, `ANTHROPIC_API_KEY`) are
read from environment variables only. Put them in the shell profile on each machine; never in a
file in the repo (`.env*` is gitignored anyway).

## 5. The `datasets/` deviation

`harbor-deonticbench/datasets/` holds the Harbor tasks: 3 framings × 173 tasks = 519 tasks, ~21 MB
of text. Upstream gitignores it and regenerates it from a DeonticBench checkout. **We track it in
git** so both machines evaluate byte-identical tasks. The `/datasets/` line in
`harbor-deonticbench/.gitignore` is commented out with a note; expect a conflict there on
`git subtree pull` and keep our version.

Regenerate only after `DeonticBench/` changes or to add a subdomain:

```bash
cd harbor-deonticbench
export DEONTICBENCH_ROOT="$(cd ../DeonticBench && pwd)"
for m in direct zeroshot fewshot; do
  (cd adapters/deonticbench_$m && uv run python run_adapter.py \
      --deonticbench-root "$DEONTICBENCH_ROOT" \
      --output-dir ../../datasets/deonticbench-$m --clean)
done
```

The adapters need only `jinja2` on Python ≥3.9. Flags: `--subdomains sara_numeric sara_binary
airline uscis-aao`, `--limit N`, `--task-ids ...`, `--clean`. Full commands:
`harbor-deonticbench/deontic_adapters_scripts.txt`; per-agent run commands:
`harbor-deonticbench/deontic-scripts/`.

## 6. Pulling upstream updates

Both vendored directories are squashed git subtrees tracking upstream `main`. Run from the
**repo root** with a clean tree:

```bash
git subtree pull --prefix DeonticBench https://github.com/guangyaodou/DeonticBench main --squash
git subtree pull --prefix harbor-deonticbench https://github.com/guangyaodou/harbor-deonticbench main --squash
```

- Local edits inside the subtrees survive; ordinary merge conflicts can appear (known:
  `harbor-deonticbench/.gitignore`).
- Never `git clone` inside a subtree directory; never convert one to a submodule.
- HuggingFace data can be newer than the GitHub repo; re-run `download_hf_data.py` for the latest
  `hard` splits.
