# 02 · Setup and Sync (Lab Cluster)

- **Date**: 2026-10-14. Rewritten from scratch; the earlier version (work Mac / personal Mac /
  "JHU cluster" TBD) is obsolete and survives only in git history.
- **Scope**: all work now happens on **lab cluster machines**: the DGX Spark 0238 (under the personal
  account `zixuan0238` since 2026-10-29) and the BRTX GPU nodes. No laptops are involved.
- A second DGX Spark (`spark-029b`) was used on 2026-10-28 only and was **returned to the professor on
  2026-10-30. Do not use it.** Its setup notes were removed from this doc (git history before
  2026-10-30 has them); a few run artifacts under `harbor-deonticbench/jobs/` still mention it.
- ✅ = checked on the machine on the date given. 🔶 = not yet checked.

## 1. Machines

| Name | Hardware | Role | Status |
|---|---|---|---|
| **0238** (`spark-0238`) | NVIDIA DGX Spark: 1× GB10 (Grace Blackwell), 20 CPU cores, 119 GB unified memory, 3.7 TB NVMe, aarch64 ✅ | Docker available → runs the DAR harness (Harbor tasks in containers); small-model serving; all docs and code | Set up under `zixuan0238` 2026-10-29, see §2 |
| **BRTX** (`brtx601`–`brtx606.idies.jhu.edu`) | 6 Slurm nodes, x86_64, RHEL 9.8. **602**: 4× A100 80GB PCIe, 2× Xeon Silver 4214 (24 threads), 754 GB RAM ✅ 2026-10-23. 601: 8× Quadro RTX 6000 24GB; 603–606: 10× RTX 6000 each (48 total) ✅ from `sinfo`. Every node: 2× 3.5 TB NVMe | GPU work on **602** (`ba100` partition): hosting Qwen3.5-35B-A3B with hidden-state access, probe training, distillation (see [04](04_initial_proposal.md) §4.2, §5). RTX 6000 nodes for small jobs. **No Docker for us** → DAR harness runs on 0238 only | Account works, repo cloned; **no Python env yet, local disks full**, see §3 |

All machines clone the same GitHub repo. **The repo is the only shared state**: commit and push
on one machine before switching to another; `git pull` first thing on the other. Nothing else is
synchronised automatically.

## 2. 0238 (DGX Spark) — current state ✅ 2026-10-29 (account `zixuan0238`)

Since 2026-10-29 work on 0238 happens under the personal account **`zixuan0238`** (previously the
shared lab account `blab-0238`, whose home is not readable by other users). The old account still
exists and, as of 2026-10-29, still runs its own vLLM server on port 9009 (see the vLLM row). The
earlier `blab-0238` table (2026-10-14/15) is in git history; its facts that still apply are kept below.

