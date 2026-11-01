"""report.md -> report.html: a self-contained page (no dependency) for the subset of Markdown the reports use:
headings, paragraphs, tables, lists, bold, italic, inline code, code blocks, links, rules and quotes.

    venv\\Scripts\\python @modules\\viral_research\\analysis\\report_html.py <run folder>
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

STYLE = """
:root{color-scheme:light dark;--bg:#fbfaf8;--fg:#1c1f22;--muted:#5d666c;--line:#e2e0dc;--panel:#f2f0ec;--accent:#2f5bd3}
@media (prefers-color-scheme:dark){:root{--bg:#121416;--fg:#eceeef;--muted:#9aa4a8;--line:#30363b;--panel:#1b1f22;--accent:#9db9ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 "Segoe UI",Inter,sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;line-height:1.2;margin:0 0 12px}h2{font-size:22px;margin:40px 0 12px;padding-top:12px;border-top:1px solid var(--line)}
h3{font-size:17px;margin:28px 0 8px}h4{font-size:15px;margin:20px 0 6px}
p,li{max-width:80ch}a{color:var(--accent)}code{font:13px Consolas,"JetBrains Mono",monospace;background:var(--panel);padding:1px 5px;border-radius:5px}
pre{background:var(--panel);padding:12px 14px;border-radius:10px;overflow-x:auto}pre code{background:none;padding:0;white-space:pre}
.table{overflow-x:auto;margin:12px 0}table{border-collapse:collapse;font-size:13px;min-width:60%}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
hr{border:0;border-top:1px solid var(--line);margin:28px 0}blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid var(--accent);background:var(--panel)}
"""


def inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", t)
    return t


def cells(line: str) -> list[str]:
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def to_html(md: str, title: str = "Report") -> str:
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(block))}</code></pre>")
        elif m := re.match(r"(#{1,6}) (.*)", line):
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"\|[\s:|-]+\|$", lines[i + 1].strip()):
            head, rows = cells(line), []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            out.append('<div class="table"><table><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
                                                          for r in rows) + "</tbody></table></div>")
            continue
        elif re.match(r"\s*(-|\d+\.) ", line):
            ordered = bool(re.match(r"\s*\d+\. ", line))
            items = []
            while i < len(lines) and (re.match(r"\s*(-|\d+\.) ", lines[i]) or (lines[i].startswith("  ") and lines[i].strip())):
                if re.match(r"\s*(-|\d+\.) ", lines[i]):
                    items.append(re.sub(r"^\s*(-|\d+\.) ", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        elif line.startswith(">"):
            out.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
        elif line.strip():
            para = [line]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"(#|\||```|---|\s*(-|\d+\.) |>)", lines[i + 1]):
                i += 1
                para.append(lines[i])
            out.append(f"<p>{inline(' '.join(p.strip() for p in para))}</p>")
        i += 1
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            f'initial-scale=1"><title>{html.escape(title)}</title><style>{STYLE}</style></head><body><main>'
            + "\n".join(out) + "</main></body></html>\n")


def write(run_dir: Path) -> Path:
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    title = next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")), "Report")
    out = run_dir / "report.html"
    out.write_text(to_html(md, title), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(write(Path(sys.argv[1])))
