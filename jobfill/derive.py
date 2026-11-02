"""Plain-Python answer rules for questions whose answer is computed, not stored.

Each rule receives a ``Ctx`` and keyword arguments (from the question spec and
from named regex groups) and returns an ``Answer``. When the profile cannot
support an honest answer the rule raises ``NeedFact`` / ``NeedUser`` so the
agent asks the candidate once and stores the result for next time. Rules never
guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .catalog import DEGREE_RANK, choice_set
from .text import (canonical_country, choose_range_option, choose_text_option, classify_option,
                   company_key as _company_key, normalize, option_label, parse_date, phrase_in)


@dataclass
class Answer:
    value: object
    choices: str | None = None   # choice set used to map the value onto form options
    kind: str | None = None      # "country" / "state" for text-option matching, "date" for dates
    source: str = ""
    fmt: str | None = None       # preferred date format when the field itself does not say


class NeedFact(Exception):
    def __init__(self, key: str, why: str | None = None):
        super().__init__(key)
        self.key, self.why = key, why


class NeedUser(Exception):
    def __init__(self, prompt: str, store: dict | None = None):
        super().__init__(prompt)
        self.prompt, self.store = prompt, store


@dataclass
class Ctx:
    store: object
    app: dict | None
    today: date
    field: dict

    @property
    def label(self) -> str:
        return normalize(self.field.get("label", ""))

    def fact(self, key: str):
        v = self.store.get_fact(key)
        if v is None:
            raise NeedFact(key)
        return v

    def opt(self, key: str, default=None):
        v = self.store.get_fact(key)
        return default if v is None else v

    def records(self, kind: str) -> list[dict]:
        return self.store.records(kind)

    def company(self) -> str:
        if not self.app:
            raise NeedUser("Which company is this application for? Start one with `jobfill app start`.")
        return self.app["company"]


RULES = {}


def rule(fn):
    RULES[fn.__name__] = fn
    return fn


_SELF_REFS = {"", "us", "this company", "our company", "the company", "here", "this organization", "our organization",
              "this organisation", "the organization", "this employer", "our firm", "this firm"}


def _target_company(ctx: Ctx, company_ref: str | None) -> str:
    ref = (company_ref or "").strip()
    if "{company}" in ref or ref in _SELF_REFS or ref.startswith(("us ", "any of our", "our ", "this company")):
        return ctx.company()
    if ref and ctx.app and _company_key(ref) == _company_key(ctx.app["company"]):
        return ctx.app["company"]
    if ref:
        raise NeedUser(f"The form asks about '{company_ref}', which is not this application's company. "
                       "Answer it directly.")
    return ctx.company()


def _years_between(start: str, end: str | None, today: date) -> float:
    s = parse_date(start)
    if not s:
        return 0.0
    e = parse_date(end) if end else (today.year, today.month, today.day)
    return max(0.0, (e[0] - s[0]) + ((e[1] or 1) - (s[1] or 1)) / 12)


def _month_index(d: str | None, today: date) -> int | None:
    p = parse_date(d) if d else (today.year, today.month, None)
    return p[0] * 12 + (p[1] or 1) - 1 if p else None


def _find_named(records: list[dict], name: str, alias_field: str | None = None) -> dict | None:
    target = normalize(name)
    target = re.sub(r"^(the|a|an) ", "", target)
    exact, partial = None, None
    for r in records:
        names = [r.get("name", "")] + list(r.get(alias_field, []) if alias_field else [])
        for n in (normalize(x) for x in names if x):
            if n == target:
                exact = exact or r
            elif n and (phrase_in(n, target) or phrase_in(target, n)):
                partial = partial or r
    return exact or partial


# ---------------------------------------------------------------- identity / contact

@rule
def initial_of(ctx, key):
    v = ctx.opt(key)
    return Answer(v.strip()[0].upper() if v else None)


@rule
def preferred_name(ctx):
    return Answer(ctx.opt("identity.preferred_name") or ctx.fact("identity.first_name"))


@rule
def signature_name(ctx):
    return Answer(f"{ctx.fact('identity.first_name')} {ctx.fact('identity.last_name')}")


@rule
def today(ctx, format=None):
    return Answer(ctx.today.isoformat(), kind="date", fmt=format)


@rule
def current_location(ctx):
    city = ctx.fact("address.city")
    state = ctx.opt("address.state")
    country = ctx.opt("address.country")
    primary = ctx.opt("work_auth.primary_country")
    parts = [city, state] if state else [city]
    if country and canonical_country(country) != canonical_country(primary or ""):
        parts.append(country)
    return Answer(", ".join(parts))


@rule
def url_username(ctx, key):
    url = ctx.fact(key)
    return Answer(url.rstrip("/").split("/")[-1])


# ---------------------------------------------------------------- work authorization

def _job_country(ctx, country: str | None) -> str:
    c = canonical_country(country) if country else None
    if c:
        return c
    if ctx.app and ctx.app.get("country"):
        return canonical_country(ctx.app["country"]) or ctx.app["country"]
    return canonical_country(ctx.fact("work_auth.primary_country")) or ctx.fact("work_auth.primary_country")


@rule
def authorized_in_country(ctx, country=None):
    target = _job_country(ctx, country)
    allowed = {canonical_country(c) or c for c in ctx.fact("work_auth.authorized_countries")}
    return Answer(target in allowed, choices="bool")


@rule
def sponsorship_now_or_future(ctx):
    return Answer(bool(ctx.fact("work_auth.requires_sponsorship_now") or
                       ctx.fact("work_auth.requires_sponsorship_future")), choices="bool")


@rule
def authorized_without_sponsorship(ctx, country=None):
    authorized = authorized_in_country(ctx, country).value
    needs = sponsorship_now_or_future(ctx).value
    return Answer(bool(authorized and not needs), choices="bool")


def _country_key(name: str) -> str:
    return (canonical_country(name) or name).strip().lower()


@rule
def citizen_of_country(ctx, country="United States"):
    target = _country_key(country)
    return Answer(target in {_country_key(c) for c in ctx.fact("work_auth.citizenship")}, choices="bool")


@rule
def permanent_resident_of(ctx, country="United States"):
    target = _country_key(country)
    # A permanent resident may work there, and a citizen is not a permanent resident.
    if target not in {_country_key(c) for c in ctx.fact("work_auth.authorized_countries")}:
        return Answer(False, choices="bool")
    if target in {_country_key(c) for c in ctx.fact("work_auth.citizenship")}:
        return Answer(False, choices="bool")
    if target == _country_key("United States"):
        return Answer(ctx.fact("work_auth.us_status") == "permanent_resident", choices="bool")
    raise NeedUser(f"Are you a permanent resident of {country}?")


@rule
def us_citizen_or_pr(ctx):
    return Answer(ctx.fact("work_auth.us_status") in ("citizen", "permanent_resident"), choices="bool")


@rule
def us_person(ctx):
    # ITAR/EAR "U.S. person": citizen, lawful permanent resident, asylee or refugee.
    return Answer(ctx.fact("work_auth.us_status") in ("citizen", "permanent_resident", "asylee_refugee"),
                  choices="bool")


@rule
def has_clearance(ctx):
    return Answer(ctx.fact("work_auth.security_clearance") not in ("none",), choices="bool")


@rule
def lives_in_country(ctx, country="United States"):
    target = canonical_country(country) or country
    return Answer((canonical_country(ctx.fact("address.country")) or ctx.fact("address.country")) == target,
                  choices="bool")


# ---------------------------------------------------------------- company relationship

@rule
def relatives_at_company(ctx):
    company = _company_key(ctx.company())
    listed = [_company_key(c) for c in ctx.fact("legal.relatives_at_companies")]
    return Answer(company in listed, choices="bool")


@rule
def previously_employed_here(ctx, company_ref=None):
    target = _company_key(_target_company(ctx, company_ref))
    current_only = "current" in ctx.label and "former" not in ctx.label
    for r in ctx.records("experience"):
        if _company_key(r.get("company", "")) == target and (is_current(r, ctx.today) or not current_only):
            return Answer(True, choices="bool")
    return Answer(False, choices="bool")


@rule
def currently_employed_here(ctx):
    target = _company_key(ctx.company())
    return Answer(any(_company_key(r.get("company", "")) == target and is_current(r, ctx.today)
                      for r in ctx.records("experience")), choices="bool")


@rule
def previously_applied_here(ctx, company_ref=None):
    company = _target_company(ctx, company_ref)
    others = ctx.store.same_company_apps(company, exclude_id=ctx.app["id"] if ctx.app else None)
    return Answer(bool(others), choices="bool")


@rule
def previously_interviewed_here(ctx, company_ref=None):
    company = _target_company(ctx, company_ref)
    others = ctx.store.same_company_apps(company, exclude_id=ctx.app["id"] if ctx.app else None)
    return Answer(any(a["status"] in ("interviewing", "offer", "hired") for a in others), choices="bool")


# ---------------------------------------------------------------- education

def _degree_level(r: dict) -> str | None:
    if r.get("degree_level"):
        return r["degree_level"]
    scores = classify_option(normalize(r.get("degree", "")), choice_set("degree_level"))
    return max(scores, key=scores.get) if scores else None


def _in_progress(r: dict, today: date) -> bool:
    if r.get("expected"):
        return True
    end = parse_date(r.get("end_date"))
    return bool(end and (end[0], end[1] or 12) > (today.year, today.month))


@rule
def highest_education_level(ctx):
    edu = ctx.records("education")
    if not edu:
        raise NeedUser("No education records yet. Add them with `jobfill record add education`.",
                       store={"record": "education"})
    completed_only = any(w in ctx.label for w in ("completed", "obtained", "earned", "achieved", "attained"))
    levels = [lvl for r in edu if (lvl := _degree_level(r)) and not (completed_only and _in_progress(r, ctx.today))]
    if not levels:
        raise NeedUser("Could not tell your highest degree level; set degree_level on your education records.")
    return Answer(max(levels, key=lambda l: DEGREE_RANK.get(l, 0)), choices="degree_level")


@rule
def has_degree(ctx, level="bachelor", field=None):
    scores = classify_option(normalize(level), choice_set("degree_level"))
    target = max(scores, key=scores.get) if scores else "associate"
    include_expected = any(w in ctx.label for w in ("will you have", "expected", "pursuing", "by the start"))
    edu = [r for r in ctx.records("education") if include_expected or not _in_progress(r, ctx.today)]
    meeting = [r for r in edu if DEGREE_RANK.get(_degree_level(r) or "", 0) >= DEGREE_RANK[target]]
    if not meeting:
        return Answer(False, choices="bool")
    if field:
        f = normalize(field)
        if any(phrase_in(f, normalize(r.get("field_of_study", ""))) or phrase_in(normalize(r.get("field_of_study", "")), f)
               for r in meeting if r.get("field_of_study")):
            return Answer(True, choices="bool")
        raise NeedUser(f"Does your {target} degree count as '{field}' (or a related field) for this question?")
    return Answer(True, choices="bool")


@rule
def currently_student(ctx):
    return Answer(any(_in_progress(r, ctx.today) for r in ctx.records("education")), choices="bool")


@rule
def graduation_date(ctx, format=None):
    edu = ctx.records("education")
    if not edu or not edu[0].get("end_date"):
        raise NeedUser("What is your (expected) graduation date? Add end_date to your latest education record.",
                       store={"record": "education"})
    return Answer(edu[0]["end_date"], kind="date", fmt=format)


_NUM = r"(\d+(?:\.\d+)?)"


def parse_gpa(raw, scale=None) -> tuple[float, float] | None:
    """'3.8' / '3.80/4.0' / '9.1 out of 10' (+ a separate scale field) -> (value, scale).

    Without a stated scale: 4.0 when the value fits, else the smallest common scale that does.
    """
    m = re.search(rf"{_NUM}\s*(?:/|out of|of)\s*{_NUM}", str(raw or ""))
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(_NUM, str(raw or ""))
    if not m:
        return None
    value = float(m.group(1))
    if scale not in (None, ""):
        s = re.search(_NUM, str(scale))
        if s:
            return value, float(s.group(1))
    return value, next(s for s in (4.0, 4.3, 5.0, 10.0, 20.0, 100.0) if value <= s) if value <= 100 else value


def _target_scale(field: dict) -> float | None:
    """The scale a form wants: 'out of 4.0', '(4.0 scale)', '3.5/4.0', or the numbers in its options."""
    text = f"{field.get('label', '')} {field.get('placeholder') or ''}"
    m = (re.search(rf"(?:out of|on an?|scale of|/)\s*{_NUM}", text, re.I)
         or re.search(rf"{_NUM}[\s-]*(?:point )?scale", text, re.I))
    if m:
        return float(m.group(1))
    nums = [float(n) for o in field.get("options") or [] for n in re.findall(_NUM, option_label(o))]
    if nums:
        return next((s for s in (4.0, 4.3, 5.0, 10.0, 100.0) if max(nums) <= s + 0.01), None)
    return None


def _gpa_record(ctx, level=None) -> dict | None:
    edu = ctx.records("education")
    if level:
        return next((r for r in edu if _degree_level(r) == level or (level == "master" and _degree_level(r) == "mba")),
                    None)
    i = int(ctx.field.get("index") or 0)
    return edu[i] if i < len(edu) else None


def _gpa_on(ctx, r: dict, want: float | None) -> float | None:
    """GPA on the form's scale. Scales are never converted by formula: a 9.1/10 is not a 3.64/4.0 to every
    school, so a different scale is asked once and kept as ``gpa_4`` on the record."""
    value, scale = parse_gpa(r.get("gpa"), r.get("gpa_scale"))
    if want is None or abs(want - scale) < 0.01:
        return value
    if abs(want - 4.0) < 0.01 and r.get("gpa_4") not in (None, ""):
        return parse_gpa(r["gpa_4"], 4.0)[0]
    raise NeedUser(f"Your GPA at {r.get('school')} is {r.get('gpa')} on a {scale:g} scale, but this form asks on a "
                   f"{want:g} scale. What should be entered? (Use your school's official conversion if it has one.)",
                   store={"record": "education", "id": r.get("_id"), "field": "gpa_4"})


@rule
def education_gpa(ctx, level=None):
    r = _gpa_record(ctx, level)
    if not r or r.get("gpa") in (None, ""):
        return Answer(None)
    options = ctx.field.get("options") or []
    value = _gpa_on(ctx, r, _target_scale(ctx.field))
    if options:
        # Range dropdowns ("3.5 - 4.0", "3.75 and above", "Below 3.0") or exact values ("3.8").
        # 3.45 against "3.0 - 3.4" / "3.5 - 4.0": fall back to one decimal, rounded down, never up.
        for v in (value, int(value * 10) / 10):
            opt = choose_range_option(v, options)
            if opt is not None:
                return Answer(option_label(opt))
        return Answer(f"{value:g}")   # no option fits: the resolver asks which one
    _, scale = parse_gpa(r.get("gpa"), r.get("gpa_scale"))
    if _target_scale(ctx.field) is None and abs(scale - 4.0) > 0.01:
        if (ctx.field.get("type") or "text") == "number":
            return Answer(f"{_gpa_on(ctx, r, 4.0):g}")   # a bare number box means the US 4.0 scale
        return Answer(f"{value:g}/{scale:g}")           # text box: name the scale, so 9.1 is never read as /4
    return Answer(f"{value:g}")


_STRICT = re.compile(r"\b(above|over|greater than|higher than|more than)\b")
_INCLUSIVE = re.compile(r"\bor (higher|above|more|better|greater)\b|\band (above|up|higher)\b|\bat least\b|\bminimum\b|\+")


@rule
def gpa_at_least(ctx, threshold):
    """'Is your GPA 3.0 or higher?' against the most recent GPA (undergraduate if the label says so).
    'above 3.0' / 'higher than 3.0' is strict; 'or higher', 'at least', 'minimum' include the threshold."""
    strict = bool(_STRICT.search(ctx.label)) and not _INCLUSIVE.search(ctx.label)
    level = "bachelor" if re.search(r"\bunder ?grad", ctx.label) else None
    edu = [r for r in ctx.records("education") if r.get("gpa") not in (None, "")]
    r = _gpa_record(ctx, level) if level else (edu[0] if edu else None)
    if not r or r.get("gpa") in (None, ""):
        raise NeedUser(f"The form asks whether your GPA meets {threshold}. What is your GPA?",
                       store={"record": "education", "field": "gpa"})
    t = float(threshold)
    value = _gpa_on(ctx, r, 4.0 if t <= 4.3 else None)
    return Answer(value > t if strict else value >= t, choices="bool")


# ---------------------------------------------------------------- experience & skills

def is_current(r: dict, today: date) -> bool:
    """Flagged current and not already over (a forgotten flag on a finished job does not count)."""
    if not r.get("current"):
        return False
    end = parse_date(r.get("end_date"))
    return not end or (end[0], end[1] or 12) >= (today.year, today.month)


def _current_job(ctx) -> dict | None:
    """The candidate's current employment. Internships are not a "current company" or "current job"."""
    return next((r for r in ctx.records("experience")
                 if is_current(r, ctx.today) and r.get("employment_type") != "internship"), None)


