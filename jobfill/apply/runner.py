"""`jobfill apply ...`: start an application from a URL and fill its form with plain Python."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from .. import report as results
from .. import resumes
from ..db import ROOT
from ..resolver import Resolver
from ..text import normalize
from . import adapters, browser, pages
from .detect import Detection, detect
from .forms import NoExactChoice, extract, fill, is_empty, to_resolver_field

MAX_PASSES = 4   # re-read the form after filling: answers can reveal follow-up questions
MAX_FILL_ATTEMPTS = 2   # same field, same value: a second failure means retrying will not help


class ApplyError(Exception):
    def __init__(self, message: str, code: int = 2, **extra):
        super().__init__(message)
        self.payload = {"error": message, **extra}
        self.code = code


def state_dir(data_dir: Path, app_id: int) -> Path:
    d = data_dir / "apply" / f"app{app_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state(data_dir: Path, app_id: int) -> dict:
    p = state_dir(data_dir, app_id) / "state.json"
    if not p.exists():
        raise ApplyError(f"application {app_id} was not started with `jobfill apply start`")
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(data_dir: Path, app_id: int, state: dict) -> None:
    (state_dir(data_dir, app_id) / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


NEXT_STEP = {
    "python": "Run `python -m jobfill apply fill --app {id}`.",
    "ai": "Fill it in the built-in browser following /apply step 3 (answers still come from `jobfill resolve`).",
    "follow": "Open the listing, find the employer's own apply link, and run `apply start` on it.",
    "agent_check": "Open the page in the built-in browser: if it is not a job posting, report the error; "
                   "otherwise rerun `apply start` with the real apply link, or with --ats custom.",
    "unsupported": "Not supported yet; tell the candidate to apply manually.",
}


# --------------------------------------------------------------------------- start

def start(store, data_dir: Path, url: str, *, company=None, role=None, source=None, referrer=None,
          ats=None, network=True, resume=None) -> dict:
    d: Detection = detect(url, network=network)
    if ats:   # the agent looked at the page and decided
        d.ats = ats
        d.kind = "job"
        # "generic": a portal that needs an account, filled in the jobfill window so the login carries over
        d.handler = "python" if ats in adapters.ADAPTERS else "ai"
        d.apply_url = d.apply_url or url
    if d.kind == "not_job":
        raise ApplyError(f"Not a job posting: {d.reason}", code=3, detection=d.to_dict())
    out = {"detection": d.to_dict(), "handler": d.handler}
    if d.handler in ("unsupported", "follow", "agent_check"):
        out["next"] = NEXT_STEP[d.handler]
        return out
    company = company or d.company
    if not company:
        raise ApplyError("Could not tell the company name; pass --company.", detection=d.to_dict())
    posting_file = None
    try:
        cv = resumes.choose(store, ROOT, company, role or d.role, resume)
    except FileNotFoundError as e:
        raise ApplyError(str(e), detection=d.to_dict())
    app_id = store.start_application(company, role or d.role, url=url, ats=d.ats, source=source, referrer=referrer,
                                     posting=d.posting, resume=cv["file"],
                                     country=None)
    if d.posting:
        posting_file = state_dir(data_dir, app_id) / "posting.txt"
        posting_file.write_text(d.posting, encoding="utf-8")
    save_state(data_dir, app_id, {"url": url, "apply_url": d.apply_url or url, "ats": d.ats, "handler": d.handler,
                                  "filled_once": False, "tag": f"{browser.TAG_PREFIX}{app_id}"})
    prior = store.same_company_apps(company, exclude_id=app_id)
    out.update({"app_id": app_id, "company": company, "role": role or d.role, "location": d.location,
                "posting_file": str(posting_file) if posting_file else None,
                "resume": {k: v for k, v in cv.items() if v is not None},
                "previous_applications_to_company": [{"id": a["id"], "role": a["role"], "status": a["status"]}
                                                     for a in prior],
                "next": NEXT_STEP[d.handler].format(id=app_id)})
    return out


# --------------------------------------------------------------------------- fill

def fill_frame(frame, store, app_id: int, adapter, *, overwrite: bool | str, files: bool = True,
               today: date | None = None, dry_run: bool = False, typed: dict | None = None,
               asked: dict | None = None) -> dict:
    """Fill everything the database can answer on the current page. Works on any Playwright frame.

    ``overwrite``: True replaces values already in the form (a page jobfill has just opened, where nobody
    typed yet); "own" re-types only values jobfill typed itself; False keeps every value in the form.
    ``typed`` maps a field's answer key to the value jobfill left in it, read back after typing, and is
    updated in place. With it, a value the candidate typed or changed is recognized: it is kept, never
    overwritten, and recorded as their answer (an open question they answered is not asked again).
    ``dry_run`` reads and resolves the form without typing anything (a preview).
    ``asked`` maps a field's answer key to the stored answer no option exactly matched (left for the candidate);
    their pick, made in the window or through `apply set`, is then remembered for that answer on other forms.
    """
    report: dict = {"fields": {}, "passes": 0}
    ai_context: dict | None = None
    seen: set[str] = set()
    attempts: list[tuple[str, dict, object]] = []   # (field id, field, value): results recorded after the check
    theirs: list[tuple[dict, dict]] = []             # (field, resolver result): answers the candidate gave
    logged = {a["field_key"]: a for a in store.answers(app_id)} if typed is not None else {}
    # Repeating sections first (Workday's Work Experience / Education "Add"): one block per stored entry.
    added = adapter.expand(frame, store, dry_run=dry_run)
    if added:
        report["added"] = added
    for _ in range(MAX_PASSES):
        fields = extract(frame, adapter.root_selector, known=seen)
        new = [f for f in fields if f["id"] not in seen]
        if not new:
            break
        report["passes"] += 1
        seen.update(f["id"] for f in fields)
        by_id = {f["id"]: f for f in new}
        result = Resolver(store, app_id, today=today, log=not dry_run).resolve([to_resolver_field(f) for f in new])
        if result.get("ai_context"):
            ai_context = result["ai_context"]
        # Upload files first: some systems (Lever) parse the resume and prefill other fields.
        for r in sorted(result["fields"], key=lambda r: by_id[r["id"]]["kind"] != "file"):
            f = by_id[r["id"]]
            entry = {k: v for k, v in r.items() if k not in ("raw", "source", "match")}
            entry["kind"] = f["kind"]
            owner = None if dry_run or overwrite is True else _owner(store, f, r, typed, logged)
            if owner is None and asked and _key(store, f) in asked and not is_empty(f) and typed is not None:
                owner = "candidate"   # jobfill asked and left it empty: what is there now is their pick
            if owner == "candidate":
                entry = {"label": f["label"], "question_id": r.get("question_id"), "kind": f["kind"],
                         "status": "filled", "value": f["value"], "fill": "kept (the candidate's answer)",
                         "by": "candidate"}
                theirs.append((f, r))
            elif r.get("no_exact"):
                if asked is not None and not dry_run:
                    asked[_key(store, f)] = {"question_id": r.get("question_id"), "value": r.get("raw")}
            elif r["status"] == "filled":
                if dry_run:
                    entry["fill"] = "would fill"
                elif f["kind"] == "file" and not files:
                    entry["fill"] = "skipped (--no-files)"
                elif f["kind"] == "file" and not is_empty(f):
                    # Uploading again would add a second copy (Workday keeps every file), never replace it.
                    entry["fill"] = f"kept (already uploaded: {f['value']})"
                    if Path(str(r["value"])).name != f["value"]:
                        # Another file than the resolved one (set by hand or `apply set`): the record names it.
                        entry["value"] = f["value"]
                        store.record_answer(app_id, _key(store, f), f["label"], f["value"],
                                            question_id=r.get("question_id"), source="user", tier=r.get("tier"),
                                            status="filled", fill=entry["fill"], section=f.get("section"),
                                            idx=f.get("index"))
                elif not is_empty(f) and not (overwrite is True or (overwrite == "own" and owner == "jobfill")):
                    entry["fill"] = "kept (already has a value)"
                elif (tried := store.fill_attempts(app_id, _key(store, f), r["value"])) >= MAX_FILL_ATTEMPTS:
                    entry["fill"] = f"not tried: failed {tried} times with this value"
                else:
                    # The question tells a menu adapter how to read its choices ("Social Media" for LinkedIn).
                    # A search-and-pick menu takes a list (skills) one entry at a time, not as one joined text.
                    value = r["raw"] if f["kind"] == "selectinput" and isinstance(r.get("raw"), list) else r["value"]
                    try:
                        ok, note = fill(frame, {**f, "question_id": r.get("question_id"),
                                                "alternatives": r.get("alternatives")}, value, root=ROOT,
                                        after=adapter.after_fill, custom=adapter.custom_fill)
                    except NoExactChoice as e:
                        _ask_candidate(store, app_id, f, r, entry, e, asked)
                        report["fields"][r["id"]] = entry
                        continue
                    entry["fill"] = "ok" if ok else f"failed: {note}"
                    if ok and note:
                        entry["note"] = note   # filled, with something left out (a skill the menu lacks)
                    if ok and f["kind"] == "file":
                        adapter.after_upload(frame)
                if not dry_run:
                    attempts.append((r["id"], f, r["value"]))
            report["fields"][r["id"]] = entry
        if dry_run:
            break   # nothing typed, so no follow-up questions can appear
    # Verify: anything we "filled" that is still empty did not take.
    final = {f["id"]: f for f in extract(frame, adapter.root_selector, read_options=False)}
    for fid, entry in report["fields"].items():
        if entry.get("fill") == "ok" and fid in final and is_empty(final[fid]) and entry["kind"] != "checkbox":
            entry["fill"] = "failed: value did not stick"
    # One result per field and run, after the check: recording "ok" first and "did not stick" later
    # would reset and bump the failure count every run, so it never reached the hand-off.
    for fid, f, value in attempts:
        entry = report["fields"][fid]
        _note(store, app_id, f, entry, value)
        if typed is not None and not str(entry["fill"]).startswith(("kept", "skipped", "not tried")) \
                and fid in final:
            typed[_key(store, f)] = final[fid]["value"]   # what jobfill left there, ok or not
    company = store.application(app_id)["company"]
    for f, r in theirs:
        store.record_answer(app_id, _key(store, f), f["label"], f["value"], question_id=r.get("question_id"),
                            source="user", tier=r.get("tier"), status="filled", fill="kept (the candidate's answer)",
                            section=f.get("section"), idx=f.get("index"))
        if asked is not None and (a := asked.pop(_key(store, f), None)):
            store.remember_choice(a["question_id"], a["value"], f["value"], company)   # picked in the window
    if ai_context:
        report["ai_context"] = ai_context
    return report


def _key(store, f: dict) -> str:
    return store.answer_key(f["label"], f.get("section"), f.get("index"))


def _flat(value) -> str:
    return normalize(", ".join(map(str, value)) if isinstance(value, list) else str(value))


def _close(a, b) -> bool:
    """Same answer as displayed: "United States +1" in a dropdown is the stored "United States"."""
    x, y = _flat(a), _flat(b)
    return x == y or bool(x and y and (x in y or y in x))


def _ask_candidate(store, app_id: int, f: dict, r: dict, entry: dict, e: NoExactChoice, asked: dict | None) -> None:
    """No option is exactly the stored answer: leave the field empty and ask the candidate."""
    entry.update(status="needs_user", no_exact=True, choices=e.offered, fill="not filled: no exact choice",
                 ask=f"No choice for '{f['label']}' is exactly your stored answer ({e.value}). "
                     f"Closest: {e.offered}. Pick one in the window, or say which; it is remembered for other forms.")
    store.record_answer(app_id, _key(store, f), f["label"], None, question_id=r.get("question_id"),
                        source=r.get("source") or "", tier=r.get("tier"), status="needs_user",
                        fill="not filled: no exact choice", section=f.get("section"), idx=f.get("index"))
    if asked is not None:
        asked[_key(store, f)] = {"question_id": r.get("question_id"), "value": e.value}


def _owner(store, f: dict, r: dict, typed: dict | None, logged: dict) -> str | None:
    """Whose value is in this field: "jobfill", "candidate", or None (empty, or what the profile says anyway).

    jobfill knows what it left in each field (``typed``); any other value is the candidate's, unless it
    already matches the profile's answer (a portal that prefills from the candidate's account).
    """
    if typed is None or is_empty(f):
        return None
    key = _key(store, f)
    if key in typed:
        return "jobfill" if _flat(f["value"]) == _flat(typed[key]) else "candidate"
    if r["status"] == "filled" and _close(f["value"], r["value"]):
        return None
    row = logged.get(key)
    if row and row["fill"] == "ok" and _close(f["value"], row["value"]):
        return "jobfill"   # typed before jobfill kept track of its own values
    return "candidate"


def _note(store, app_id: int, f: dict, entry: dict, value) -> None:
    """Record the fill result; after MAX_FILL_ATTEMPTS failures with the same value, hand the field off.

    A deterministic failure (the option is not in the list, a widget jobfill cannot operate) will not go
    away by retrying, so the agent decides instead: pick the right option, or ask the candidate.
    """
    attempts = store.note_fill(app_id, _key(store, f), entry["fill"], value)
    if attempts >= MAX_FILL_ATTEMPTS:
        entry.update(status="handoff", attempts=attempts, options=f.get("options") or None)


def summarize(report: dict, app_id: int) -> dict:
    fields = report["fields"]
    pick = lambda status: [v | {"field": k} for k, v in fields.items() if v["status"] == status]
    out = {
        "app_id": app_id,
        "blockers": report.get("blockers", []),
        "counts": {s: len(pick(s)) for s in ("filled", "needs_ai", "needs_user", "uncertain", "unmatched", "skipped",
                                             "handoff")},
        # Failed once: the next `apply fill` tries once more.
        "fill_failed": [{"field": k, "label": v["label"], "value": v.get("value"), "why": v["fill"]}
                        for k, v in fields.items() if str(v.get("fill", "")).startswith("failed")
                        and v["status"] != "handoff"],
        # Failed twice with the same value: Python stops; the agent decides or asks the candidate.
        "handoff": [{"field": v["field"], "label": v["label"], "value": v.get("value"), "options": v.get("options"),
                     "why": v["fill"], "attempts": v.get("attempts")} for v in pick("handoff")],
        "needs_user": [{"field": v["field"], "label": v["label"], "ask": v.get("ask"), "fact": v.get("fact"),
                        "choices": v.get("choices"), "store": v.get("store"), "sensitive": v.get("sensitive"),
                        # No exact option: the candidate picks in the window, or tells the agent (`apply set`).
                        "set_with": f"python -m jobfill apply set --app {app_id} --field {v['field']} "
                                    "--value \"<their choice>\"" if v.get("no_exact") else None,
                        # The form's own words under the field ("enter None if it doesn't apply", "in USD").
                        "instructions": v.get("instructions")}
                       for v in pick("needs_user")],
        "uncertain": [{"field": v["field"], "label": v["label"], "candidates": v.get("candidates"),
                       "instructions": v.get("instructions")} for v in pick("uncertain")],
        "unmatched": [{"field": v["field"], "label": v["label"], "instructions": v.get("instructions")}
                      for v in pick("unmatched")],
        # Filled, but not completely (e.g. skills the menu has no entry for): the agent or candidate decides.
        "partly": [{"field": k, "label": v["label"], "note": v["note"]} for k, v in fields.items() if v.get("note")
                   and v.get("fill") == "ok"],
        "added": report.get("added"),
        "needs_ai": [{"field": v["field"], **v["ai_task"]} for v in pick("needs_ai")],
        # Answered by the candidate in the window: kept as they are, never asked again or overwritten.
        "by_candidate": [{"field": k, "label": v["label"], "value": v.get("value")}
                         for k, v in fields.items() if v.get("by") == "candidate"],
        "page": report.get("page"),
    }
    if report.get("moved_by_candidate"):
        out["moved_by_candidate"] = True
        out["note"] = ("The candidate moved on to this page themselves; jobfill filled what was left and kept "
                       "their answers. Carry on from here.")
    if out["needs_ai"] and report.get("ai_context"):
        out["ai_context"] = report["ai_context"]
    if "login_required" in out["blockers"]:
        out["next"] = (f"This site needs an account. Ask the candidate to run `python -m jobfill login --app {app_id}` "
                       "in their own terminal (it creates or signs in to the account), then run `apply fill` again. "
                       "Never run login yourself.")
    return {k: v for k, v in out.items() if v not in ([], None)}


def open_page(ctx, state: dict, adapter):
    page, fresh = browser.find_page(ctx, state["tag"], state["apply_url"])
    if fresh:
        adapter.open(page, state["apply_url"])
        page.evaluate("(t) => { window.name = t; }", state["tag"])
    page.bring_to_front()
    return page, fresh


# --------------------------------------------------------------------------- where the candidate is

GATES = {"login_required", "job_closed", "email_gate", "consent_required"}


def place(page, adapter, state: dict) -> dict:
    """Which of this application's pages the window shows (see pages.py): current, new, earlier or unknown."""
    info = pages.settle(page, adapter)
    plist = state.setdefault("pages", [])
    if pages.blank(info):   # nothing to read (still loading, or a page without questions)
        return {"where": "unknown", "index": max(len(plist) - 1, 0), "info": info}
    where, index = pages.locate(plist, info)
    return {"where": where, "index": index, "info": info}


