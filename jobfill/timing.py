"""How long applications take, and who the time went to: a development tool, off unless JOBFILL_TIMING is set.

With it set, every jobfill command adds a row to the ``timing`` table when it starts and closes it when it ends,
so the agent does nothing extra to be measured, and a command that is killed still leaves its row. From that log:

- **jobfill**: the time inside a command (plain Python and the browser). Exact. `apply fill` also keeps
  where its time went (``phases``) and its slowest fields.
- **The agent**: the silence before a command is the agent working towards it (reading results, driving the
  browser, thinking, writing). It is charged to the application of the command it led to, which stays right
  when one agent works through a batch of applications: the commands of a batch interleave, so adding up the
  gaps of each application on its own would count the same minutes several times.
  A silence before `answer check` / `answer record` is writing that answer; before `learn`, `pending` and
  `match`, classifying new questions. A silence before a command with no application goes to the batch
  (``shared``).
- **The candidate**: `apply watch` and `login` run while the candidate acts, and the time around `login` is
  theirs too. Turn hooks (README, "Measuring time") mark that the agent ended its turn (``pause``) and that the
  candidate replied (``resume``); the wait lasts from one to the other, or to the next command.
- **Nobody**: a silence over ``IDLE_AFTER`` is charged to no one and ends the run (a batch, as a whole).

`app giveup` (for everyone, see cli.py) also leaves a row, so this says how much work came before it.

This module imports only the standard library: `pause` and `resume` run on every turn, and importing the rest of
jobfill (the question catalog) would add a third of a second to each one.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

IDLE_AFTER = 15 * 60   # seconds of silence that mean nobody is working on it
BURST = 3.0            # commands this close together came from one script, after one piece of thinking
KEEP_FIELDS = 5        # slowest fields kept per fill and per report
SLOW_FIELD = 0.5       # seconds; faster fields are not worth keeping
STEP = 0.5             # seconds; a shorter silence or phase is not worth a line of its own

PAUSE, RESUME, GIVEUP = "pause", "resume", "app giveup"
MARKS = {PAUSE, RESUME}
LOGGED = {"apply", "answer", "app", "resolve", "context", "login", "learn", "pending", "match", "question", "fact",
          "record", "choice"}
UNLOGGED = {"app stats", "app timing", "app list"}   # reading the results is not part of filling
CANDIDATE = {"apply watch", "login"}                 # these run while the candidate acts
WRITING = {"answer check", "answer record"}
CLASSIFYING = {"learn", "pending", "match", "question list", "question show", "question alias", "fact define"}
STARTS = {"apply start", "app start"}                # the application exists only once these have run

OFF = 'It is a development tool, off unless JOBFILL_TIMING=1 is set (README, "Measuring time").'

ACTIVE = ("python", "writing", "classifying", "agent")
LABEL = {"python": "jobfill (plain Python and the browser)", "writing": "Agent writing answers",
         "classifying": "Agent classifying new questions", "agent": "Agent, everything else",
         "candidate": "Waiting on you (login, the window)"}


# --------------------------------------------------------------------------- the clock of one command

class _Clock:
    def __init__(self, conn, name: str, app_id: int | None):
        self.conn, self.name, self.app_id = conn, name, app_id
        self.started = time.time()
        self.detail, self.phases, self.fields = {}, {}, []
        self.in_phase = False
        self.row = None
        self._write(lambda: conn.execute(
            "INSERT INTO timing (session, application_id, name, started) VALUES (?,?,?,?)",
            (_session(conn, name, app_id), app_id, name, self.started)).lastrowid)

    def _write(self, action) -> None:
        """Timing must never break the command it measures."""
        try:
            with self.conn:
                result = action()
            if self.row is None:
                self.row = result
        except sqlite3.Error:
            pass

    def save(self, seconds: float | None = None) -> None:
        if self.row is not None:
            self._write(lambda: self.conn.execute(
                "UPDATE timing SET application_id=?, seconds=?, detail=? WHERE id=?",
                (self.app_id, seconds, json.dumps(self.detail, ensure_ascii=False), self.row)))


_clock: _Clock | None = None


def own_session() -> str:
    """Which agent session this runs in; separate sessions on one database are accounted separately."""
    return os.environ.get("JOBFILL_SESSION") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""


def _session(conn, name: str, app_id: int | None) -> str:
    """`login` runs in the candidate's terminal, which may not know the session: it belongs to the agent's run."""
    own = own_session()
    if app_id and (name == "login" or not own):
        row = conn.execute("SELECT session FROM timing WHERE application_id=? ORDER BY id DESC LIMIT 1",
                           (app_id,)).fetchone()
        if row:
            return row[0]
    return own


def command_name(args) -> str | None:
    """The name a command is logged under, or None if it is not part of filling an application."""
    action = getattr(args, "action", None)
    name = f"{args.command} {action}" if action else args.command
    return name if args.command in LOGGED and name not in UNLOGGED else None


def enabled() -> bool:
    return os.environ.get("JOBFILL_TIMING", "") not in ("", "0")


def start(conn, name: str | None, app_id: int | None = None) -> None:
    global _clock
    _clock = _Clock(conn, name, app_id) if name and enabled() else None


def note(**detail) -> None:
    """Add to this command's row at once, so a report written during the command already has it."""
    if _clock:
        _clock.detail.update({k: v for k, v in detail.items() if v is not None})
        _clock.save()