# Whole words only: "your" or "for" must not read as "current OR most recent".
_ASKS_RECENT = re.compile(r"\b(most recent|previous|latest|last|past)\b")


def _current_or_recent(ctx) -> dict | None:
    job = _current_job(ctx)
    if job:
        return job
    exp = ctx.records("experience")
    if exp and _ASKS_RECENT.search(ctx.label):
        return exp[0]   # "current or most recent employer": the latest one, internships included
    return None


@rule
def current_employer(ctx):
    job = _current_or_recent(ctx)
    return Answer(job.get("company") if job else None)


@rule
def current_title(ctx):
    job = _current_or_recent(ctx)
    return Answer(job.get("title") if job else None)


@rule
def currently_employed(ctx):
    return Answer(_current_job(ctx) is not None, choices="bool")


@rule
def years_experience_total(ctx, include_internships=False):
    override = ctx.opt("profile.years_experience_override")
    if override is not None:
        return Answer(override)
    jobs = [r for r in ctx.records("experience")
            if include_internships or r.get("employment_type") != "internship"]
    if not jobs:
        raise NeedFact("profile.years_experience_override",
                       "No non-internship jobs on record, so the honest number depends on how you count "
                       "internships and research. Set it once.")
    spans = sorted(((_month_index(r.get("start_date"), ctx.today), _month_index(r.get("end_date"), ctx.today))
                    for r in jobs if r.get("start_date")), key=lambda s: s[0])
    months, cur_s, cur_e = 0, None, None
    for s, e in spans:
        if s is None or e is None:
            continue
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                months += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        months += cur_e - cur_s
    return Answer(round(months / 12, 1))