def paused(state: dict, spot: dict, app_id: int) -> dict:
    """The candidate went back to an earlier page: jobfill waits for them instead of acting."""
    plist = state["pages"]
    pos = pages.position(plist, spot["index"])
    title = f" ({pos['title']})" if pos.get("title") else ""
    return {"app_id": app_id, "paused": "candidate_went_back", "page": pos, "furthest_page": len(plist),
            "next": f"The candidate went back to page {pos['n']} of {len(plist)}{title}, probably to check or change "
                    "something. Stop and wait: do not fill, click or submit. Run "
                    f"`python -m jobfill apply watch --app {app_id}` (in the background if you can) to learn when "
                    f"they move; when they are back on page {len(plist)} or further, run `apply fill` again. Fill "
                    "the earlier page only if they ask you to (`apply fill --here`)."}


# --------------------------------------------------------------------------- commands on the open page

def fill_page(store, data_dir: Path, app_id: int, state: dict, page, adapter, *, fresh=False, overwrite=False,
              files=True, dry_run=False, here=False) -> dict:
    """`apply fill` on the page in the window. Returns the summary; updates ``state`` (the caller saves it)."""
    blockers = adapter.prepare(page, store, dry_run=dry_run)
    if GATES & set(blockers):
        report = {"fields": {}, "blockers": blockers}
    else:
        spot = place(page, adapter, state)
        if spot["where"] == "earlier" and not here:
            state["page_url"] = page.url
            return paused(state, spot, app_id)
        # A page jobfill has just opened has no answers of the candidate's yet: replace prefilled values.
        full = fresh and not state.get("filled_once")
        report = fill_frame(adapter.frame(page), store, app_id, adapter,
                            overwrite=True if full else ("own" if overwrite else False),
                            typed=state.setdefault("typed", {}), asked=state.setdefault("asked", {}),
                            files=files, dry_run=dry_run)
        report["blockers"] = blockers
        if spot["where"] == "new" and spot["index"] > 0:
            report["moved_by_candidate"] = True
        if not dry_run and spot["where"] != "unknown":
            pages.remember(state["pages"], spot["where"], spot["index"], spot["info"])
        report["page"] = pages.position(state["pages"], spot["index"])
        state["filled_once"] = state.get("filled_once") or not dry_run
    state["page_url"] = page.url
    (state_dir(data_dir, app_id) / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                                             encoding="utf-8")
    out = summarize(report, app_id)
    if not dry_run:
        out["record"] = results.write(store, data_dir, app_id)["markdown"]
    return out


