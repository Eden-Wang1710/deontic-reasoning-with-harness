"""Per-trial dossiers for DAR SARA-numeric trajectories: what the model saw and what it did.

For every numeric trial of a Harbor job this script records expected / got, the case facts
and gold Prolog comments (from DeonticBench/data/sara_numeric/hard.json), which statute lines
the model actually had in its observations (exact line matching against the task's
statute.txt, summarised per key provision), the statute-reading commands it issued, whether
it ran python, and the last analysis messages plus the tail of the thinking that produced the
answer. The output is what the error classification in claude_docs/07 §1.5.1 was read from.

Usage (from the repo root):
    python tools/dar_trajectory_dossiers.py <job_dir> <tag> <out_dir>
writes <out_dir>/dossiers_<tag>.json and <out_dir>/wrong_<tag>.md (wrong trials only, compact)
and prints the per-trial provision-coverage table.
"""
import json,glob,re,os,sys,collections
from datetime import datetime
ROOT="harbor-deonticbench"
STAT=open(f"{ROOT}/datasets/deonticbench-direct/deonticbench-direct-sara-numeric-tax-case-10/environment/statute.txt").read().split("\n")
# key provisions (1-based line ranges), from the section index
KEY={"§1 tables":(1,51),"§151(a-d2)":(52,77),"§151(d)(3) phaseout":(78,87),"§151(d)(5) 2018+":(88,94),"§152 dependent":(95,181),
     "§2 surviving/HoH":(182,244),"§3301/3306 FUTA":(245,357),"§63(a-c1)":(358,383),"§63(c)(2) basic SD":(384,397),"§63(c)(3) aged/blind":(398,401),
     "§63(c)(5-7)":(402,431),"§63(d-g)":(432,470),"§68 itemized lim":(471,503),"§7703 marital":(504,523)}
cases={c["id"]:c for c in json.load(open("DeonticBench/data/sara_numeric/hard.json"))}
def obs_text(step):
    o=step.get("observation")
    if o is None: return ""
    if isinstance(o,str): return o
    if isinstance(o,dict):
        parts=[]
        for r in o.get("results",[]) or []:
            parts.append(r.get("content","") if isinstance(r,dict) else str(r))
        return "\n".join(parts) if parts else json.dumps(o)
    return str(o)
def seen_lines(obs_all):
    seen=set()
    for i,l in enumerate(STAT,1):
        ls=l.strip()
        if len(ls)>=25 and ls in obs_all: seen.add(i)
    return seen
def cov(seen,a,b):
    tot=sum(1 for i in range(a,b+1) if len(STAT[i-1].strip())>=25)
    got=sum(1 for i in range(a,b+1) if i in seen)
    return got,tot
def cmds_of(step):
    out=[]
    for tc in step.get("tool_calls") or []:
        a=tc.get("arguments") or {}
        if isinstance(a,dict) and "commands" in a:
            for c in a["commands"]: out.append(c.get("keystrokes","") if isinstance(c,dict) else str(c))
        elif isinstance(a,dict) and a.get("keystrokes"): out.append(a["keystrokes"])
    return out
def dossier(d):
    r=json.load(open(d+"result.json")); task=r["task_name"]; cid="tax_case_"+task.rsplit("-",1)[1]
    t=json.load(open(d+"agent/trajectory.json")); steps=t["steps"]
    txt=open(d+"verifier/test-stdout.txt").read() if os.path.exists(d+"verifier/test-stdout.txt") else ""
    exp=re.search(r"Expected:\s*(\S+)",txt); got=re.search(r"Got:\s*(\S+)",txt)
    exp=exp.group(1) if exp else None; got=got.group(1) if got else None
    rew=(r.get("verifier_result") or {}).get("rewards",{}).get("reward")
    obs_all="\n".join(obs_text(s) for s in steps)
    seen=seen_lines(obs_all)
    cmds=[c for s in steps for c in cmds_of(s)]
    reads=[c.strip().split("\n")[0][:70] for c in cmds if "statute" in c]
    py=any(c.lstrip().startswith("python") for c in cmds)
    ans_step=None
    for s in steps:
        if any("answer.txt" in c and ("echo" in c or "printf" in c or ">" in c) for c in cmds_of(s)): ans_step=s; break
    key={k:cov(seen,*v) for k,v in KEY.items()}
    return dict(trial=os.path.basename(d.rstrip("/")),task=task,cid=cid,exp=exp,got=got,ok=rew==1.0,exc=bool(r.get("exception_info")),
                steps=len(steps),reads=reads,py=py,key=key,seen_n=len(seen),
                msgs=[(s.get("message") or "")[:900] for s in steps if s.get("source")=="agent"][-3:],
                reasoning_tail=((ans_step or steps[-1]).get("reasoning_content") or "")[-1800:],
                case=cases.get(cid,{}))
