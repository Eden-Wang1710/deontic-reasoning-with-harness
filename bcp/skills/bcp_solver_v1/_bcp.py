"""Shared by the sandbox commands of bcp_solver_v1: they post to the tools server on the host."""
import json, os, re, sys, time, urllib.request

STATE = "/app/state.md"
ANSWER = "/app/answer.md"
NONE_YET = "(none yet)"
STATE_TEMPLATE = f"""## Goal
{NONE_YET}

## Found
{NONE_YET}

## Conclusions
{NONE_YET}

## Next
{NONE_YET}

## Best answer
Explanation: {NONE_YET}
Exact Answer: {NONE_YET}
Confidence: 0%
"""


def read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _call(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def post(path: str, name: str, **body) -> int:
    """Post to the tools server with the trial's id and the state table; print the reply.

    search, read and verify take minutes (they wait for the reader or the verifier), and the
    proxy between the sandbox and the host drops a silent connection: these start a job and
    poll for its result."""
    base = "http://" + os.environ.get("BCP_TOOLS_ADDR", "172.17.0.1:9010")
    body.update(qid=os.environ.get("BCP_QID", ""), state=read_file(STATE))
    op = path.rsplit("/", 1)[-1]
    try:
        if op not in ("search", "read", "verify"):
            print(_call(base + path, body)["text"])
            return 0
        job = _call(f"{base}/v01/start/{op}", body)
        if "id" not in job:
            raise RuntimeError(job.get("error", "no job id"))
        deadline, misses = time.time() + 1700, 0
        while time.time() < deadline:
            time.sleep(2)
            try:
                reply = _call(f"{base}/v01/poll/{job['id']}")
            except Exception:  # noqa: BLE001 - a lost poll is tried again
                misses += 1
                if misses > 20:
                    raise
                continue
            if reply["done"]:
                print(reply["text"])
                return 0
        raise TimeoutError("no result after 1700 s")
    except Exception as exc:  # noqa: BLE001
        print(f"{name}: the request failed ({exc}). Try again.", file=sys.stderr)
        return 1


def best_answer(state: str) -> str:
    """The Best answer part of the state table, or "" when it holds no answer."""
    m = re.search(r"^##\s*Best answer\s*\n(.*?)(?=^##\s|\Z)", state, re.S | re.M | re.I)
    if not m:
        return ""
    body = m.group(1).strip()
    exact = re.search(r"Exact Answer:\s*(.*)", body, re.I)
    if not exact or not exact.group(1).strip() or NONE_YET in exact.group(1):
        return ""
    return body
