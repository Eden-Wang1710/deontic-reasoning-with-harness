#!/usr/bin/env python3
"""What the checks of a solver-checks (t2sc) job cost and what they tell apart.

Reads the per-trial logs of one job directory (rubric_verifier/summary.json, llm_calls.jsonl,
episode-*/debug.json, verifier/test-stdout.txt) and prints:

  - check runs by mode (reply finished with thinking / cut at the output cap and sent again
    without thinking), the output tokens of each kind of attempt and of each round;
  - the length of the thinking replies that finished, as percentiles;
  - per round, how many answer files were right or wrong and whether the report passed;
  - per check, how often it failed a right and a wrong answer file in round 1;
  - trials whose outcome differs between the first checked answer file and the final reward;
  - the size of the solver's context and what its last prompt consists of.

"Right" compares the answer line of the checked file with the grader's expected value. This is
offline analysis: nothing here is fed back to a prompt or a skill.

Usage: python3 tools/sc_check_stats.py <job dir>
"""

from __future__ import annotations

import collections
import glob
import json
import re
import statistics
import sys


def percentile(values: list[int], p: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p / 100 * len(ordered)))]


def answer_is_right(answer_file: str | None, expected: str) -> bool:
    lines = re.findall(r"^Answer:\s*(.+)$", answer_file or "", re.M)
    if not lines:
        return False
    value = re.sub(r"[^0-9.\-]", "", lines[-1])
    try:
        return round(float(value)) == round(float(expected))
    except ValueError:
        return lines[-1].strip().lower() == expected.strip().lower()


def context_parts(debug_file: str) -> tuple[collections.Counter, int, int]:
    messages = json.load(open(debug_file)).get("messages") or []
    parts: collections.Counter = collections.Counter()
    written = echoed = 0
    for i, m in enumerate(messages):
        text = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
        if i == 0 or (i == 1 and messages[0]["role"] == "system"):
            parts["first message"] += len(text)
        elif m["role"] == "assistant":
            parts["solver replies"] += len(text)
            written += "## Sections" in text
        elif "VERIFIER" in text[:300].upper():
            parts["verifier reports"] += len(text)
        else:
            parts["terminal output"] += len(text)
            echoed += text.count("## Sections")
    return parts, written, echoed