_GENERIC_EXPERIENCE = {"total", "the industry", "industry", "this field", "the field", "a professional setting",
                       "professional setting", "this role", "a similar role", "similar roles", "a similar position",
                       "the workforce", "full time roles", "a full time role", "your field", "your career",
                       "professional", "work", "relevant", "related", "full time", "hands on", "post graduate",
                       "postgraduate", "industry", "overall", "paid", "total professional", "relevant work",
                       "professional work"}


def lacks_skill(r: dict) -> bool:
    """A skill record means "has it" unless it explicitly says otherwise.

    Resumes list skills without years or level, so a bare record is a yes. Only
    ``level: none``, or ``years: 0`` with no level, records a skill the
    candidate does not have.
    """
    if r.get("level") == "none":
        return True
    return not r.get("level") and r.get("years") == 0


@rule
def years_with_skill(ctx, skill):
    if normalize(skill) in _GENERIC_EXPERIENCE:
        return years_experience_total(ctx)
    r = _find_named(ctx.records("skill"), skill, "aliases")
    if r and r.get("years") is not None:
        return Answer(r["years"])
    if r and lacks_skill(r):
        return Answer(0)
    raise NeedUser(f"How many years of experience do you have with {skill}?",
                   store={"record": "skill", "template": {"name": skill, "years": "<number>"}})


