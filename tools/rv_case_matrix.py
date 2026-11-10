#!/usr/bin/env python3
"""Per-case correctness of SARA numeric across configurations, for error attribution.

Prints one row per task: gold, the four direct-prompt generations (DeonticBench, whole statute in
the prompt), reward in the t2fix / t2verify run-3 / t2rv / t2rvc runs (T = agent timeout), and the
answer and rubric statuses of every verifier round of the t2rvc run. Then the tasks the t2rvc run
got wrong, split by whether any other configuration solved them (claude_docs/09 §10.11).

Run from the repo root with an interpreter that has numpy (DeonticBench's answer parser needs it):
    harbor-deonticbench/.venv/bin/python tools/rv_case_matrix.py
"""
import ast
import glob
import json
import re
import sys

sys.path.insert(0, 'DeonticBench/scripts')
sys.path.insert(0, 'tools')
import bootstrap_outputs as bo  # noqa: E402
import rv_run_report as R  # noqa: E402

DIRECT = 'DeonticBench/outputs/sara_numeric/direct/vllm/qwen3.5-35b-a3b/source.json'
J = 'harbor-deonticbench/jobs/qwen3.5-35b-a3b'
RUNS = {'t2fix': J + '/t2fix-direct/sara-t2fix-run1', 'run3': J + '/t2verify-direct/sara-t2verify-run3',
        'rv': J + '/t2rv-direct/rv-run1-numeric'}
RVC = J + '/t2rvc-direct/rvc-run1-numeric'


def direct_answers():
    out = {}
    for x in json.load(open(DIRECT)):
        case = int(re.search(r'(\d+)', x['id']).group(1))
        answers = x['answers'] if isinstance(x['answers'], list) else ast.literal_eval(x['answers'])
        vals = []
        for a in answers:
            try:
                vals.append(int(round(float(bo._parse_direct_answer(a, 'sara_numeric')))))
            except Exception:
                vals.append(None)
        out[case] = (int(float(x['label'])), vals)
    return out


def rewards(job):
    out = {}
    for f in glob.glob(job + '/*/result.json'):
        r = json.load(open(f))
        m = re.search(r'sara-numeric-tax-case-(\d+)', r.get('task_name') or '')
        if m:
            reward = ((r.get('verifier_result') or {}).get('rewards') or {}).get('reward') or 0.0
            out[int(m.group(1))] = (reward, bool(r.get('exception_info')))
    return out


def mark(d, t):
    if t not in d:
        return ' ? '
    return (' 1' if d[t][0] == 1.0 else ' 0') + ('T' if d[t][1] else ' ')


def main():
    direct = direct_answers()
    other = {k: rewards(v) for k, v in RUNS.items()}
    rvc = R.load(RVC)
    print('case    gold  direct: n right of 4, answers' + ' ' * 22 + 't2fix run3 rv  rvc | rvc rounds: answer[statuses]')
    rows = []
    for t in sorted(rvc):
        x = rvc[t]
        gold, vals = direct[t]
        nd = sum(v == gold for v in vals)
        rounds = ' '.join(f"{R.answer_value(r)}[{''.join((s or '?')[0] for s in R.statuses(r)) or 'none'}]"
                          for r in x['rounds'])
        own = (' 1' if x['reward'] == 1 else ' 0') + ('T' if x['exc'] else ' ')
        print(f"{t:4d} {gold:7d}  {nd}/4 {str(vals):44s} {mark(other['t2fix'], t)}  {mark(other['run3'], t)} "
              f"{mark(other['rv'], t)} {own} | {rounds}")
        rows.append((t, nd, [other[k].get(t, (0.0,))[0] == 1.0 for k in RUNS], x['reward'] == 1.0))
    wrong = [r for r in rows if not r[3]]
    print('\nt2rvc wrong:', len(wrong))
    print('  solved by no configuration:', sorted(r[0] for r in wrong if r[1] == 0 and not any(r[2])))
    print('  solved by the direct prompt at least once:', sorted(r[0] for r in wrong if r[1] >= 1))
    print('  solved by another harness run:', sorted(r[0] for r in wrong if any(r[2])))
    print('direct prompt: at least one of 4 right on', sum(r[1] >= 1 for r in rows), 'tasks; at least 3 of 4 on',
          sum(r[1] >= 3 for r in rows), '; mean accuracy', round(sum(r[1] for r in rows) / 4 / len(rows), 3))
    print('tasks solved by at least one harness run:', sum(1 for r in rows if any(r[2]) or r[3]),
          '| by at least one harness run or direct generation:', sum(1 for r in rows if r[1] or any(r[2]) or r[3]))


if __name__ == '__main__':
    main()