def belongs_to(app_id: int | None) -> None:
    if _clock and app_id:
        _clock.app_id = app_id
        _clock.save()


def result(obj) -> None:
    """What the command printed: keep the part that says how it ended."""
    if not _clock or not isinstance(obj, dict):
        return
    keep = {k: obj[k] for k in ("handler", "blockers", "event", "paused", "submitted", "survey", "moved", "recorded")
            if obj.get(k) not in (None, [])}
    counts = {k: v for k, v in (obj.get("counts") or {}).items() if v}
    _clock.detail.update(keep, **({"counts": counts} if counts else {}))
    if _clock.name in STARTS:
        _clock.app_id = obj.get("app_id") or obj.get("id") or _clock.app_id


def finish(error: str | None = None) -> None:
    global _clock
    clock, _clock = _clock, None
    if clock is None:
        return
    if error:
        clock.detail["error"] = error[:200]
    seconds = round(time.time() - clock.started, 3)
    if clock.phases:
        other = seconds - sum(clock.phases.values())
        clock.detail["phases"] = {k: round(v, 2) for k, v in {**clock.phases, "other": max(other, 0)}.items()}
    slow = sorted((f for f in clock.fields if f["seconds"] >= SLOW_FIELD), key=lambda f: -f["seconds"])
    if slow:
        clock.detail["slowest"] = slow[:KEEP_FIELDS]
    if clock.fields:
        clock.detail["fields_typed"] = len(clock.fields)
    clock.save(seconds)


def spent(name: str, seconds: float) -> None:
    """Add time to a phase (for a stretch of code a `with` block cannot wrap)."""
    if _clock:
        _clock.phases[name] = _clock.phases.get(name, 0.0) + seconds


@contextmanager
def phase(name: str, label: str | None = None, kind: str | None = None):
    """Where a command's time goes. Inside another phase it adds nothing, so phases never count twice."""
    clock = _clock
    if clock is None or clock.in_phase:
        yield
        return
    clock.in_phase = True
    began = time.perf_counter()
    try:
        yield
    finally:
        seconds = time.perf_counter() - began
        clock.in_phase = False
        spent(name, seconds)
        if label is not None:
            clock.fields.append({"label": label[:80], "kind": kind, "seconds": round(seconds, 2)})


# --------------------------------------------------------------------------- from the log to who spent what