| Item | State | Action needed |
|---|---|---|
| OS / GPU | Ubuntu 24.04.3 LTS, aarch64 (ARM); GB10, driver 580.82.09, CUDA 13.0 toolkit at `/usr/local/cuda` (`nvcc` on PATH) ✅ | Docker images and Python wheels must be `arm64`. |
| Docker | 28.3.3, daemon active; `zixuan0238` added to the `docker` group ✅ 2026-10-29 (`sudo usermod -aG docker zixuan0238`). In a shell opened before that, use `sg docker -c "<cmd>"`; new logins get it directly. The DAR task image builds and runs on this host (verified under `blab-0238`, 2026-10-15). | — |
| Python | miniconda3 at `~/miniconda3` (conda 26.9.4). Installed with `-b`, **`conda init bash` not run** → call binaries by path (`~/miniconda3/envs/<env>/bin/...`, as `tools/serve_qwen35_35b.sh` does). **Anaconda's default channels need a ToS acceptance on this conda version**; both envs were created with `--override-channels -c conda-forge` instead, which needs none. | `~/miniconda3/bin/conda init bash` if the interactive `conda activate` is wanted |
| `myenv` (serving) | Python 3.12 (conda-forge); **vllm 0.20.1, torch 2.11.0+cu130, transformers 5.8.0, flashinfer 0.6.8.post1, ninja**. `pip install vllm==0.20.1` pulled the cu130 torch by itself; transformers was then upgraded to 5.8.0 (vLLM pins 4.57.6; the `xgrammar` warning about `transformers<5` is ignored, the import works). `torch.cuda.is_available()` → True on the GB10 ✅. | — |
| `deontic` (DeonticBench) | Python 3.12 (conda-forge) with `DeonticBench/requirements.txt` minus `vllm` ✅. No `swipl`: conda-forge has no `linux-aarch64` build and this host has no `/usr/bin/swipl`. Not needed for Harbor runs (it is inside the task image). | `sudo apt install swi-prolog` only if DeonticBench's own Prolog eval must run here |
| `uv` | 0.13.6 at `~/.local/bin/uv` ✅; `harbor-deonticbench` synced (`uv sync --all-extras --dev`), `uv run harbor` works ✅. `hf` CLI 2.4.0 via `uv tool install huggingface_hub` (`~/.local/bin/hf`). | — |
| `swipl` / `tmux` | `swipl` ❌ (see above); `/usr/bin/tmux` ✅ | — |
| `gh` | ❌ not installed | Optional |
| git identity | `user.name "Zixuan Wang"`, `user.email zixuanw712@gmail.com` set globally ✅ 2026-10-29 | — |
| git remote | SSH, `git@github.com:Eden-Wang1710/deontic-reasoning-with-harness.git`, with an ed25519 key (`~/.ssh/id_ed25519`) registered on GitHub ✅ 2026-10-29; `ssh -T git@github.com` authenticates. | — |
| Repo path | `/home/zixuan0238/deontic-reasoning-with-harness` (cloned 2026-10-29 at `a53d6509`) | — |
| Model weights | `~/models/Qwen3.5-35B-A3B` is a **symlink to `/home/blab-0238/models/Qwen3.5-35B-A3B`** (bf16, 14 safetensors shards, 67 GiB), shared read-only through a POSIX ACL (`setfacl -m u:zixuan0238:x /home/blab-0238; setfacl -R -m u:zixuan0238:rX /home/blab-0238/models`) set from the `blab-0238` account on 2026-10-29; all shards verified readable ✅. vLLM logs in `~/vllm_logs/`. | New models: `hf download <repo> --local-dir ~/models/<name>`, or ask for them under the shared path |
| vLLM serving | `tools/serve_qwen35_35b.sh` works unchanged from this account ✅ 2026-10-29 (started with `setsid nohup`, log `~/vllm_logs/qwen35-35b-a3b.log`): `/v1/models` lists `qwen3.5-35b-a3b`, a chat request returns the answer in `content` and the thinking in `message.reasoning`, and `http://172.17.0.1:9009/v1` (the Docker bridge address the task containers use) answers ✅. Cold start succeeded on the first attempt: weights 107 s from the shared NVMe path, torch.compile 25 s, FlashInfer MoE JIT + autotune the rest, ~5 min in total (23:06 → 23:11). GPU KV cache 982,090 tokens at `--gpu-memory-utilization 0.75` (15.0× concurrency at 64k); with the server up `free` shows ~22 GB available. The `blab-0238` account's server that had held port 9009 since 2026-10-26 was stopped on 2026-10-29 before this start. **Memory-pressure caveat:** a Harbor run with two task containers launched from a Claude Code background shell was once killed by Claude Code's memory-pressure reaper at ~1 GB free; launch Harbor runs from a terminal or with `setsid nohup`, not as a Claude Code background task. | — |
| API keys | none in the environment yet | Export `OPENAI_API_KEY` etc. in `~/.bashrc` (§4); not needed for local-vLLM runs |

### 2.1 Project environment on 0238 ✅ done 2026-10-29