@rule
def has_skill(ctx, skill):
    r = _find_named(ctx.records("skill"), skill, "aliases")
    if r:
        return Answer(not lacks_skill(r), choices="bool")
    raise NeedUser(f"Do you have experience with {skill}? If yes, roughly how many years and at what level?",
                   store={"record": "skill", "template": {"name": skill, "years": "<number, 0 for no>",
                                                          "level": "<beginner|intermediate|advanced|expert|none>"}})


@rule
def skill_proficiency(ctx, skill):
    lang = _find_named(ctx.records("language"), skill)
    if lang and lang.get("proficiency"):
        return Answer(lang["proficiency"], choices="language_proficiency")
    r = _find_named(ctx.records("skill"), skill, "aliases")
    if r and r.get("level"):
        return Answer(r["level"], choices="skill_level")
    raise NeedUser(f"What is your proficiency in {skill}?",
                   store={"record": "skill", "template": {"name": skill, "level": "<beginner|intermediate|advanced|expert>"}})


@rule
def speaks_language(ctx, language):
    r = _find_named(ctx.records("language"), language)
    if not r:
        # Setup records every language the candidate works in; absent means no.
        return Answer(False, choices="bool")
    level = r.get("proficiency", "")
    strict = any(w in ctx.label for w in ("fluent", "proficient", "bilingual", "professional"))
    ok = level in (("native", "fluent", "professional") if strict else ("native", "fluent", "professional", "conversational"))
    return Answer(ok, choices="bool")


