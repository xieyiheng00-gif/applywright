"""Answer form fields from the database; hand only what it cannot answer to the agent.

Input field (one per form control, produced by whatever drives the browser):

    {"id": "q7", "label": "Will you require sponsorship?", "type": "select",
     "options": ["Yes", "No"], "required": true,
     "section": "education", "index": 0,      # optional: repeating blocks
     "format": "MM/YYYY", "max_chars": 1000}  # optional: date format / text limits

Output status per field:

filled      answered by plain Python (value is ready to type/select)
needs_ai    tier-3 question: the agent writes it from ``ai_task`` + ``ai_context``
needs_user  the profile lacks a fact; ask the candidate once, store it, re-run
uncertain   label resembles several questions; the agent picks one (``jobfill learn``)
unmatched   unknown short question; queued in pending_questions for ``jobfill learn``
skipped     optional field with nothing to say
"""

from __future__ import annotations

import json
import re
from datetime import date

from .catalog import CHECKLIST, PLAYBOOKS, RECORD_KINDS, STYLE, choice_set, playbook_for
from . import relevance
from .derive import RULES, Answer, Ctx, NeedFact, NeedUser, _degree_level, is_current, lacks_skill
from .matcher import Matcher
from .store import DATE_FIELDS
from .text import (choose_date_option, choose_enum_option, choose_range_option, choose_text_option,
                   classify_option, closest_options, date_part, detect_date_format, format_date, names_same, normalize,
                   not_applicable_answer, option_label, parse_date)

LONG_TEXT_CHARS = 200
CATCH_ALL = {"open_ended_custom", "bh_generic"}

COUNTRY_FACTS = {"address.country", "work_auth.primary_country", "work_auth.citizenship"}
STATE_FACTS = {"address.state"}

AI_RULES = [
    "Use only facts in `candidate`. Never invent employers, titles, dates, numbers, skills or outcomes.",
    "Write this answer for this application only; do not reuse wording written for other companies.",
    "Company facts must come from the posting or from sources you verified yourself.",
    "Respect limits: count characters/words programmatically, not by eye.",
    "Plain text, no placeholders like [Company]. Keep ownership honest (contributor vs. lead).",
    "If the data cannot support a good answer, ask the candidate instead of guessing.",
]

THEME_WORDS = {
    "failure": ("fail", "mistake", "wrong", "setback"), "conflict": ("conflict", "disagree", "difficult person"),
    "leadership": ("lead", "leader", "mentor"), "teamwork": ("team", "collaborat"),
    "pressure": ("pressure", "deadline", "stress", "priorit"), "learning": ("learn", "new skill", "quickly"),
    "initiative": ("initiative", "above and beyond", "improve", "ownership"),
    "ambiguity": ("ambigu", "uncertain", "incomplete"), "feedback": ("feedback", "critic"),
    "accomplishment": ("proud", "accomplish", "achievement"), "challenge": ("challeng", "difficult", "hard"),
    "data": ("data", "metric", "analy"), "customer": ("customer", "client", "user"),
    "influence": ("persuad", "convinc", "influenc", "buy in"), "adaptability": ("adapt", "change", "pivot"),
    "mentoring": ("mentor", "coach", "teach", "taught"), "ethics": ("ethic", "integrity", "dilemma", "right thing"),
    "creativity": ("creativ", "innovat", "unconventional", "outside the box"),
}


def _human(canon: str, choices: str | None = None) -> str:
    """How a stored choice reads in text: the choice set's label ("they/them"), else 'Not veteran'."""
    label = choice_set(choices)["_labels"].get(canon) if choices and choices != "bool" else None
    return label or canon.replace("_", " ").capitalize()


def _canonical(value: str, cs: dict) -> str:
    """A stored value in its choice set's terms; free text from older data ("they/them") is classified."""
    if value in cs["_values"]:
        return value
    scores = classify_option(normalize(value), cs)
    return max(scores, key=scores.get) if scores else value