def set_field(store, app_id: int, state: dict, page, adapter, field_id: str, value, *, force=False,
              here=False) -> dict:
    spot = place(page, adapter, state)
    if spot["where"] == "earlier" and not here:
        return paused(state, spot, app_id)
    frame = adapter.frame(page)
    fields = {f["id"]: f for f in extract(frame, adapter.root_selector, read_options=False)}
    f = fields.get(field_id) or fields.get("g-" + field_id)
    if f is None:
        raise ApplyError(f"field {field_id} is not on the page (the candidate may have moved to another page, or "
                         "it reloaded; run `apply fill` again)")
    typed = state.setdefault("typed", {})
    if not force and _owner(store, f, {"status": "set"}, typed, {}) == "candidate":
        return {"field": field_id, "label": f["label"], "ok": False, "kept": f["value"],
                "note": "The candidate already answered this in the window; their answer is kept. Change it only "
                        "if they ask you to (--force)."}
    key = _key(store, f)
    try:
        ok, note = fill(frame, f, value, root=ROOT, after=adapter.after_fill, custom=adapter.custom_fill)
    except NoExactChoice as e:   # the agent's value is not one of the choices either: show them
        return {"field": field_id, "label": f["label"], "ok": False,
                "note": f"{value!r} is not one of the choices. Closest: {e.offered}"}
    after = {g["id"]: g for g in extract(frame, adapter.root_selector, read_options=False)}.get(f["id"])
    if after is not None:
        typed[key] = after["value"]
    if ok:
        prior = next((a for a in store.answers(app_id) if a["field_key"] == key), None)
        asked = state.setdefault("asked", {}).pop(key, None)
        question_id = (asked or {}).get("question_id") or (prior or {}).get("question_id")
        store.record_answer(app_id, key, f["label"], value, question_id=question_id, source="user", status="filled",
                            fill="ok", section=f.get("section"), idx=f.get("index"))
        store.note_fill(app_id, key, "ok", value)   # clears any earlier failures
        if asked:   # the candidate told the agent which choice: remembered for this answer on other forms
            store.remember_choice(asked["question_id"], asked["value"], after["value"] if after else value,
                                  store.application(app_id)["company"])
    return {"field": field_id, "label": f["label"], "ok": ok, "note": note or None}