@rule
def skills_list(ctx, limit=25):
    skills = sorted(ctx.records("skill"), key=lambda r: (-(r.get("years") or 0)))
    names = [r["name"] for r in skills if not lacks_skill(r)][:limit]
    if ctx.field.get("options") or ctx.field.get("picks"):   # checkboxes, or a search-and-pick menu: one by one
        return Answer(names or None)
    return Answer(", ".join(names) if names else None)


@rule
def languages_list(ctx):
    langs = ctx.records("language")
    if not langs:
        raise NeedUser("Which languages do you speak, and at what level?", store={"record": "language"})
    if ctx.field.get("options"):   # "Language skills (check all that apply)"
        return Answer([r["name"] for r in langs])
    return Answer(", ".join(f"{r['name']} ({r['proficiency'].replace('_', ' ').title()})" if r.get("proficiency")
                            else r["name"] for r in langs))


@rule
def records_list(ctx, kind):
    items = ctx.records(kind)
    if not items:
        return Answer(None)
    out = []
    for r in items:
        name = r.get("name") or r.get("title")
        extra = ", ".join(str(r[k]) for k in ("issuer", "venue", "date") if r.get(k))
        out.append(f"{name} ({extra})" if extra else name)
    return Answer("; ".join(out))


@rule
def has_certification(ctx, cert):
    return Answer(_find_named(ctx.records("certification"), cert) is not None, choices="bool")