def main(job: str) -> None:
    runs: collections.Counter = collections.Counter()
    attempts: collections.Counter = collections.Counter()
    attempt_tokens: collections.Counter = collections.Counter()
    round_tokens: collections.Counter = collections.Counter()
    round_state: collections.Counter = collections.Counter()
    round1_fail: collections.Counter = collections.Counter()
    mode_verdict: collections.Counter = collections.Counter()
    rounds_per_trial: collections.Counter = collections.Counter()
    parts_total: collections.Counter = collections.Counter()
    thinking_lengths: list[int] = []
    largest_prompt: list[int] = []
    first_prompt: list[int] = []
    changed: list[str] = []
    written_total = echoed_total = trials = 0

    for trial in sorted(glob.glob(job.rstrip("/") + "/*/")):
        try:
            summary = json.load(open(trial + "agent/rubric_verifier/summary.json"))
            stdout = open(trial + "verifier/test-stdout.txt").read()
            expected = re.search(r"Normalized expected:\s*(\S+)", stdout).group(1)
            reward = float(open(trial + "verifier/reward.txt").read().strip())
        except (OSError, AttributeError, ValueError):
            continue
        trials += 1
        calls = [json.loads(line) for line in open(trial + "agent/llm_calls.jsonl")]
        solver = [c["prompt_tokens"] for c in calls if c["role"] == "solver"]
        largest_prompt.append(max(solver))
        first_prompt.append(solver[0])
        for c in calls:
            if c["role"].startswith("verifier") and c["thinking"]:
                if c["completion_tokens"] < c["max_tokens"]:
                    thinking_lengths.append(c["completion_tokens"])

        episodes = sorted(
            glob.glob(trial + "agent/episode-*/debug.json"),
            key=lambda p: int(re.search(r"episode-(\d+)", p).group(1)),
        )
        if episodes:
            parts, written, echoed = context_parts(episodes[-1])
            parts_total.update(parts)
            written_total += written
            echoed_total += echoed

        rounds_per_trial[len(summary["rounds"])] += 1
        states = []
        for r in summary["rounds"]:
            right = answer_is_right(r["answer"], expected)
            state = "right" if right else "wrong"
            states.append(state)
            round_state[(r["round"], state, "passed" if r["passed"] else "not passed")] += 1
            for c in r["checks"]:
                if c.get("reused"):
                    runs["reused"] += 1
                    continue
                mode = "no thinking (after the cap)" if c.get("thinking_fallback") else "thinking"
                runs[mode] += 1
                mode_verdict[(mode, state, c["status"])] += 1
                if r["round"] == 1 and c["status"] == "FAIL":
                    round1_fail[(c["check"], state)] += 1
                for a in c.get("attempts", []):
                    key = ("thinking" if a["thinking"] else "no thinking", a["outcome"])
                    attempts[key] += 1
                    attempt_tokens[key] += a["completion_tokens"]
                    round_tokens[r["round"]] += a["completion_tokens"]
        if states and (states[0] == "right") != (reward == 1.0):
            changed.append(f"{trial.rstrip('/').split('__')[-1]}: {' > '.join(states)} > reward {reward:g}")

    print(f"trials: {trials}")
    print(f"check runs: {dict(runs)}")
    print("attempts (mode, outcome): count, output tokens, mean")
    for key in sorted(attempts):
        n, t = attempts[key], attempt_tokens[key]
        print(f"  {key}: {n}, {t:,}, {t / n:,.0f}")
    print(f"check output tokens by round: {dict(sorted(round_tokens.items()))}")
    if thinking_lengths:
        print(
            f"thinking replies that finished: {len(thinking_lengths)}, "
            f"p50 {percentile(thinking_lengths, 50):,}, p75 {percentile(thinking_lengths, 75):,}, "
            f"p90 {percentile(thinking_lengths, 90):,}; share within "
            + ", ".join(
                f"{b:,}: {sum(x <= b for x in thinking_lengths) / len(thinking_lengths):.0%}"
                for b in (2000, 4000, 6000)
            )
        )
    print(f"rounds per trial: {dict(sorted(rounds_per_trial.items()))}")
    print("round, answer file, report: trials")
    for key in sorted(round_state):
        print(f"  {key}: {round_state[key]}")
    print(f"round-1 FAIL by (check, answer file): {dict(sorted(round1_fail.items()))}")
    print("status by (mode, answer file, status):")
    for key in sorted(mode_verdict):
        print(f"  {key}: {mode_verdict[key]}")
    print("first checked answer and final reward differ:")
    for line in changed:
        print(f"  {line}")
    print(
        f"solver prompt tokens: first {statistics.mean(first_prompt):,.0f}; largest per trial "
        f"mean {statistics.mean(largest_prompt):,.0f}, p50 {percentile(largest_prompt, 50):,}, "
        f"p90 {percentile(largest_prompt, 90):,}, max {max(largest_prompt):,}"
    )
    print(
        "trials whose solver context passes a window: "
        + ", ".join(f"{w:,}: {sum(x > w for x in largest_prompt)}" for w in (16000, 20000, 24000, 32000))
    )
    total = sum(parts_total.values()) or 1
    print("last solver prompt, characters per trial:")
    for name, chars in parts_total.most_common():
        print(f"  {name}: {chars / trials:,.0f} ({chars / total:.0%})")
    print(
        f"answer file per trial: written {written_total / trials:.1f} times, "
        f"shown again in terminal output {echoed_total / trials:.1f} times"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