def load(conn) -> list[dict]:
    rows = conn.execute("SELECT session, application_id, name, started, seconds, detail FROM timing "
                        "ORDER BY started, id").fetchall()
    return [{"session": r[0], "application_id": r[1], "name": r[2], "started": r[3], "seconds": r[4],
             "detail": json.loads(r[5]) if r[5] else {}} for r in rows]


def _end(row: dict, next_start: float | None, now: float) -> float:
    """When a command ended. One with no end was killed (or is the one running now): at most until the next."""
    if row["seconds"] is not None:
        return row["started"] + row["seconds"]
    return min(now if next_start is None else next_start, row["started"] + IDLE_AFTER)


def _kind(row: dict) -> str:
    """What the agent was doing in the silence before this command."""
    if row["name"] in WRITING and row["detail"].get("source", "ai") == "ai":
        return "writing"
    return "classifying" if row["name"] in CLASSIFYING else "agent"


def _said(row: dict, unfinished: bool) -> str:
    d, bits = row["detail"], []
    if d.get("counts"):
        bits.append(", ".join(f"{k} {v}" for k, v in d["counts"].items()))
    if d.get("blockers"):
        bits.append("blocked: " + ", ".join(d["blockers"]))
    bits += [f"{k}: {d[k]}" for k in ("handler", "event", "paused") if d.get(k)]
    if "submitted" in d:
        bits.append("submitted" if d["submitted"] else "not submitted")
    if d.get("why"):
        bits.append(f"gave up: {d['why']}")
    if d.get("error"):
        bits.append("error: " + d["error"][:80])
    if unfinished:
        bits.append("did not finish (killed?)")
    return "; ".join(bits)


def account(rows: list[dict], now: float | None = None) -> list[dict]:
    """Turn the log into segments: {start, seconds, who, what, app, url, run, question, words, result}."""
    now = time.time() if now is None else now
    sessions: dict[str, list[dict]] = {}
    for r in rows:
        sessions.setdefault(r["session"], []).append(r)
    segments = []
    for session, seq in sessions.items():
        segments += _walk(session, seq, now)
    return sorted(segments, key=lambda s: s["start"])


def _walk(session: str, seq: list[dict], now: float) -> list[dict]:
    ends = [_end(r, seq[i + 1]["started"] if i + 1 < len(seq) else None, now) for i, r in enumerate(seq)]
    out: list[dict] = []
    run, waiting, last_end, prev = 0, False, None, None

    def add(row: dict | None, start: float, seconds: float, who: str, what: str, *, app="row", url="row", **extra):
        detail = row["detail"] if row else {}
        owner = row["application_id"] if row and app == "row" else (None if app == "row" else app)
        out.append({"start": start, "seconds": seconds, "who": who, "what": what, "app": owner,
                    "url": (detail.get("url") if owner is None else None) if url == "row" else url,
                    "run": f"{session}#{run}", "question": None, "words": None, "result": "", **extra})

    for i, r in enumerate(seq):
        if last_end is not None and r["started"] > last_end:
            gap = r["started"] - last_end
            if gap > IDLE_AFTER:
                run += 1
            elif waiting:                 # one reply answers for the whole batch: the wait is the batch's
                add(None, last_end, gap, "candidate", "waiting on you", app=None, url=None)
            elif "login" in (r["name"], prev["name"] if prev else None):
                add(r, last_end, gap, "candidate", "waiting on you (login)")
            elif _kind(r) == "writing":
                _shares(add, seq, ends, i, last_end, gap)
            else:
                kind = _kind(r)
                add(None if r["name"] in MARKS else r, last_end, gap, kind,
                    "classifying new questions" if kind == "classifying" else f"agent, before {r['name']}")
        if r["name"] not in MARKS:
            unfinished = r["seconds"] is None and i + 1 < len(seq)
            add(r, r["started"], ends[i] - r["started"], "candidate" if r["name"] in CANDIDATE else "python", r["name"],
                result=_said(r, unfinished))
            prev = r
        waiting = r["name"] == PAUSE
        last_end = ends[i] if last_end is None else max(last_end, ends[i])
    return out