# ---------------------------------------------------------------- compensation & logistics

def _money(n) -> str:
    return f"{int(n):,}" if float(n) == int(n) else f"{n:,.2f}"


@rule
def salary_expectation(ctx):
    ftype = (ctx.field.get("type") or "text").lower()
    target = ctx.opt("comp.salary_target_annual")
    minimum = ctx.opt("comp.salary_min_annual")
    if ftype == "number" or ctx.field.get("options"):
        if target is None and minimum is None:
            raise NeedFact("comp.salary_target_annual")
        return Answer(target if target is not None else minimum)
    text = ctx.opt("comp.salary_expectation_text")
    if text:
        return Answer(text)
    if target is None and minimum is None:
        raise NeedFact("comp.salary_expectation_text")
    currency = ctx.opt("comp.currency", "")
    return Answer(f"{currency} {_money(target if target is not None else minimum)}".strip())


_MONEY = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", re.IGNORECASE)


@rule
def salary_range_ok(ctx):
    nums = []
    for m in _MONEY.finditer(ctx.field.get("label", "")):
        n = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        if n >= 1000:
            nums.append(n)
    if not nums:
        raise NeedUser("The form asks whether the posted salary range works for you, but the range is not in "
                       "the question. Does it?")
    minimum = ctx.fact("comp.salary_min_annual")
    if max(nums) >= minimum:
        return Answer(True, choices="bool")
    raise NeedUser(f"The posted range tops out at {_money(max(nums))}, below your minimum of {_money(minimum)}. "
                   "Answer this one yourself.")


