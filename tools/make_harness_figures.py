"""Two overview figures of the reader and the compactor sub-agents (claude_docs/09 §2, §10).

  python3 tools/make_harness_figures.py          # writes claude_docs/figures/{reader,compactor}.svg

PNG copies in the same directory were rendered with cairosvg (scale 3, white background).
mode "inline" emits CSS-variable colours for embedding in an HTML page; "file" emits literals.
"""
from pathlib import Path

LIGHT = {"ink": "#1c2530", "muted": "#5d6a79", "line": "#b9c3cf", "panel": "#ffffff",
         "soft": "#eef1f5", "accent": "#0b6e69", "accent-soft": "#dff1ef"}


class Fig:
    def __init__(self, fid, w, h, mode, label):
        self.fid, self.w, self.h, self.mode, self.label, self.out = fid, w, h, mode, label, []

    def paint(self, fill=None, stroke=None):
        if self.mode == "inline":
            parts = [f"fill:var(--{fill})" if fill else "fill:none"]
            if stroke:
                parts.append(f"stroke:var(--{stroke})")
            return f'style="{";".join(parts)}"'
        s = f'fill="{LIGHT[fill]}"' if fill else 'fill="none"'
        return s + (f' stroke="{LIGHT[stroke]}"' if stroke else "")

    def box(self, x, y, w, h, title, sub=None, kind="panel"):
        fill, stroke, sw = {"panel": ("panel", "ink", 1.5), "soft": ("soft", "line", 1.2),
                            "accent": ("accent-soft", "accent", 1.8)}[kind]
        self.out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" stroke-width="{sw}" {self.paint(fill, stroke)}/>')
        cx = x + w / 2
        if sub:
            self.text(cx, y + h / 2 - 3, title, 14, weight=600)
            self.text(cx, y + h / 2 + 15, sub, 11, token="muted")
        else:
            self.text(cx, y + h / 2 + 5, title, 13 if h < 40 else 14, weight=500 if h < 40 else 600,
                      token="muted" if kind == "soft" else "ink")

    def text(self, x, y, s, size=12, anchor="middle", weight=400, token="ink"):
        self.out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" {self.paint(token)}>{s}</text>')

    def arrow(self, pts, accent=False):
        tok = "accent" if accent else "ink"
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        mid = f"{self.fid}-{'aa' if accent else 'a'}"
        self.out.append(f'<path d="{d}" stroke-width="{2 if accent else 1.5}" stroke-linejoin="round" marker-end="url(#{mid})" {self.paint(None, tok)}/>')

    def step(self, n, cx, y, label, accent=False):
        """Numbered arrow label, centred on cx."""
        tok = "accent" if accent else "ink"
        total = 18 + 6 + len(label) * 6.4
        x0 = cx - total / 2
        self.out.append(f'<circle cx="{x0 + 9}" cy="{y - 4}" r="9" {self.paint(tok)}/>')
        self.text(x0 + 9, y, str(n), 11, weight=700, token="panel")
        self.text(x0 + 24, y, label, 12, anchor="start", weight=500, token=tok)

    def svg(self):
        defs = "".join(
            f'<marker id="{self.fid}-{sfx}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" {self.paint(tok)}/></marker>' for sfx, tok in (("a", "ink"), ("aa", "accent")))
        font = "" if self.mode == "inline" else ' font-family="Arial, Helvetica, sans-serif"'
        head = "" if self.mode == "inline" else '<?xml version="1.0" encoding="UTF-8"?>\n'
        return (f'{head}<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}"'
                f' role="img" aria-label="{self.label}"{font}>\n<defs>{defs}</defs>\n' + "\n".join(self.out) + "\n</svg>\n")


def reader(mode):
    f = Fig("rd", 770, 290, mode, "The solver sends a query to the reader, the reader searches the rule file and returns line ranges, the harness copies those lines verbatim to the solver.")
    f.box(20, 110, 170, 70, "Solver", "never opens the rule file")
    f.box(300, 40, 170, 70, "Reader", "fresh context")
    f.box(580, 40, 170, 70, "Rule file", "statute.txt")
    f.box(300, 190, 170, 70, "Harness", "plain code, no model")
    f.arrow([(190, 125), (245, 125), (245, 75), (298, 75)])
    f.step(1, 245, 60, "query")
    f.arrow([(470, 75), (578, 75)])
    f.step(2, 525, 62, "search")
    f.text(525, 93, "≤ 3 commands", 11, token="muted")
    f.arrow([(385, 110), (385, 188)])
    f.step(3, 440, 154, "line ranges")
    f.arrow([(665, 110), (665, 225), (472, 225)], accent=True)
    f.step(4, 568, 212, "copy those lines", accent=True)
    f.arrow([(300, 225), (245, 225), (245, 165), (192, 165)], accent=True)
    f.step(5, 245, 247, "verbatim text", accent=True)
    return f.svg()


def compactor(mode):
    f = Fig("cp", 770, 305, mode, "The compactor selects rule line ranges and step numbers from the solver's history, and the harness rebuilds the solver's context as the task plus one state page.")
    f.text(110, 26, "Solver context: before", 13, weight=600)
    f.box(20, 40, 180, 26, "Task")
    for i, name in enumerate(["Reader result", "Solver steps", "Reader result", "Solver steps", "Verifier report", "Reader result", "Solver steps"]):
        f.box(20, 72 + i * 32, 180, 26, name, kind="soft")
    f.box(295, 70, 180, 70, "Compactor", "fresh context, selects only")
    f.box(295, 200, 180, 70, "Harness", "copies the text verbatim")
    f.arrow([(200, 105), (293, 105)])
    f.text(247, 96, "history", 12, weight=500)
    f.arrow([(385, 140), (385, 198)])
    f.text(396, 166, "line ranges", 12, anchor="start", weight=500)
    f.text(396, 182, "step numbers", 12, anchor="start", weight=500)
    f.arrow([(475, 235), (568, 235)], accent=True)
    f.text(521, 226, "rebuilds", 12, weight=500, token="accent")
    f.text(660, 26, "Solver context: after", 13, weight=600)
    f.box(570, 40, 180, 26, "Task")
    f.out.append(f'<rect x="570" y="72" width="180" height="220" rx="6" stroke-width="1.8" {f.paint("accent-soft", "accent")}/>')
    f.text(660, 92, "State page", 13, weight=600, token="accent")
    for i, name in enumerate(["Answer file", "Latest verifier report", "Selected rule text", "Selected step outputs"]):
        f.box(582, 102 + i * 46, 156, 36, name)
    return f.svg()


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "claude_docs" / "figures"
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in (("reader", reader), ("compactor", compactor)):
        (out / f"{name}.svg").write_text(fn("file"))
    print("wrote", out)