def _shares(add, seq: list[dict], ends: list[float], i: int, start: float, gap: float) -> None:
    """The agent writes several answers, then checks them in one script: the silence before the first check was
    spent on all of them. Each gets a share by its length, so per-answer times mean something."""
    burst = [seq[i]]
    for j in range(i + 1, len(seq)):
        if seq[j]["name"] not in WRITING or seq[j]["started"] - ends[j - 1] >= BURST:
            break
        burst.append(seq[j])
    jobs: dict[tuple, dict] = {}
    for b in burst:
        if _kind(b) == "writing":
            jobs.setdefault((b["application_id"], b["detail"].get("question")), b)
    words = sum(b["detail"].get("words") or 0 for b in jobs.values())
    at = start
    for (app, question), b in jobs.items():
        share = gap * ((b["detail"].get("words") or 0) / words if words else 1 / len(jobs))
        add(b, at, share, "writing", f"writing {question}", app=app, question=question, words=b["detail"].get("words"))
        at += share


def totals(segments: list[dict]) -> dict:
    out = {k: 0.0 for k in (*ACTIVE, "candidate")}
    for s in segments:
        out[s["who"]] += s["seconds"]
    out["active"] = sum(out[k] for k in ACTIVE)
    out["elapsed"] = _elapsed(segments)
    return {k: round(v, 1) for k, v in out.items()}


def _elapsed(segments: list[dict]) -> float:
    """Clock time, first step to last, leaving out the silences between runs."""
    runs: dict[str, list[float]] = {}
    for s in segments:
        span = runs.setdefault(s["run"], [s["start"], s["start"] + s["seconds"]])
        span[0], span[1] = min(span[0], s["start"]), max(span[1], s["start"] + s["seconds"])
    return sum(b - a for a, b in runs.values())


def _median(values: list[float]) -> float | None:
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().replace(microsecond=0).isoformat()