@rule
def onsite_ok(ctx):
    arr = set(ctx.fact("prefs.work_arrangements"))
    if "onsite" in arr:
        return Answer(True, choices="bool")
    m = re.search(r"\b(\d) days\b", ctx.label)
    hybrid = "hybrid" in ctx.label or (m and int(m.group(1)) < 5)
    return Answer(bool(hybrid and "hybrid" in arr), choices="bool")


@rule
def remote_ok(ctx):
    return Answer("remote" in set(ctx.fact("prefs.work_arrangements")), choices="bool")


@rule
def preferred_location(ctx):
    prefs = ctx.fact("prefs.preferred_locations")
    options = ctx.field.get("options") or []
    if not options:
        return Answer(prefs[0] if prefs else None)
    multi = (ctx.field.get("type") or "").lower() in ("multiselect", "checkbox")
    picked = []
    for p in prefs:
        city = p.split(",")[0]
        opt = choose_text_option(p, options) or choose_text_option(city, options)
        if opt is not None and opt not in picked:
            picked.append(opt)
            if not multi:
                break
    if not picked:
        if any(normalize(option_label(o)) in ("remote", "anywhere", "any location", "no preference") for o in options):
            return Answer(next(o for o in options if normalize(option_label(o)) in
                               ("remote", "anywhere", "any location", "no preference")))
        raise NeedUser(f"None of your preferred locations {prefs} appear in the options. Which do you want?")
    return Answer([option_label(o) for o in picked] if multi else option_label(picked[0]))


@rule
def travel_ok(ctx, pct=None):
    willing = ctx.fact("prefs.willing_to_travel")
    if willing and pct is not None:
        limit = ctx.opt("prefs.max_travel_percent")
        if limit is not None:
            return Answer(float(pct) <= float(limit), choices="bool")
    return Answer(bool(willing), choices="bool")


@rule
def seeking_employment_type(ctx, etype="full_time"):
    scores = classify_option(normalize(etype), choice_set("employment_type"))
    target = max(scores, key=scores.get) if scores else etype
    return Answer(target in ctx.fact("prefs.employment_types"), choices="bool")


@rule
def application_role(ctx):
    if not ctx.app or not ctx.app.get("role"):
        raise NeedUser("Which role is this application for? Set it with `jobfill app update --role`.")
    return Answer(ctx.app["role"])


@rule
def how_heard(ctx, detail=False):
    """Where the candidate found the job, matched to however the form structures its options:
    flat ("LinkedIn"), combined ("Job Board: LinkedIn", also optgroups and grouped menus, read as
    "Group: Option"), or two steps (pick "Job Board" here; ``detail`` answers "Which job board?")."""
    source = (ctx.app or {}).get("source") or ctx.fact("source.how_heard_default")
    options = ctx.field.get("options") or []
    if not options:
        return Answer(source)
    # Nothing fits: the resolver asks which option.
    return Answer(choose_source(source, [option_label(o) for o in options], detail) or source)


_OTHER = re.compile(r"^others?\b|\bothers?$")