def next_page(state: dict, page, adapter, app_id: int) -> dict:
    """Click Next / Save and Continue, unless the candidate already moved on or went back."""
    spot = place(page, adapter, state)
    plist = state["pages"]
    if spot["where"] == "earlier":
        return paused(state, spot, app_id)
    if spot["where"] == "new" and plist:
        # Left for `apply fill` to record, so its summary says the candidate moved on too.
        return {"clicked": None, "moved_by_candidate": True, "page": pages.position(plist, spot["index"]),
                "next": "The candidate already moved on to this page themselves; do not click Next for them. Run "
                        f"`python -m jobfill apply fill --app {app_id}` for it (their answers are kept)."}
    if spot["where"] != "unknown":
        pages.remember(plist, spot["where"], spot["index"], spot["info"])
    frame = adapter.frame(page)
    btn = adapter.next_button(frame)
    if btn is None:
        raise ApplyError("No Next/Continue button on this page (the next button may be the final submit).")
    label = btn.inner_text(timeout=2000) if btn.count() else ""
    btn.click()
    page.wait_for_load_state("domcontentloaded")
    adapter.wait_ready(page)
    after = place(page, adapter, state)
    errors = adapter.errors(adapter.frame(page))
    moved = after["where"] != "current"
    if after["where"] == "new":
        pages.remember(plist, "new", after["index"], after["info"])
    state["page_url"] = page.url
    pos = pages.position(plist, after["index"])
    return {"clicked": label.strip(), "moved": moved, "page": pos, "page_url": page.url, "errors": errors,
            "next": f"Run `python -m jobfill apply fill --app {app_id}` for this step." if moved else
                    f"Still on page {pos['n']}: the site did not accept it. Fix what `errors` lists (or ask the "
                    "candidate), then run `apply next` again."}