class Book:
    """The whole log, accounted once: reports ask it about one application or all of them."""

    def __init__(self, conn, now: float | None = None):
        self.rows = load(conn)
        self.segments = account(self.rows, now)

    def _of(self, app_id: int) -> list[dict]:
        return [s for s in self.segments if s["app"] == app_id]

    def _gave_up(self, row: dict, segments: list[dict]) -> dict:
        t = totals([s for s in segments if s["start"] < row["started"]])
        return {"at": _iso(row["started"]), "why": row["detail"].get("why"), "reason": row["detail"].get("reason"),
                "active": t["active"], "elapsed": t["elapsed"]}

    def application(self, app_id: int, timeline: bool = False) -> dict | None:
        """This application's time, or None if it started before jobfill kept time."""
        segments = self._of(app_id)
        if not segments:
            return None
        rows = [r for r in self.rows if r["application_id"] == app_id]
        out = {**totals(segments), "started": _iso(segments[0]["start"])}
        answers: dict[str, dict] = {}
        for s in segments:
            if s["who"] == "writing":
                a = answers.setdefault(s["question"], {"question": s["question"], "words": s["words"], "seconds": 0.0})
                a["seconds"] += s["seconds"]
                a["words"] = s["words"] or a["words"]
        if answers:
            out["answers"] = [{**a, "seconds": round(a["seconds"], 1)} for a in answers.values()]
        blocked = next((r for r in rows if r["detail"].get("blockers")), None)
        if blocked:
            out["first_blocker"] = {"blockers": blocked["detail"]["blockers"],
                                    "after": totals([s for s in segments if s["start"] <= blocked["started"]])["active"]}
        gave_up = next((r for r in reversed(rows) if r["name"] == GIVEUP), None)
        if gave_up:
            out["gave_up"] = self._gave_up(gave_up, segments)
        phases, slow = _fills(rows)
        if phases:
            out["phases"] = phases
        if slow:
            out["slowest_fields"] = slow
        if timeline:
            first = segments[0]["start"]
            out["timeline"] = [{"clock": datetime.fromtimestamp(s["start"]).strftime("%H:%M:%S"),
                                "from_start": round(s["start"] - first, 1), "who": s["who"], "what": s["what"],
                                "seconds": round(s["seconds"], 1), "result": s["result"]} for s in segments
                               if s["who"] == "python" or s["seconds"] >= STEP]
        return out

    def runs(self) -> list[dict]:
        """Each batch as a whole. ``shared`` is agent work not tied to one application; ``per_application`` shares
        all the work out, the honest cost of one application in that batch."""
        groups: dict[str, list[dict]] = {}
        for s in self.segments:
            groups.setdefault(s["run"], []).append(s)
        out = []
        for segments in groups.values():
            apps = sorted({s["app"] for s in segments if s["app"]})
            if not apps and not any(s["url"] for s in segments):
                continue   # profile work, no application in it
            t = totals(segments)
            shared = totals([s for s in segments if not s["app"] and not s["url"]])["active"]
            out.append({"started": _iso(segments[0]["start"]), "applications": apps, **t, "shared": shared,
                        "per_application": round(t["active"] / len(apps), 1) if apps else None})
        return sorted(out, key=lambda r: r["started"])

    def overview(self, apps: list[dict]) -> dict:
        timed, gave_up, writing = [], [], []
        systems: dict[str, list[float]] = {}
        for a in sorted(apps, key=lambda a: a["id"]):
            info = self.application(a["id"])
            if not info:
                continue
            who = {"id": a["id"], "company": a["company"], "role": a["role"]}
            timed.append({**who, "system": a.get("ats"), "status": a["status"],
                          **{k: info[k] for k in ("active", *ACTIVE, "candidate", "elapsed")}})
            systems.setdefault(a.get("ats") or "unknown", []).append(info["active"])
            writing += [{**who, **x} for x in info.get("answers", [])]
            if info.get("gave_up"):
                gave_up.append({**who, "system": a.get("ats"), **info["gave_up"]})
        phases, slow = _fills(self.rows)
        return {"applications_timed": len(timed), "median_active": _median([t["active"] for t in timed]),
                "runs": self.runs(), "applications": timed,
                "by_system": {k: {"applications": len(v), "median_active": _median(v)}
                              for k, v in sorted(systems.items())},
                "gave_up": gave_up,
                "stopped_at_start": _stopped(self.rows),
                "writing": _writing(writing),
                "where_jobfill_spends_it": phases,
                "slowest_fields": slow}


def _fills(rows: list[dict]) -> tuple[dict, list[dict]]:
    """Where the commands' own time went, the slowest first; and the slowest fields."""
    phases: dict[str, float] = {}
    slow: list[dict] = []
    for r in rows:
        for k, v in r["detail"].get("phases", {}).items():
            phases[k] = phases.get(k, 0.0) + v
        slow += r["detail"].get("slowest", [])
    ordered = {k: round(v, 1) for k, v in sorted(phases.items(), key=lambda kv: -kv[1])}
    return ordered, sorted(slow, key=lambda f: -f["seconds"])[:KEEP_FIELDS]


def _stopped(rows: list[dict]) -> list[dict]:
    """`apply start` runs that ended the matter: an unsupported system, or a link that is not a job."""
    return [{"url": r["detail"].get("url"), "said": r["detail"].get("handler") or r["detail"].get("error"),
             "seconds": round(r["seconds"] or 0, 1), "at": _iso(r["started"])}
            for r in rows if r["name"] == "apply start" and not r["application_id"]
            and (r["detail"].get("handler") == "unsupported" or r["detail"].get("error"))]