def choose_source(source: str, labels: list[str], detail: bool = False) -> str | None:
    """The option of ``labels`` meaning ``source``, or None when none does. Grouped options read as
    "Group: Option" (optgroups, Workday's two-level menus); a group alone is not an answer, so under
    "Job Board" only an option that is itself generic ("Other", "Other Job Board") stands for LinkedIn."""
    norms = [normalize(l) for l in labels]
    cats = source_categories(source)
    named = [i for i, n in enumerate(norms) if phrase_in(normalize(source), n) or (n and phrase_in(n, normalize(source)))]
    if named:
        def rank(i):   # under the source's most likely category first ("Job Board: LinkedIn" before "Social Media: ...")
            hit = [k for k, c in enumerate(cats) if any(phrase_in(normalize(w), norms[i]) for w in CATEGORY_WORDS[c])]
            return (min(hit) if hit else len(cats), len(norms[i]))
        return labels[min(named, key=rank)]
    if not detail:
        for c in cats:   # first step of a two-level question: the category; its follow-up gets the name
            # The most specific wording wins: "Company Careers Page" over "Job Boards / Websites".
            fit = lambda text: max((len(w) for w in CATEGORY_WORDS[c] if phrase_in(normalize(w), text)), default=0)
            scored = []
            for i, label in enumerate(labels):
                group, _, option = label.rpartition(": ")
                score = fit(normalize(option))
                if not score and group and _OTHER.search(normalize(option)):
                    score = fit(normalize(group))   # "Job Board: Other"
                scored.append((score, -i))
            best, neg_i = max(scored)
            if best:
                return labels[-neg_i]
    return None


# Category options forms use, and the sources that belong to each.
CATEGORY_WORDS = {
    "job board": ["job board", "job boards", "job site", "job sites", "job website", "job search site",
                  "online job board", "job posting site", "job listing", "job boards websites"],
    "social media": ["social media", "social network", "social networking"],
    "company website": ["company website", "careers page", "career site", "careers site", "careers website",
                        "our website", "company careers", "corporate website", "website"],
    "referral": ["referral", "referred", "employee referral", "friend", "colleague"],
    "recruiter": ["recruiter", "agency", "headhunter", "recruiting firm", "staffing"],
    "university": ["university", "college", "campus", "career fair", "career services", "school", "handshake"],
    "search engine": ["search engine", "google search", "web search"],
    "event": ["event", "conference", "meetup", "career fair", "hackathon"],
    "online": ["online", "internet", "online ad", "online advertisement", "web"],
}
_SOURCE_CATEGORIES = [
    (("linkedin",), ["job board", "social media", "online"]),
    (("indeed", "glassdoor", "ziprecruiter", "monster", "dice", "built in", "builtin", "wellfound", "angellist",
      "simplify", "hired", "otta", "welcome to the jungle", "jobright", "levels fyi", "careerbuilder"),
     ["job board", "online"]),
    (("handshake",), ["university", "job board", "online"]),
    (("company website", "careers page", "career site", "company site", "website"), ["company website", "online"]),
    (("referral", "referred", "employee", "friend", "colleague"), ["referral"]),
    (("recruiter", "agency", "headhunter"), ["recruiter"]),
    (("career fair", "university", "college", "campus", "school", "career services"), ["university", "event"]),
    (("twitter", "x com", "facebook", "instagram", "youtube", "tiktok", "reddit", "wechat", "xiaohongshu"),
     ["social media", "online"]),
    (("google", "bing", "search"), ["search engine", "online"]),
    (("conference", "meetup", "hackathon", "event"), ["event"]),
]


def source_categories(source: str) -> list[str]:
    n = normalize(source)
    for keys, cats in _SOURCE_CATEGORIES:
        if any(phrase_in(normalize(k), n) for k in keys):
            return cats
    return [c for c, words in CATEGORY_WORDS.items() if any(phrase_in(normalize(w), n) for w in words)] or ["online"]


@rule
def was_referred(ctx):
    return Answer(bool(ctx.app and ctx.app.get("referrer")), choices="bool")


@rule
def referrer_name(ctx):
    return Answer(ctx.app.get("referrer") if ctx.app else None)


# ---------------------------------------------------------------- documents

@rule
def resume_file(ctx):
    """The CV set for this application (tailored to the posting), else the default resume."""
    own = ctx.app.get("resume") if ctx.app else None
    return Answer(own) if own else Answer(ctx.fact("documents.resume_path"), source="fact:documents.resume_path")