Recipe used (binaries by path; `--override-channels -c conda-forge` avoids the Anaconda channel ToS prompt):

```bash
# from the repo root
C=~/miniconda3/bin/conda
$C create -n myenv   --override-channels -c conda-forge python=3.12   # serving
~/miniconda3/envs/myenv/bin/pip install vllm==0.20.1 flashinfer-python==0.6.8.post1 ninja transformers==5.8.0
$C create -n deontic --override-channels -c conda-forge python=3.12   # DeonticBench (Harbor needs ≥3.12)
grep -v '^vllm' DeonticBench/requirements.txt | ~/miniconda3/envs/deontic/bin/pip install -r /dev/stdin
# conda-forge has no swi-prolog for linux-aarch64; install it with apt if needed

# Harbor side
cd harbor-deonticbench && uv sync --all-extras --dev && cd ..
```

Upstream's README differs from this repo in two places (still true ✅):

- No `git submodule update` is needed: `harbor-deonticbench/meta-harness/` is committed as plain
  files; there is no `.gitmodules`.
- `harbor-deonticbench/datasets/` does not need generating: we track it in git (§5).

## 3. BRTX (601–606) — current state ✅ 2026-10-23, written on brtx602

Sources: checked on `brtx602` on 2026-10-23, plus the lab's BRTX wiki (`/srv/local1/zwang544/BRTX.pdf`,
a Teams export; the wiki itself is from 2023, so prefer the live checks where they differ).

### 3.0 Binding rule: operate BRTX strictly by the lab wiki

**Owner's rule, 2026-10-31.** On brtx601–606 we follow the BRTX wiki to the letter, for storage and
for GPU scheduling alike, even when other users visibly do not. The wiki is a Teams export saved at
`/srv/local1/zwang544/BRTX.pdf` on 602 (not in git; copy it out of band to any machine that needs
it). Everything below is a restatement of the wiki; the wiki wins if they ever differ.

**Connecting**

- Never SSH to a node directly; go through `dslogin01.pha.jhu.edu` (or a CLSP login node) with
  `ProxyJump`. Do not open ports to the outside; between nodes only TCP 50000–51024 are open, so a
  model server listens on one of those.

**GPU jobs — Slurm, always, no exceptions**

- Every GPU process is started by Slurm (`srun`/`sbatch`) and uses only the GPUs Slurm assigns via
  `CUDA_VISIBLE_DEVICES`. Nothing GPU-related is ever launched from a login shell, in `nohup`, in
  `tmux`, or by a Claude Code session on its own initiative.
- Interactive GPU work: partition `brtx6-dev` (brtx601 only), at most 1 GPU, at most 1 session at
  a time, 12 h limit; `srun --pty --gpus=1 -p brtx6-dev bash`, launched from brtx601.
- Batch / servers: `sbatch --gpus=N -p <partition> script.sh`. A100 work goes to `ba100` (brtx602,
  24 h limit); RTX 6000 batch to `brtx6` (604–605, 24 h). Always pass an explicit `--time` ≤ the
  partition limit. A vLLM server is a batch job like any other: it ends with the job, is never
  left running "just in case", and is resubmitted when the next run needs it.
- Because some users start processes outside Slurm, a job must check at start that the GPUs it was
  given are really free (`nvidia-smi --query-gpu=memory.used --format=csv`) and exit cleanly if not,
  instead of squeezing in beside someone else's process.
- Check the queue with `squeue` (with `-O 'JobID,Name,UserName,tres-per-job,TimeUsed,ReasonList'`
  for GPU counts) and node health with `nvidia-smi` / `gpustat` before submitting.

**Storage**

- `/home/<user>` is a 25 GB NFS quota shared by all nodes: configs, keys, small tools (`uv`) only.
  No weights, caches, run outputs or Python environments there; point `HF_HOME`, `UV_CACHE_DIR` and
  similar at NVMe.