WATCH_EVENTS = {
    "moved_on": "The candidate moved on to a new page themselves. Run `python -m jobfill apply fill --app {id}`: "
                "it fills what is left on that page and keeps their answers.",
    "returned": "The candidate is back on the furthest page. Run `python -m jobfill apply fill --app {id}` to go on.",
    "submitted": "The site confirmed the application: the candidate submitted it. It is marked submitted.",
    "closed": "The jobfill tab was closed. Ask the candidate whether to stop; `apply fill` would reopen the "
              "application from its start link.",
    "timeout": "Nothing changed. Run `apply watch` again to keep waiting, or check in with the candidate.",
}


def watch_page(store, data_dir: Path, app_id: int, state: dict, page, adapter, *, timeout: float = 600,
               interval: float = 2.0) -> dict:
    """Wait until the candidate changes page (Next, Back, submit, closing the tab) and say what to do."""
    plist = state.get("pages", [])

    def observe():
        if page.is_closed():
            return ("closed", None)
        try:
            info = pages.read(page, adapter)
            if pages.blank(info):   # loading, or a page with no questions: maybe the confirmation
                return ("submitted", None) if adapter.confirmed(page) else None
        except Exception:
            return None             # navigating
        return pages.locate(plist, info)

    def event(obs) -> dict:
        kind = {"new": "moved_on", "earlier": "went_back", "current": "returned"}.get(obs[0], obs[0])
        out = {"app_id": app_id, "event": kind}
        if obs[1] is not None:
            out["page"] = pages.position(plist, obs[1])
        if kind == "went_back":
            return {**out, **paused(state, {"index": obs[1]}, app_id)}
        if kind == "submitted":
            store.update_application(app_id, status="submitted")
            out["record"] = results.write(store, data_dir, app_id)["markdown"]
        return {**out, "next": WATCH_EVENTS[kind].format(id=app_id)}

    deadline = time.monotonic() + timeout
    base, seen = None, None
    while time.monotonic() < deadline:
        obs = observe()
        if obs is not None and obs[0] in ("closed", "submitted"):
            return event(obs)
        if base is None:
            base = obs
            if base is not None and base[0] == "new":
                return event(base)   # the candidate had already moved on
        elif obs is not None and obs != base:
            if obs != seen:
                seen = obs           # seen once; a page mid-change must look the same twice
            elif base[0] == "earlier" and obs[0] == "earlier":
                base, seen = obs, None   # still looking through earlier pages: keep waiting
            else:
                return event(obs)
        try:
            page.wait_for_timeout(interval * 1000)
        except Exception:
            return event(("closed", None))
    out = {"app_id": app_id, "event": "timeout", "next": WATCH_EVENTS["timeout"]}
    if base is not None and base[1] is not None:
        out["page"] = pages.position(plist, base[1])
    return out


