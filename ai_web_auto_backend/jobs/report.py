"""Self-contained HTML mission report generated from job.json + journal.jsonl.

Pure file operation — no server, no connection. Regenerating a report never
mutates the job.
"""

from __future__ import annotations

import html
from pathlib import Path

from .models import JobStatus
from .store import JobStore


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


_STATUS_COLORS = {
    JobStatus.COMPLETED.value: "#16a34a",
    JobStatus.FAILED.value: "#dc2626",
    JobStatus.INTERRUPTED.value: "#d97706",
    JobStatus.RUNNING.value: "#2563eb",
    JobStatus.CANCELLED.value: "#6b7280",
}

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Job {job_id} — {status}</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; margin: 2rem; color: #1f2937; }}
  h1 {{ font-size: 1.3rem; }} h2 {{ font-size: 1.05rem; margin-top: 1.8rem; }}
  .badge {{ padding: .15rem .6rem; border-radius: 999px; color: #fff; font-weight: 600; font-size: .85rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .85rem; }}
  th, td {{ text-align: left; padding: .35rem .55rem; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
  th {{ background: #f9fafb; }}
  code {{ background: #f3f4f6; padding: .05rem .3rem; border-radius: 4px; font-size: .8rem; }}
  .ok {{ color: #16a34a; }} .fail {{ color: #dc2626; }}
  .muted {{ color: #6b7280; }}
  img.shot {{ max-width: 480px; border: 1px solid #e5e7eb; margin: .4rem .4rem .4rem 0; }}
  .meta td:first-child {{ font-weight: 600; width: 11rem; }}
</style>
</head>
<body>
<h1><span class="badge" style="background:{status_color}">{status}</span> {objective}</h1>
<table class="meta">
<tr><td>Job</td><td><code>{job_id}</code></td></tr>
<tr><td>Start URL</td><td>{start_url}</td></tr>
<tr><td>Created / Started</td><td>{created_at} / {started_at}</td></tr>
<tr><td>Completed</td><td>{completed_at}</td></tr>
<tr><td>Duration</td><td>{duration_s}s &middot; steps journaled: {steps_done}/{max_steps} &middot; resumes: {resume_count}</td></tr>
<tr><td>Outcome</td><td class="{outcome_class}">{outcome}{error_line}</td></tr>
</table>

<h2>Action timeline</h2>
{timeline_table}

<h2>Screenshots</h2>
{shots_section}
<p class="muted">Report regenerated on demand from journal.jsonl — safe to re-run.</p>
</body>
</html>
"""


def generate(job_id: str, store: JobStore | None = None) -> Path:
    store = store or JobStore()
    spec = store.load(job_id)

    timeline_rows = []
    shots = []
    n_ok = n_fail = 0
    for ev in store.read_journal(job_id):
        etype = ev.get("type")
        if etype == "action_result":
            ok = bool(ev.get("ok"))
            n_ok += ok
            n_fail += (not ok)
            cls = "ok" if ok else "fail"
            status = "success" if ok else "failure"
            err = ev.get("error") or ""
            code = ev.get("error_code") or ""
            err_cell = f'<br><span class="muted"><code>{_esc(code)}</code> {_esc(err[:160])}</span>' if err else ""
            timeline_rows.append(
                f"<tr><td>{ev.get('step', '?')}</td>"
                f"<td>{_esc(ev.get('action', ''))}</td>"
                f"<td><code>{_esc(ev.get('selector') or '')}</code></td>"
                f"<td class=\"{cls}\">{status}</td>"
                f"<td>{err_cell}</td></tr>"
            )
        elif etype == "screenshot":
            rel = ev.get("path")
            if rel and (store.dir(job_id) / rel).exists():
                shots.append((ev.get("label", ""), rel))
        elif etype == "job_finished" and ev.get("screenshot"):
            rel = ev["screenshot"]
            if (store.dir(job_id) / rel).exists():
                shots.append(("final", rel))

    timeline_table = ("<p class='muted'>No actions were journaled.</p>")
    if timeline_rows:
        timeline_table = (
            "<table><tr><th>#</th><th>Action</th><th>Selector</th><th>Status</th><th>Error</th></tr>"
            + "".join(timeline_rows) + "</table>"
            f"<p class='muted'>{n_ok} succeeded, {n_fail} failed.</p>"
        )

    shots_section = ("<p class='muted'>None captured.</p>")
    if shots:
        shots_section = "".join(
            f'<div><div class="muted">{_esc(label)}</div>'
            f'<img class="shot" src="{_esc(rel)}" alt="{_esc(label)}"></div>'
            for label, rel in shots
        )

    error_line = ""
    outcome_class = "ok" if spec.success else "fail"
    if spec.success:
        outcome = "Objective achieved"
    else:
        outcome = "Not achieved"
        if spec.error:
            error_line = f' &mdash; <code>{_esc(spec.error_code or "UNKNOWN")}</code> {_esc(spec.error)}'

    out = store.dir(job_id) / "report.html"
    out.write_text(_TEMPLATE.format(
        job_id=_esc(job_id),
        objective=_esc(spec.objective),
        status=_esc(spec.status.value),
        status_color=_STATUS_COLORS.get(spec.status.value, "#6b7280"),
        start_url=_esc(spec.start_url),
        created_at=_esc(spec.created_at),
        started_at=_esc(spec.started_at or "-"),
        completed_at=_esc(spec.completed_at or "-"),
        duration_s=spec.duration_ms // 1000,
        steps_done=spec.steps_done,
        max_steps=spec.max_steps,
        resume_count=spec.resume_count,
        outcome=outcome,
        outcome_class=outcome_class,
        error_line=error_line,
        timeline_table=timeline_table,
        shots_section=shots_section,
    ), encoding="utf-8")
    return out