- Each node has two 3.5 TB NVMe partitions, `/srv/local1` and `/srv/local2` locally, the same disks
  visible cluster-wide as `/brtx/60X-nvme1` and `/brtx/60X-nvme2`. **Use `local1`/`nvme1`;** avoid
  `local2`/`nvme2`, which holds Docker's data and breaks Docker on that node if it fills.
- Own files live under a directory named after the user (`/srv/local1/zwang544/`,
  `/brtx/601-nvme1/zwang544/`). Check `df -h` before any large write; never fill a partition to 0.
- Clean up after yourself: delete stale checkpoints and outputs; if a Docker container ever leaves
  root-owned files, remove them with the wiki's bind-mount recipe rather than leaving them.

**Node hygiene**

- Set `RAYON_RS_NUM_CPUS=1` (and `MKL_NUM_THREADS=1` when relevant) in the shell profile so
  tokenizers and MKL do not take every core of a shared node.
- Do not run `docker system prune` or similar cluster-wide cleanups; the admin's weekly cron does it.
- Anything the wiki does not cover is asked on the lab's Teams channel first, not improvised.

### 3.1 Access and scheduling

| Item | State |
|---|---|
| Hostnames | `brtx601.idies.jhu.edu` … `brtx606.idies.jhu.edu`. Currently on `brtx602` (IPs 172.23.93.102 / 10.99.93.102). |
| Login path | **No direct SSH.** Jump through `dslogin01.pha.jhu.edu` (IDIES) or a CLSP login node. Current access is via VS Code Remote. Suggested `~/.ssh/config` on the client: `Host brtx*.idies` / `Hostname %h.jhu.edu` / `ProxyJump dslogin01.pha.jhu.edu`, then `ssh brtx602.idies`. |
| User / group | `zwang544`, group `vandurme` (no `docker` group). |
| Scheduler | **Slurm, mandatory for all GPU use** (wiki rule, **reaffirmed as a project rule by the owner on 2026-10-31**: we never start GPU processes directly on a node, even though other users do, see §3.1 Contention). `srun`/`sbatch`/`sinfo`/`squeue` in `/usr/bin`. Slurm sets `CUDA_VISIBLE_DEVICES`. **Every vLLM job is submitted with a time limit of at most 24 h** (owner's rule, 2026-10-31); a server is never left running indefinitely, and it is resubmitted when a run needs it. Because non-Slurm processes exist, every job must verify at start that its allocated GPUs are actually free (`nvidia-smi --query-gpu=pci.bus_id,memory.used --format=csv`) and exit if not. |
| Partitions (`sinfo` ✅) | `ba100` → brtx602, 4× A100, **24 h limit**, LLM jobs. `brtx6-dev` → brtx601, 8× RTX 6000, 12 h, interactive only (≤1 GPU, ≤1 session, and `srun` for it must be launched **from brtx601**). `brtx6` → brtx604-605, 10× RTX 6000 each, 24 h batch. `brtx6-ir` → brtx603 (IR group). `brtx-pod` → brtx606. |
| Typical commands | Interactive (from 601): `srun --pty --gpus=1 -p brtx6-dev bash`. Batch on the A100s: `sbatch --gpus=N -p ba100 script.sh` (`--nodelist=brtx602` is implied by the partition). Defaults via `export SLURM_PARTITION=brtx6-dev` / `export SBATCH_PARTITION=ba100`. Status: `squeue -O 'JobID,Name,UserName,tres-per-job,TimeUsed,ReasonList'`. |
| Contention | On 2026-10-23 three of the four A100s were in use by another user's `ba100` jobs; plan for queueing. **2026-10-30 ✅:** `sinfo` showed all six nodes `idle` and `squeue` empty, yet `nvidia-smi` on 602 showed two vLLM servers of another user on GPU 0 (74 GB, up 32 days, 0 % util) and GPU 1 (56 GB, up 11 days), i.e. **started outside Slurm**. GPUs 2 and 3 were free. Consequence: Slurm does not know those two GPUs are taken, so a `--gpus=2` job may be handed GPU 0/1 and collide; check `CUDA_VISIBLE_DEVICES` at job start, **Decision 2026-10-31: we do not copy that practice; our servers always go through Slurm** (the owner's rule, also in `04` decision log). 604/605/606 had all 10 RTX 6000 each free. Node-wide `gpustat`/`nvidia-smi -l 5`; cross-node dashboard: `gpustat-web` (wiki §GPUs). Helper scripts: <https://github.com/JHU-CLSP/brtx-utils>. |
| Networking | Most ports between nodes are blocked; **TCP 50000–51024 are open between all six nodes** (use these for a vLLM server that other nodes/jobs talk to). Inside a node, use `127.0.0.1` not `localhost` (IPv6 hang with Docker-published ports). |