# --------------------------------------------------------------------------- commands

def fill_command(store, data_dir: Path, app_id: int, *, overwrite=False, headless=False, files=True,
                 dry_run=False, here=False) -> dict:
    state = load_state(data_dir, app_id)
    if state["handler"] != "python":
        raise ApplyError(f"application {app_id} is handled by the agent ({state['handler']}), not the Python filler")
    adapter = adapters.get(state["ats"])
    with browser.session(data_dir, headless) as ctx:
        page, fresh = open_page(ctx, state, adapter)
        out = fill_page(store, data_dir, app_id, state, page, adapter, fresh=fresh, overwrite=overwrite, files=files,
                        dry_run=dry_run, here=here)
    save_state(data_dir, app_id, state)
    return out


def set_command(store, data_dir: Path, app_id: int, field_id: str, value, *, headless=False, force=False,
                here=False) -> dict:
    state = load_state(data_dir, app_id)
    adapter = adapters.get(state["ats"])
    with browser.session(data_dir, headless) as ctx:
        page, _ = open_page(ctx, state, adapter)
        out = set_field(store, app_id, state, page, adapter, field_id, value, force=force, here=here)
    save_state(data_dir, app_id, state)
    if out.get("ok"):
        results.write(store, data_dir, app_id)
    return out