class Resolver:
    def __init__(self, store, app_id: int | None = None, today: date | None = None, log: bool = True):
        self.store = store
        self.app = store.application(app_id) if app_id else None
        self.today = today or date.today()
        self.log = log and self.app is not None
        self.matcher = Matcher.from_store(store)
        self.questions = self.matcher.questions
        self.defs = store.fact_defs()

    # ------------------------------------------------------------------ public
    def resolve(self, fields: list[dict]) -> dict:
        results = [self.resolve_field(f, i) for i, f in enumerate(fields)]
        summary: dict[str, int] = {}
        for r in results:
            summary[r["status"]] = summary.get(r["status"], 0) + 1
        out = {"application_id": self.app["id"] if self.app else None, "summary": summary, "fields": results}
        ai = [r for r in results if r["status"] == "needs_ai"]
        if ai:
            keys = {k for r in ai for k in r["ai_task"]["context_keys"]}
            themes = {t for r in ai for t in r["ai_task"]["themes"]}
            playbooks = {r["ai_task"]["playbook"] for r in ai}
            out["ai_context"] = build_ai_context(self.store, self.app, keys, themes, playbooks)
        return out

    def resolve_field(self, field: dict, position: int = 0) -> dict:
        field = dict(field)
        field.setdefault("id", f"field_{position}")
        field.setdefault("type", "text")
        res = self._resolve_field(field)
        # The form's own instructions under the field go to whoever answers it: the candidate or the agent.
        if field.get("description") and res["status"] in ("needs_user", "needs_ai", "uncertain", "unmatched"):
            res["instructions"] = field["description"]
            if res["status"] == "needs_ai":
                res["ai_task"]["instructions"] = field["description"]
        if self.log:
            self._log(field, res)
        return res

    def _resolve_field(self, field: dict) -> dict:
        base = {"id": field["id"], "label": field.get("label", "")}
        m = self.matcher.match(field.get("label", ""), section=field.get("section"), field_type=field["type"],
                               company=self.app["company"] if self.app else None,
                               role=self.app.get("role") if self.app else None)
        if m.matched:
            q = self.questions[m.question_id]
            res = self._answer(q, m.params, field)
            return {**base, "question_id": q["id"], "tier": q["tier"],
                    "match": {"how": m.status, "score": m.score}, **res}

        long_text = field["type"] == "textarea" or (field["type"] == "text" and
                                                     (field.get("max_chars") or 0) >= LONG_TEXT_CHARS)
        self.store.add_pending(field.get("label", ""), m.normalized, field_type=field["type"],
                               options=[option_label(o) for o in field.get("options") or []] or None,
                               candidates=m.candidates or None,
                               company=self.app["company"] if self.app else None,
                               url=self.app.get("url") if self.app else None)
        if m.status == "uncertain":
            return {**base, "status": "uncertain", "candidates": [{"question_id": q, "score": s,
                                                                   "text": self.questions[q]["text"]}
                                                                  for q, s in m.candidates]}
        if long_text:
            q = self.questions["open_ended_custom"]
            res = self._answer(q, {}, field)
            return {**base, "question_id": q["id"], "tier": 3, "match": {"how": "fallback", "score": 0.0}, **res}
        return {**base, "status": "unmatched",
                "note": "New question, queued for `jobfill learn`. Classify it so plain Python answers it next time."}

    # ------------------------------------------------------------------ strategies
    def _answer(self, q: dict, params: dict, field: dict) -> dict:
        spec = q["answer"]
        if "ai" in spec:
            return self._ai(q, spec["ai"], field)
        try:
            if "fact" in spec:
                key = spec["fact"]
                value = self.store.get_fact(key)
                if value is None:
                    raise NeedFact(key)
                d = self.defs[key]
                ans = Answer(value, choices=d["choices"], source=f"fact:{key}",
                             kind="country" if key in COUNTRY_FACTS else "state" if key in STATE_FACTS
                             else "date" if d["type"] == "date" else None)
            elif "template" in spec:
                facts = {}
                for key in re.findall(r"\{([a-z_]+\.[a-z0-9_]+)\}", spec["template"]):
                    v = self.store.get_fact(key)
                    if v is None:
                        raise NeedFact(key)
                    facts[key] = v
                text = re.sub(r"\{([a-z_]+\.[a-z0-9_]+)\}", lambda mm: str(facts[mm.group(1)]), spec["template"])
                ans = Answer(text, source="template")
            elif "derive" in spec:
                fn = RULES[spec["derive"]]
                args = {**spec.get("args", {}), **params}
                ctx = Ctx(self.store, self.app, self.today, field)
                ans = fn(ctx, **args)
                ans.source = ans.source or f"derive:{spec['derive']}"
            elif "record" in spec:
                ans = self._record(spec["record"], spec["field"], q, field)
            else:
                raise ValueError(f"question {q['id']} has no answer strategy")
        except NeedFact as nf:
            return self._missing_fact(nf, field)
        except NeedUser as nu:
            return {"status": "needs_user", "ask": nu.prompt, "store": nu.store}
        return self._render(ans, q, field)

    def _record(self, kind: str, fname: str, q: dict, field: dict) -> Answer:
        items = self.store.records(kind)
        idx = int(field.get("index") or 0)
        src = f"record:{kind}[{idx}].{fname}"
        if idx >= len(items):
            return Answer(None, source=src)
        r = items[idx]
        if fname == "description" and kind == "experience":
            parts = [r.get("summary", "")] + [f"- {h}" for h in r.get("highlights", [])]
            return Answer("\n".join(p for p in parts if p) or None, source=src)
        ongoing = is_current(r, self.today) if kind == "experience" else False
        if fname == "end_date" and (ongoing or (kind == "education" and r.get("expected") and not r.get("end_date"))):
            return Answer(None, source=src)
        if fname == "current" and kind == "experience":
            return Answer(ongoing, choices="bool", source=src)
        if fname in ("current", "expected", "may_contact"):
            return Answer(bool(r.get(fname)), choices="bool", source=src)
        if fname == "degree_level" or (fname == "degree" and field.get("options")):
            # Dropdowns list levels ("Master's Degree"), not diploma titles ("Master of Science").
            return Answer(r.get("degree_level") or _degree_level(r), choices="degree_level", source=src)
        return Answer(r.get(fname), choices=q.get("choices"), source=src,
                      kind="date" if fname in DATE_FIELDS else None)

    def _ai(self, q: dict, ai: dict, field: dict) -> dict:
        # Catch-all questions cover many different prompts: reuse only an answer to the same wording.
        same_label = field.get("label") if q["id"] in CATCH_ALL else None
        prior = self.store.prior_answer(q["id"], ai["scope"], self.app, label=same_label)
        if prior is not None:
            same_app = prior["application_id"] == self.app["id"]
            return {"status": "filled", "value": prior["value"],
                    "source": prior["source"] if same_app else f"answer_log:app{prior['application_id']}",
                    "note": "Recorded answer." if same_app else f"Reused: same {ai['scope']} answered this before."}
        if ai["optional"] == "skip" and not field.get("required"):
            return {"status": "skipped", "note": "Optional open question; left blank unless the candidate wants it."}
        limits = {k: field[k] for k in ("max_chars", "max_words", "min_words") if field.get(k)}
        themes = set(ai.get("themes") or [])
        if q["category"] == "behavioral" and not themes:
            lab = normalize(field.get("label", ""))
            themes = {t for t, words in THEME_WORDS.items() if any(w in lab for w in words)}
        task = {
            "question_id": q["id"], "question": field.get("label") or q["text"], "mode": ai["mode"],
            "scope": ai["scope"], "guidance": ai["guidance"], "playbook": playbook_for(ai, q["category"]),
            "deliverable": ai["deliverable"],
            "limits": limits, "context_keys": ai["context"], "themes": sorted(themes),
            "record_with": f"python -m jobfill answer record --app {self.app['id'] if self.app else '<id>'} "
                           f"--question {q['id']} --label \"<the question's label>\" --file <answer.txt>",
        }
        if ai.get("examples"):
            task["examples"] = ai["examples"]
            task["examples_how"] = (f"Write {ai['examples']} different drafts, each built on a different entry of "
                                    "`ai_context.relevant.angles` (its posting line and its evidence). Run `answer "
                                    "check` on each, show them to the candidate to pick, edit or replace, and record "
                                    "only the one they choose.")
        return {"status": "needs_ai", "ai_task": task}

    def _missing_fact(self, nf: NeedFact, field: dict) -> dict:
        d = self.defs.get(nf.key, {})
        if not field.get("required") and d.get("setup") == "optional":
            return {"status": "skipped", "note": f"{nf.key} not set and field is optional"}
        out = {"status": "needs_user", "ask": d.get("prompt", nf.key), "fact": nf.key,
               "store": {"cmd": f"python -m jobfill fact set {nf.key} <value>"}}
        if nf.why:
            out["why"] = nf.why
        if d.get("type") == "enum":
            out["choices"] = sorted(choice_set(d["choices"])["_values"])
        if d.get("sensitive"):
            out["sensitive"] = True
        return out

    # ------------------------------------------------------------------ rendering
    def _render(self, ans: Answer, q: dict, field: dict) -> dict:
        value = ans.value
        ftype = (field.get("type") or "text").lower()
        options = field.get("options") or []
        if value is None or value == "" or value == []:
            # A rule found nothing to say (no current employer): that is the case the form's
            # "If it doesn't apply to you, please enter 'None'" is written for.
            na = None if options else not_applicable_answer(field.get("description"))
            if na:
                return {"status": "filled", "raw": na, "value": na, "source": "form:instructions",
                        "note": f"Not applicable; the form says to enter '{na}'."}
            if field.get("required"):
                return {"status": "needs_user", "ask": f"No stored answer for '{field.get('label')}'. What should it be?",
                        "source": ans.source}
            return {"status": "skipped", "note": "no stored value", "source": ans.source}
        choices = ans.choices or q.get("choices")
        out = {"status": "filled", "raw": value, "source": ans.source}
        if ans.kind == "date" and parse_date(value) is not None:
            return self._render_date(ans, field, out)

        if options:
            picked = self._pick(value, options, choices, ans.kind, ftype, q["id"])
            if picked is None or picked == []:
                vals = value if isinstance(value, list) else [value]
                shown = ", ".join(_human(v, choices) if choices and choices != "bool" and isinstance(v, str) else str(v)
                                  for v in vals)
                if not choices and isinstance(value, str):
                    # A name (school, field of study) with no exact option: never a look-alike. The candidate
                    # picks, in the window or by telling the agent, and that pick is remembered for other forms.
                    near = closest_options(value, options)
                    return {"status": "needs_user", "raw": value, "source": ans.source, "no_exact": True,
                            "choices": near,
                            "ask": f"No option for '{field.get('label')}' is exactly your stored answer ({shown}). "
                                   f"Closest: {near}. Pick one in the window, or say which; it is remembered for "
                                   "other forms."}
                return {"status": "needs_user", "raw": value, "source": ans.source,
                        "ask": f"Your stored answer ({shown}) is not one of the options for '{field.get('label')}'. "
                               f"Which option should be selected? {[option_label(o) for o in options]}"}
            out["value"] = [option_label(p) for p in picked] if isinstance(picked, list) else option_label(picked)
            return out

        if isinstance(value, bool):
            out["value"] = value if ftype == "checkbox" else ("Yes" if value else "No")
        elif choices and choices != "bool" and isinstance(value, (str, list)):
            cs = choice_set(choices)
            vals = value if isinstance(value, list) else [value]
            out["value"] = ", ".join(_human(_canonical(str(v), cs), choices) for v in vals)
        elif isinstance(value, list):
            out["value"] = ", ".join(str(v) for v in value)
        elif isinstance(value, float) and value.is_integer():
            out["value"] = str(int(value))
        else:
            out["value"] = str(value)
        if field.get("picks") and isinstance(value, str) and (alts := self.store.learned_choices(q["id"], value)):
            out["alternatives"] = alts   # a search-and-pick menu: what the candidate picked for it before
        if field.get("max_chars") and isinstance(out["value"], str) and len(out["value"]) > field["max_chars"]:
            out["warning"] = f"{len(out['value'])} chars exceeds limit {field['max_chars']}; trim before submitting"
        return out

    def _render_date(self, ans: Answer, field: dict, out: dict) -> dict:
        """Write a stored date the way this field wants it: one piece, a dropdown option, or a format."""
        parts = parse_date(ans.value)
        label = field.get("label", "")
        options = field.get("options") or []
        part = date_part(label, options)
        if options:
            picked = choose_date_option(parts, part, options)
            if picked is None:
                return {"status": "needs_user", "raw": ans.value, "source": ans.source,
                        "ask": f"Your stored date ({ans.value}) matches none of the options for '{label}'. "
                               f"Which option should be selected? {[option_label(o) for o in options]}"}
            out["value"] = option_label(picked)
            return out
        ftype = (field.get("type") or "text").lower()
        placeholder = field.get("placeholder") or ""
        if part == "month":
            fmt = "M" if ftype == "number" else ("MMMM" if re.search(r"[a-z]{3}", placeholder, re.I) else "MM")
        elif part == "year":
            fmt = "YY" if (field.get("max_chars") or 4) <= 2 else "YYYY"
        elif part == "day":
            fmt = "D" if ftype == "number" else "DD"
        else:
            precision = "day" if parts[2] else "month" if parts[1] else "year"
            # Plain-English questions ("When can you start?") get a readable date; date fields get MM/YYYY style.
            spoken = re.match(r"^(when|what|how)\b", normalize(label)) and ftype in ("text", "textarea")
            fmt = (field.get("format") or {"date": "YYYY-MM-DD", "month": "YYYY-MM"}.get(ftype)
                   or detect_date_format(label, placeholder) or ans.fmt
                   or {"year": "YYYY", "month": "MMMM YYYY" if spoken else "MM/YYYY",
                       "day": "MMMM D, YYYY" if spoken else "MM/DD/YYYY"}[precision])
        text = format_date(ans.value, fmt)
        if text is None:
            return {"status": "needs_user", "raw": ans.value, "source": ans.source,
                    "ask": f"'{label}' needs a month, but only the year ({ans.value}) is stored. What month?"}
        out["value"] = text
        return out

    def _pick(self, value, options, choices, kind, ftype, question_id=None):
        multi = ftype in ("multiselect", "checkbox") and len(options) > 1
        if choices:
            cs = choice_set(choices)
            vals = value if isinstance(value, list) else [value]
            vals = [("true" if v else "false") if isinstance(v, bool) else _canonical(str(v), cs) for v in vals]
            picked = [p for v in vals if (p := choose_enum_option(v, options, cs)) is not None]
            return picked if multi else (picked[0] if picked else None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return choose_range_option(float(value), options) or choose_text_option(str(value), options)
        if isinstance(value, list):
            picked = [p for v in value if (p := self._exact(str(v), options, kind, question_id)) is not None]
            return picked if multi else (picked[0] if picked else None)
        return self._exact(str(value), options, kind, question_id)

    def _exact(self, value: str, options, kind, question_id):
        """The option that is ``value`` (or it with a qualifier the form adds), else the one the candidate picked
        for it on an earlier form. Never a look-alike: "University of Wisconsin" is not "...-Madison"."""
        if kind in ("country", "state"):   # "USA" is "United States of America": known synonyms, not look-alikes
            return choose_text_option(value, options, kind)
        hit = next((o for o in options if names_same(option_label(o), value)), None)
        for choice in ([] if hit is not None else self.store.learned_choices(question_id, value)):
            hit = next((o for o in options if normalize(option_label(o)) == normalize(choice)), None)
            if hit is not None:
                break
        return hit

    def _log(self, field: dict, res: dict) -> None:
        """Every question on the form goes into the application's record, answered or not."""
        filled = res["status"] == "filled"
        self.store.record_answer(
            self.app["id"], self.store.answer_key(field.get("label", ""), field.get("section"), field.get("index")),
            field.get("label", ""), res.get("value") if filled else None, question_id=res.get("question_id"),
            source=res.get("source") or "", tier=res.get("tier"), status=res["status"],
            section=field.get("section"), idx=field.get("index"))


# ---------------------------------------------------------------------- AI context

def _span(r: dict) -> str:
    start = r.get("start_date", "?")
    if r.get("current"):
        end = "present"
    elif r.get("expected"):
        end = f"{r.get('end_date', '?')} (expected)"
    else:
        end = r.get("end_date", "?")
    return f"{start} to {end}"


def build_ai_context(store, app: dict | None, keys: set[str], themes: set[str],
                     playbooks: set[str] = frozenset()) -> dict:
    """Only the profile slices and writing playbooks the pending questions need, so the prompt stays small."""
    f = store.get_fact
    name = f("identity.preferred_name") or f("identity.first_name")
    cand: dict = {"name": " ".join(x for x in (name, f("identity.last_name")) if x)}
    edu = store.records("education")
    exp = store.records("experience")
    today = date.today()
    job = next((r for r in exp if is_current(r, today) and r.get("employment_type") != "internship"), None)
    intern = next((r for r in exp if is_current(r, today) and r.get("employment_type") == "internship"), None)
    if job:
        cand["now"] = f"{job.get('title')} at {job.get('company')}"
    elif edu and edu[0].get("expected"):
        cand["now"] = f"{edu[0].get('degree')} student at {edu[0].get('school')}, graduating {edu[0].get('end_date')}"
        if intern:
            cand["now"] += f"; currently interning as {intern.get('title')} at {intern.get('company')}"
    elif intern:
        cand["now"] = f"Intern: {intern.get('title')} at {intern.get('company')}"
    simple = {"motivations": "profile.motivations", "career_goals": "profile.career_goals",
              "strengths": "profile.strengths", "weaknesses": "profile.weaknesses",
              "work_style": "profile.work_style", "interests": "profile.interests"}
    for key in sorted(keys):
        if key in simple:
            if f(simple[key]):
                cand[key] = f(simple[key])
        elif key == "summary":
            cand["summary"] = {k: v for k, v in (("headline", f("profile.headline")), ("about", f("profile.summary"))) if v}
        elif key == "education":
            cand["education"] = [f"{r.get('degree', '')} in {r.get('field_of_study', '?')}, {r.get('school')} ({_span(r)})"
                                 for r in edu]
        elif key == "coursework":
            cand["coursework"] = [f"{r.get('school')}: {', '.join(c) if isinstance(c, list) else c}"
                                  for r in edu if (c := r.get("coursework"))]
        elif key == "experience":
            cand["experience"] = [f"{r.get('title')}, {r.get('company')} ({_span(r)}): {r.get('summary', '')}".rstrip(": ")
                                  for r in exp]
        elif key == "experience_full":
            cand["experience_full"] = [{k: r[k] for k in ("title", "company", "employment_type", "summary", "highlights",
                                                          "skills") if r.get(k)} | {"dates": _span(r)} for r in exp]
        elif key == "projects":
            cand["projects"] = [f"{r['name']}: {r.get('summary', '')}" + (f" [{', '.join(r['tech'])}]" if r.get("tech") else "")
                                for r in store.records("project")]
        elif key == "projects_full":
            cand["projects_full"] = [{k: r[k] for k in ("name", "role", "ownership", "is_coursework", "summary",
                                                        "description", "highlights", "metrics", "tech", "url") if r.get(k)}
                                     for r in store.records("project")]
        elif key == "skills":
            cand["skills"] = [r["name"] + (f" ({r['years']}y)" if r.get("years") else "")
                              for r in sorted(store.records("skill"), key=lambda r: -(r.get("years") or 0))
                              if not lacks_skill(r)][:20]
        elif key == "stories":
            stories = store.records("story")
            chosen = [s for s in stories if themes & set(s.get("themes", []))] or stories
            cand["stories"] = [{k: s[k] for k in ("title", "themes", "situation", "task", "action", "result", "source")
                                if s.get(k)} for s in chosen]
    if f("profile.writing_style"):
        cand["writing_style"] = f("profile.writing_style")
    ctx = {"candidate": cand, "rules": AI_RULES}
    if "relevant" in keys:
        # Python's pick of what this job is about in the candidate's background (see relevance.py).
        ctx["relevant"] = relevance.match(store, app, today)
    if playbooks:
        # How to write: shared style, the technique per question type on this form, a check before recording.
        ctx["writing"] = {"style": STYLE, "playbooks": {p: PLAYBOOKS[p] for p in sorted(playbooks)},
                          "checklist": CHECKLIST}
    if app:
        ctx["application"] = {k: app[k] for k in ("id", "company", "role", "url") if app.get(k)}
        if app.get("posting"):
            ctx["application"]["posting"] = app["posting"][:6000]
        if app.get("notes"):
            ctx["application"]["notes"] = app["notes"][:2000]
    return ctx


def ai_task_for(store, question_id: str, app_id: int | None, label: str | None = None, **limits) -> dict:
    """Standalone version of a needs_ai result, for `jobfill context`."""
    r = Resolver(store, app_id, log=False)
    q = r.questions.get(question_id)
    if q is None or "ai" not in q["answer"]:
        raise ValueError(f"{question_id!r} is not a tier-3 (ai) question")
    field = {"id": question_id, "label": label or q["text"], "type": "textarea", "required": True,
             **{k: v for k, v in limits.items() if v}}
    res = r._ai(q, q["answer"]["ai"], field)
    if res["status"] == "needs_ai":
        res["ai_context"] = build_ai_context(store, r.app, set(res["ai_task"]["context_keys"]),
                                             set(res["ai_task"]["themes"]), {res["ai_task"]["playbook"]})
    return res


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