### 3.2 Hardware and drivers on brtx602 ✅

| Item | State |
|---|---|
| OS / arch | RHEL 9.8 (Plow), kernel 5.14, **x86_64** (so wheels and images from 0238's ARM setup do not transfer; environments are rebuilt per machine). |
| CPU / RAM | 2× Intel Xeon Silver 4214 @ 2.2 GHz, 24 threads; 754 GB RAM (Slurm `RealMemory` 739 GB). 602–606 share this CPU spec. |
| GPUs | 4× **NVIDIA A100 80GB PCIe** (81920 MiB each). Driver 595.58.03, CUDA driver API 13.2. Toolkit at `/usr/local/cuda-13.2` (`/usr/local/cuda` symlink) but `nvcc` is **not on PATH** — add `/usr/local/cuda/bin` if compiling kernels. |
| Containers | ❌ `docker`, `podman`, `apptainer`, `enroot` all absent from PATH on 602; docker service inactive, no socket, and we are not in a `docker` group. `/srv/local2/docker` exists on every node, so Docker may exist for other users/nodes 🔶 — ask on Teams if the harness must run here. **Decision for now: the DAR harness (Harbor + task containers) runs only on 0238; BRTX is for GPU jobs.** |

### 3.3 Software ✅ (all "missing" below are things we still have to install)

| Item | State | Action needed |
|---|---|---|
| Python | system `/usr/bin/python3` = **3.9.25**, no `pip`, `venv` works. Too old for Harbor (≥3.12) and the vLLM stack. | Install **miniconda** on NVMe, never in `$HOME` (§3.0): `/brtx/601-nvme1/zwang544/miniconda3` until 602's `local1` has room. See §3.6. |
| conda / `uv` | `uv` 0.13.6 at `~/.local/bin/uv` ✅ 2026-10-31 (a 40 MB binary; its cache goes to `UV_CACHE_DIR` on NVMe). conda ❌ | conda: Miniconda x86_64 installer with `-p /brtx/601-nvme1/zwang544/miniconda3`. |
| `swipl` | ❌ | `conda install -c conda-forge swi-prolog` in the project env (needed only for DeonticBench's own Prolog eval). |
| `torch` / `vllm` | ❌ | Into the project env, CUDA 12.x/13.x wheels (x86_64; driver 595 supports both). |
| `gh` | ❌ | Optional. |
| `git` | 2.52.0 ✅ | — |
| `rsync` / `scp` | ✅ | For moving weights/outputs between nodes and to 0238. |
| Environment modules | none (`module` not found); nothing under `/opt` except `nvidia`. | — |
| Shell profile | `~/.bashrc` is the 2020 default; no API keys, no `SLURM_PARTITION`. | Add exports: API keys (§4), `SBATCH_PARTITION=ba100`, `RAYON_RS_NUM_CPUS=1` (wiki: fast tokenizers otherwise spawn a thread per core and thrash the node), `HF_HOME` pointing at scratch (§3.4). |

### 3.4 Storage ✅ — **this is the blocker**

| Location | Size / free on 2026-10-23 | Notes |
|---|---|---|
| `/home/zwang544` | **25 GB** NFS quota, 3.7 GB used, 22 GB free | Shared across all six nodes. Slow. Configs, SSH keys and the `uv` binary only; **no conda, no Python envs, no caches, no weights, no run outputs** (§3.0). |
| `/srv/local1` on 602 | 3.5 TB NVMe, **100% full, ~200 MB free** (2026-10-31) | Our repo lives here (`/srv/local1/zwang544/deontic-reasoning-with-harness`, 117 MB after `jobs/` was un-tracked). On 2026-10-31 a `git pull` that still carried ~450 MB of `harbor-deonticbench/jobs/` failed with `No space left on device` and left the clone in a broken rebase; it had to be moved to home to recover, then moved back. **Keep run artifacts out of git** (they are gitignored again) and never let this disk hit 0, or git itself stops working. |
| `/srv/local2` on 602 | 3.5 TB NVMe, **100% full, 1.5 GB free** | Holds `/srv/local2/docker`; wiki says prefer `local1` anyway. |
| Other nodes' NVMe, cross-mounted at `/brtx/60{1..6}-nvme{1,2}` (NFS v4.2, rw, dirs are `root:vandurme` setgid so the group can create subdirs; many lab members keep dirs on several nodes) | **Re-checked 2026-10-30, it moves fast**: 605-nvme1 went from 639 GB free to **100% full (inodes too)** in a week. Now: 606-nvme2 235 GB, 605-nvme2 224 GB, 601-nvme2 201 GB, 601-nvme1 115 GB; every other partition ≤55 GB. | Writable from 602 but over NFS. Nothing in the wiki forbids using another node's partition, but the wiki asks to prefer `local1` over `local2` (Docker data lives on `local2`; a full `local2` breaks Docker on that node). The only `local1` with room is **601-nvme1, 115 GB**, enough for one 72 GB checkpoint and little else. Re-run `df -h /brtx/*` right before downloading. |

Plan: the lab-wide disk situation, not just 602's, is the real constraint (10 of 12 NVMe partitions ≥93% full on 2026-10-30).
Ask on Teams for a cleanup of 602's `/srv/local1`; that is the right home for the weights. Until then the fallback is
`/brtx/601-nvme1/zwang544/` (local1, 115 GB free), with `HF_HOME` pointed there and `hf download --local-dir` so nothing
lands in the 25 GB home. Weekly cron on each node runs `docker system prune -f` (Mon 02:00), irrelevant without Docker.

### 3.5 Git ✅

| Item | State | Action needed |
|---|---|---|
| Repo | `/srv/local1/zwang544/deontic-reasoning-with-harness`, `main` tracking `origin/main` | Home (25 GB NFS) is not an option for the repo; if local1 fills up again, 601-nvme1 is the fallback. |
| Identity | set ✅ 2026-10-23 (`user.name` / `user.email` in `~/.gitconfig`, shared by all six nodes) | — |
| Push auth | **SSH, works from any shell on any BRTX node** ✅ 2026-10-31. Key `~/.ssh/id_ed25519` (no passphrase, home is shared by all six nodes) is registered on the GitHub account; remote is `git@github.com:Eden-Wang1710/deontic-reasoning-with-harness.git`. Before this, HTTPS pushes only worked inside VS Code Remote (it injects `GIT_ASKPASS`) and failed from plain SSH or Claude Code remote-control sessions. | — |

### 3.6 Project environment on BRTX (to do)

Mirrors §2.1 but x86_64 and disk-aware. Run on 602 (CPU-only steps don't need Slurm):

```bash
# one-off. Everything big lives on NVMe (§3.0); $HOME gets only the uv binary and shell exports.
# 602's own /srv/local1 is full (2026-10-31), so the NVMe in use is 601's, cross-mounted. Move back when 602 has room.
S=/brtx/601-nvme1/zwang544
mkdir -p $S/{models,hf_cache,uv_cache,outputs,logs}            # check `df -h $S` first; 48 GB free on 2026-10-31
cat >> ~/.bashrc <<'EOF'
export HF_HOME=/brtx/601-nvme1/zwang544/hf_cache
export UV_CACHE_DIR=/brtx/601-nvme1/zwang544/uv_cache
export SBATCH_PARTITION=ba100 RAYON_RS_NUM_CPUS=1
EOF
curl -LsSf https://astral.sh/uv/install.sh | sh                 # ✅ done 2026-10-31 → ~/.local/bin/uv
curl -fsSLo $S/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash $S/miniconda.sh -b -p $S/miniconda3 && rm $S/miniconda.sh   # on NVMe, not in $HOME; do not run `conda init`
C=$S/miniconda3/bin/conda

# project envs (same recipe as 0238 §2.1, binaries by path)
$C create -n myenv   --override-channels -c conda-forge python=3.12 -y   # serving
$S/miniconda3/envs/myenv/bin/pip install vllm torch                       # x86_64 wheels; pin to 0238's versions when they work
$C create -n deontic --override-channels -c conda-forge python=3.12 swi-prolog -y   # DeonticBench; x86_64 has swi-prolog
grep -v '^vllm' DeonticBench/requirements.txt | $S/miniconda3/envs/deontic/bin/pip install -r /dev/stdin
cd harbor-deonticbench && uv sync --all-extras --dev && cd ..  # Harbor CLI only; tasks can't run here (no Docker)

# weights ✅ done 2026-10-31 (71.9 GB, 14 shards verified against the index)
hf download Qwen/Qwen3.5-35B-A3B --local-dir $S/models/Qwen3.5-35B-A3B
```

### 3.7 Serving Qwen3.5-35B-A3B with hidden-state access 🔶

Not yet tried. Starting point: one A100 80GB holds the bf16 weights (72 GB) with little KV room, so request
`--gpus=2` and run vLLM with `--tensor-parallel-size 2` under `sbatch -p ba100` (24 h limit → the server
must be resubmitted daily; write the script so it restarts cleanly). Serve on a port in 50000–51024 so
jobs on other nodes can reach it. For hidden states, vLLM does not expose them; the plan in
[04](04_initial_proposal.md) §4.2 needs either a `transformers` forward pass with `output_hidden_states=True`
on a second GPU, or a vLLM hook — decide once the env exists.

## 4. Sync workflow

1. `git pull` when starting on either machine.
2. Work. Keep our own code **outside** `DeonticBench/` and `harbor-deonticbench/` (subtree rule in
   `CLAUDE.md`).
3. Commit and `git push` when stopping, **before** switching machines. Small, frequent commits.
4. If both machines diverge, `git pull --rebase` on the later one; the docs are plain markdown and
   conflicts are easy to resolve.

**Run artifacts.** The small per-trial summaries in `outputs/` and `DeonticBench/outputs/` (about
10 MB together) are tracked since 2026-10-27. The full Harbor job directories under
`harbor-deonticbench/jobs/` (about 470 MB, 16k files) were tracked from 2026-10-27 (commit `51dc548`)
to 2026-10-31 so the machine switch could go through git alone; since 2026-10-31 they are
**gitignored again** and stay on the machine that produced them. Every job up to `rv-smoke1` is
still in history: `git restore -s 0dc69015 -W -- harbor-deonticbench/jobs` puts them back on disk
without staging them. **On a clone that still has the tracked copy**, the first `git pull` after
2026-10-31 deletes those files from the working tree; run that restore command afterwards (or copy
`jobs/` aside before pulling). The numbers from every run are in `claude_docs/`.

**Not in git**, by design (regenerate or copy out of band with `rsync`/`scp` between machines when
needed):

- `harbor-deonticbench/jobs/` (see above), `bootstrap_results/`, `wandb/`, and `*.log` files
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