def next_command(data_dir: Path, app_id: int, *, headless=False) -> dict:
    state = load_state(data_dir, app_id)
    adapter = adapters.get(state["ats"])
    with browser.session(data_dir, headless) as ctx:
        page, _ = open_page(ctx, state, adapter)
        out = next_page(state, page, adapter, app_id)
    save_state(data_dir, app_id, state)
    return out


def watch_command(store, data_dir: Path, app_id: int, *, timeout: float = 600, headless=False) -> dict:
    state = load_state(data_dir, app_id)
    adapter = adapters.get(state["ats"])
    with browser.session(data_dir, headless) as ctx:
        page = browser.tagged_page(ctx, state["tag"])
        if page is None:
            return {"app_id": app_id, "event": "closed", "next": WATCH_EVENTS["closed"]}
        return watch_page(store, data_dir, app_id, state, page, adapter, timeout=timeout)


def submit_command(store, data_dir: Path, app_id: int, *, confirm: bool, headless=False) -> dict:
    if not confirm:
        raise ApplyError("Refusing to submit without --confirm. Only pass it after the candidate explicitly "
                         "approved submitting this application.")
    state = load_state(data_dir, app_id)
    adapter = adapters.get(state["ats"])
    with browser.session(data_dir, headless) as ctx:
        page, _ = open_page(ctx, state, adapter)
        spot = place(page, adapter, state)
        if spot["where"] == "earlier":
            return {"submitted": False, **paused(state, spot, app_id)}
        frame = adapter.frame(page)
        btn = adapter.submit_button(frame)
        if btn is None:
            raise ApplyError("No submit button found on this page.")
        btn.click()
        page.wait_for_timeout(5000)
        blockers = adapter.blockers(page)
        done = adapter.confirmed(page)
        errors = [] if done else adapter.errors(adapter.frame(page))
        state["page_url"] = page.url
    save_state(data_dir, app_id, state)
    if done:
        store.update_application(app_id, status="submitted")
    record = results.write(store, data_dir, app_id)["markdown"]
    return {"submitted": done, "record": record, "blockers": blockers, "errors": errors, "page_url": state["page_url"],
            "next": None if done else ("Solve the CAPTCHA in the browser window, then run submit again."
                                       if "captcha" in blockers else
                                       "Not confirmed. Check the window for errors, fix them, and try again.")}