def _writing(answers: list[dict]) -> dict:
    spent = sum(a["seconds"] for a in answers)
    return {"answers": len(answers), "seconds": round(spent, 1),
            "average": round(spent / len(answers), 1) if answers else None,
            "slowest": sorted(answers, key=lambda a: -a["seconds"])[:KEEP_FIELDS]}


# --------------------------------------------------------------------------- what people read

def clock(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = round(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


def _gave_up_line(g: dict) -> str:
    why = g.get("why") or "unknown"
    reason = f": {g['reason']}" if g.get("reason") else ""
    return f"**Gave up after {clock(g['active'])} of work** ({clock(g['elapsed'])} on the clock). {why}{reason}"


def section(info: dict) -> list[str]:
    """The time of one application, as Markdown lines (also part of the application's report)."""
    lines = ["| | Time |", "|---|---|", f"| **Working (jobfill + agent)** | **{clock(info['active'])}** |"]
    lines += [f"| {LABEL[k]} | {clock(info[k])} |" for k in (*ACTIVE, "candidate")]
    lines.append(f"| Clock time, first step to last | {clock(info['elapsed'])} |")
    if info.get("gave_up"):
        lines += ["", _gave_up_line(info["gave_up"])]
    if info.get("first_blocker"):
        b = info["first_blocker"]
        lines += ["", f"First blocker ({', '.join(b['blockers'])}) after {clock(b['after'])} of work."]
    if info.get("answers"):
        lines += ["", "| Written answer | Words | Agent's time |", "|---|---|---|"]
        lines += [f"| {a['question']} | {a['words'] or '-'} | {clock(a['seconds'])} |" for a in info["answers"]]
    return lines


def _field_lines(slow: list[dict]) -> list[str]:
    return ["| Slowest field | Kind | Time |", "|---|---|---|"] + [
        f"| {f['label']} | {f['kind'] or '-'} | {clock(f['seconds']) if f['seconds'] >= 1 else str(f['seconds']) + 's'} |"
        for f in slow]


def markdown_application(app: dict, info: dict) -> str:
    lines = [f"# Time: {app['company']} - {app['role'] or 'unknown role'} (application {app['id']})", ""]
    lines += section(info)
    if info.get("phases"):
        lines += ["", "| Inside jobfill | Time |", "|---|---|"]
        lines += [f"| {k} | {clock(v)} |" for k, v in info["phases"].items() if v >= STEP]
    if info.get("slowest_fields"):
        lines += [""] + _field_lines(info["slowest_fields"])
    lines += ["", "## Step by step", "", "| Clock | From start | Who | What | Time | Result |", "|---|---|---|---|---|---|"]
    lines += [f"| {s['clock']} | {clock(s['from_start'])} | {s['who']} | {s['what']} | {clock(s['seconds'])} | {s['result']} |"
              for s in info.get("timeline", [])]
    return "\n".join(lines) + "\n"


def markdown_overview(ov: dict) -> str:
    if not ov["runs"] and not ov["applications"]:
        return f"No timing recorded. {OFF}\n"
    lines = ["# Time spent on applications", "",
             f"Applications timed: {ov['applications_timed']}. Typical work per application: "
             f"{clock(ov['median_active'])} (jobfill + agent, without waiting on you).", "", "## Runs (a batch, as a whole)", "",
             "| Started | Applications | Clock | Work | Per application | jobfill | Writing | Classifying | Agent, other | "
             "Waiting on you |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in ov["runs"]:
        lines.append(f"| {r['started'][:16].replace('T', ' ')} | {', '.join(map(str, r['applications'])) or '-'} | "
                     f"{clock(r['elapsed'])} | {clock(r['active'])} | {clock(r['per_application'])} | {clock(r['python'])} | "
                     f"{clock(r['writing'])} | {clock(r['classifying'])} | {clock(r['agent'])} | {clock(r['candidate'])} |")
    lines += ["", "## Applications", "", "| # | Company | System | Status | Work | jobfill | Writing | Agent, other | "
              "Clock |", "|---|---|---|---|---|---|---|---|---|"]
    for a in ov["applications"]:
        lines.append(f"| {a['id']} | {a['company']} | {a['system'] or '-'} | {a['status']} | {clock(a['active'])} | "
                     f"{clock(a['python'])} | {clock(a['writing'])} | {clock(a['agent'] + a['classifying'])} | "
                     f"{clock(a['elapsed'])} |")
    if ov["by_system"]:
        lines += ["", "## By system", "", "| System | Applications | Typical work |", "|---|---|---|"]
        lines += [f"| {k} | {v['applications']} | {clock(v['median_active'])} |" for k, v in ov["by_system"].items()]
    if ov["gave_up"]:
        lines += ["", "## Given up: not fillable by a robot", "", "| # | Company | System | Why | Work before giving up | "
                  "Clock | Reason |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {g['id']} | {g['company']} | {g['system'] or '-'} | {g['why']} | "
                  f"{clock(g['active'])} | {clock(g['elapsed'])} | {g['reason'] or ''} |" for g in ov["gave_up"]]
    if ov["stopped_at_start"]:
        lines += ["", "## Stopped at `apply start`", "", "| Link | jobfill said | Time |", "|---|---|---|"]
        lines += [f"| {s['url']} | {s['said']} | {clock(s['seconds'])} |" for s in ov["stopped_at_start"]]
    w = ov["writing"]
    if w["answers"]:
        lines += ["", "## Written answers", "", f"Answers written: {w['answers']}. The agent spent {clock(w['seconds'])}, "
                  f"{clock(w['average'])} each on average.", "", "| Application | Question | Words | Time |", "|---|---|---|---|"]
        lines += [f"| {a['id']} {a['company']} | {a['question']} | {a['words'] or '-'} | {clock(a['seconds'])} |"
                  for a in w["slowest"]]
    if ov["where_jobfill_spends_it"]:
        lines += ["", "## Inside jobfill", "", "| Step | Time |", "|---|---|"]
        lines += [f"| {k} | {clock(v)} |" for k, v in ov["where_jobfill_spends_it"].items() if v >= STEP]
    if ov["slowest_fields"]:
        lines += [""] + _field_lines(ov["slowest_fields"])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- turn marks (hooks)

def database() -> Path:
    """Same place as ``db.default_db_path`` (importing db would load the catalog; tests check they agree)."""
    env = os.environ.get("JOBFILL_DB")
    return Path(env) if env else Path(__file__).resolve().parent.parent / "data" / "jobfill.db"


def mark(name: str, session: str, when: float | None = None) -> None:
    """A turn boundary. Before a session's first command it means nothing, so unrelated sessions log nothing."""
    path = database()
    if not path.exists():
        return
    conn = sqlite3.connect(path, timeout=2)
    try:
        if conn.execute("SELECT 1 FROM timing WHERE session=? AND name NOT IN (?, ?) LIMIT 1",
                        (session, PAUSE, RESUME)).fetchone():
            with conn:
                conn.execute("INSERT INTO timing (session, name, started, seconds) VALUES (?,?,?,0)",
                             (session, name, time.time() if when is None else when))
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """`python -m jobfill.timing pause|resume [--hook]`: the agent's turn ended / the candidate replied.

    `--hook`: the session id comes as JSON on stdin, as Claude Code passes it. It prints nothing and never fails:
    a hook's output would reach the conversation, and timing must never get in the way of an application.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in MARKS:
        print("usage: python -m jobfill.timing pause|resume [--hook]", file=sys.stderr)
        return 2
    session = None
    if "--hook" in args:
        try:
            session = json.load(sys.stdin).get("session_id")
        except (ValueError, OSError, AttributeError):
            pass
    try:
        mark(args[0], session or own_session())
    except sqlite3.Error:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