def run(job,tag):
    out=[dossier(d) for d in sorted(glob.glob(job+"/deonticbench-direct-sara-numeric__*/"))]
    json.dump(out,open(f"{sys.argv[1]}/dossiers_{tag}.json","w"),indent=1)
    # coverage table
    print(f"\n=== {tag}: key-provision coverage (lines seen / lines in range), numeric trials, sorted wrong first ===")
    names=list(KEY)
    print("trial      ok  exp      got      rel%   reads py | "+" | ".join(n[:14] for n in names))
    for x in sorted(out,key=lambda x:(x["ok"],x["task"])):
        try: rel=100*(int(x["got"])-int(x["exp"]))/int(x["exp"])
        except: rel=float("nan")
        print(f"{x['trial'][-7:]}  {'Y' if x['ok'] else 'N'}  {str(x['exp']):>8} {str(x['got']):>8} {rel:6.1f} {len(x['reads']):5d} {'y' if x['py'] else '.'}  | "+" | ".join(f"{x['key'][n][0]:3d}/{x['key'][n][1]:<3d}" for n in names))
    # aggregate: fraction of trials that saw >=50% of each key range, wrong vs right
    print("\nshare of trials with >=50% of the range seen: wrong | right")
    for n in names:
        w=[x for x in out if not x["ok"]]; g=[x for x in out if x["ok"]]
        f=lambda xs: sum(1 for x in xs if x["key"][n][1] and x["key"][n][0]/x["key"][n][1]>=0.5)
        print(f"  {n:24s} {f(w):2d}/{len(w)} | {f(g):2d}/{len(g)}")
    return out



def write_wrong_md(out, tag, out_dir):
    lines = []
    for x in sorted(out, key=lambda x: (x["ok"], x["task"])):
        if x["ok"]:
            continue
        try:
            rel = 100 * (int(x["got"]) - int(x["exp"])) / int(x["exp"])
        except (TypeError, ValueError):
            rel = float("nan")
        seen = [n for n, (a, b) in x["key"].items() if b and a / b >= 0.5]
        pl = x["case"].get("reference_prolog", "")
        comments = [l.strip() for l in pl.split("\n") if l.strip().startswith("%") and not l.strip().startswith("% tax_bracket")]
        lines.append(f"""### {tag} {x['trial'][-7:]} | {x['task'].rsplit('-', 1)[1]} | exp {x['exp']} got {x['got']} ({rel:+.1f}%) | reads {len(x['reads'])} py {x['py']} | steps {x['steps']}
FACTS: {x['case'].get('text', '')[:900]}
Q: {x['case'].get('question', '')}
SEEN>=50%: {', '.join(seen)}
GOLD COMMENTS: {' | '.join(comments)[:900]}
MODEL FINAL ANALYSIS: {x['msgs'][-1][:900] if x['msgs'] else ''}
MODEL REASONING TAIL: {x['reasoning_tail'][-1100:]}
""")
    open(os.path.join(out_dir, f"wrong_{tag}.md"), "w").write("\n".join(lines))


if __name__ == "__main__":
    job, tag, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)
    sys.argv[1] = out_dir  # run() writes dossiers_<tag>.json under sys.argv[1]
    write_wrong_md(run(job, tag), tag, out_dir)
