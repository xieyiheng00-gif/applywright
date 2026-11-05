"""The result record of each application: every question on the form, its answer, who answered it.

Written to ``data/applications/app<ID>_<company>_<role>.md`` (readable) and ``.json`` (for tools),
refreshed after every fill, recorded answer, and status change, so the latest file always shows
what was sent or is about to be sent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import timing

PYTHON_SOURCES = ("fact:", "template", "derive:", "record:", "form:")
OPEN = {"needs_ai": "needs an AI-written answer", "needs_user": "needs your answer",
        "uncertain": "new question (unclassified)", "unmatched": "new question (unclassified)"}


def _not_entered(row: dict) -> bool:
    return str(row.get("fill") or "").startswith(("failed", "not tried"))


def open_reason(row: dict) -> str | None:
    if _not_entered(row):
        tries = row.get("attempts") or 0
        return (f"jobfill could not enter it ({tries} tries); the agent or you set it" if tries >= 2
                else "jobfill could not enter it yet")
    return OPEN.get(row["status"])


def answered_by(row: dict) -> str:
    """profile | ai | you | blank | open"""
    if row["status"] == "filled" and _not_entered(row):
        return "open"
    if row["status"] == "filled":
        src = row.get("source") or ""
        if src.startswith(PYTHON_SOURCES):
            return "profile"
        if src == "user":
            return "you"
        return "ai"   # "ai", or "answer_log:appN" (an earlier written answer reused)
    return "blank" if row["status"] == "skipped" else "open"


WHO = {"profile": "your profile (plain Python, no AI)", "ai": "AI", "you": "you", "blank": "left blank",
       "open": "still open"}


def build(store, app_id: int, book: timing.Book | None = None) -> dict:
    app = store.application(app_id)
    rows = store.answers(app_id)
    counts = {k: 0 for k in WHO}
    fields = []
    for i, r in enumerate(rows, 1):
        who = answered_by(r)
        counts[who] += 1
        fields.append({
            "n": i, "question": r["label"], "answer": r["value"], "answered_by": who, "source": r["source"],
            "question_id": r["question_id"], "tier": r["tier"], "status": r["status"],
            "typed": _typed(r), **({"open_reason": open_reason(r)} if who == "open" else {}),
        })
    answered = counts["profile"] + counts["ai"] + counts["you"]
    time_spent = (book or timing.Book(store.conn)).application(app_id)
    return {
        "application": {k: app[k] for k in ("id", "company", "role", "status", "ats", "url", "giveup_why",
                                            "giveup_reason", "created_at", "updated_at")},
        "summary": {"questions": len(rows), **counts,
                    "python_share": round(100 * counts["profile"] / answered) if answered else None},
        **({"time": time_spent} if time_spent else {}),
        "fields": fields,
        "generated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
    }


def _typed(r: dict) -> str:
    fill = r.get("fill")
    if fill is None:
        return "entered by the agent" if r["status"] == "filled" else "-"
    if fill == "ok":
        return "typed by jobfill"
    return fill


def _slug(text: str | None) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40].strip("-") or "untitled"


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    text = " ".join(str(value).split()).replace("|", "\\|")
    return text if len(text) <= 120 else text[:117] + "..."


def markdown(rep: dict) -> str:
    a, s = rep["application"], rep["summary"]
    lines = [f"# {a['company']} - {a['role'] or 'unknown role'}", "",
             f"- Application #{a['id']} | status: **{a['status']}** | system: {a['ats'] or 'unknown'}",
             f"- Posting: {a['url'] or '-'}",
             *([f"- **jobfill could not fill this one, so it is yours to finish by hand** ({a['giveup_why']}): "
                f"{a['giveup_reason']}"] if a.get("giveup_why") else []),
             f"- Started {a['created_at']} | report generated {rep['generated_at']}", "",
             "## Result", "",
             "| Answered by | Questions |", "|---|---|"]
    for key in ("profile", "ai", "you", "blank", "open"):
        lines.append(f"| {WHO[key][0].upper() + WHO[key][1:]} | {s[key]} |")
    lines.append(f"| **Total questions on the form** | **{s['questions']}** |")
    if s["python_share"] is not None:
        lines += ["", f"Plain Python answered {s['python_share']}% of the answered questions without any AI tokens."]
    if rep.get("time"):
        lines += ["", "## Time", "", *timing.section(rep["time"]), "",
                  f"Every step: `python -m jobfill app timing {a['id']}`"]
    lines += ["", "## Questions", "", "| # | Question | Answer | Answered by | Into the form |", "|---|---|---|---|---|"]
    for f in rep["fields"]:
        who = WHO[f["answered_by"]] if f["answered_by"] != "open" else f"open: {f.get('open_reason')}"
        lines.append(f"| {f['n']} | {_cell(f['question'])} | {_cell(f['answer'])} | {who} | {_cell(f['typed'])} |")
    written = [f for f in rep["fields"] if f["answered_by"] in ("ai", "you") and isinstance(f["answer"], str)
               and len(f["answer"]) > 120]
    if written:
        lines += ["", "## Written answers (full text)"]
        for f in written:
            lines += ["", f"### {f['n']}. {f['question']}", "", f"_{WHO[f['answered_by']]}, "
                      f"{len(f['answer'])} characters_", "", f["answer"]]
    still_open = [f for f in rep["fields"] if f["answered_by"] == "open"]
    if still_open:
        lines += ["", "## Still open", ""] + [f"- {f['question']} ({f.get('open_reason')})" for f in still_open]
    return "\n".join(lines) + "\n"


def write(store, data_dir: Path, app_id: int) -> dict:
    rep = build(store, app_id)
    folder = Path(data_dir) / "applications"
    folder.mkdir(parents=True, exist_ok=True)
    a = rep["application"]
    base = folder / f"app{a['id']}_{_slug(a['company'])}_{_slug(a['role'])}"
    for old in folder.glob(f"app{a['id']}_*"):   # role/company renamed: drop the stale file
        if old.stem != base.name:
            old.unlink()
    base.with_suffix(".md").write_text(markdown(rep), encoding="utf-8")
    base.with_suffix(".json").write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return {"markdown": str(base.with_suffix(".md")), "json": str(base.with_suffix(".json")), "summary": rep["summary"]}


def stats(store) -> dict:
    """Totals across every application: how much plain Python did versus the AI."""
    totals = {k: 0 for k in WHO}
    per_app = []
    book = timing.Book(store.conn)
    apps = store.applications()
    for app in apps:
        s = build(store, app["id"], book)["summary"]
        for k in WHO:
            totals[k] += s[k]
        per_app.append({"id": app["id"], "company": app["company"], "role": app["role"], "status": app["status"],
                        **{k: s[k] for k in ("questions", "profile", "ai", "you", "open", "python_share")}})
    answered = totals["profile"] + totals["ai"] + totals["you"]
    given_up = [{k: a[k] for k in ("id", "company", "role", "giveup_why", "giveup_reason")} for a in apps
                if a.get("giveup_why")]
    time_spent = book.overview(apps) if book.rows else None
    return {"applications": len(per_app), "totals": totals,
            "python_share": round(100 * totals["profile"] / answered) if answered else None, "per_application": per_app,
            **({"given_up": given_up} if given_up else {}),
            **({"time": {k: time_spent[k] for k in ("applications_timed", "median_active", "by_system", "gave_up",
                                                    "writing")}} if time_spent else {})}
