"""Text normalization, similarity scoring and option matching. No AI involved."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher

_TRANSLATE = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})

STOPWORDS = frozenset("""
a an the to of in on at for and or with by from as is are be been being am was were do does did
you your yours yourself we our ours they them their it its this that these those any all if please
will would can could should shall may might have has had what which who whom how when where there
here i me my so such than then also just about into over per via eg ie etc
whats hows whos wheres theres thats youre youve youd youll im ive id ill lets
""".split())

COMPANY_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co",
                    "company", "plc", "gmbh", "ag", "sa", "bv", "pbc", "technologies", "labs", "group"}


def normalize(text: str | None, company: str | None = None, role: str | None = None) -> str:
    """Lowercase, strip punctuation/apostrophes, collapse spaces.

    Keeps ``{}`` (placeholders), ``+`` and ``#`` (C++, C#). When ``company`` or
    ``role`` is given, their occurrences become ``{company}`` / ``{role}`` so one
    alias covers every employer.
    """
    t = unicodedata.normalize("NFKC", text or "").translate(_TRANSLATE).lower()
    t = re.sub(r"\bu\.\s?s\.(\s?a\.)?", "us ", t)
    t = re.sub(r"\((?:required|optional)\)|\*", " ", t)
    t = t.replace("'", "")
    t = re.sub(r"[^a-z0-9{}+#]+", " ", t)
    t = " ".join(t.split())
    if role:
        t = _placeholder(t, role, "{role}", strip_suffixes=False)
    if company:
        t = _placeholder(t, company, "{company}", strip_suffixes=True)
    return t


def _placeholder(t: str, name: str, token: str, strip_suffixes: bool) -> str:
    variants = []
    full = normalize(name)
    if full:
        variants.append(full)
    if strip_suffixes:
        words = full.split()
        while words and words[-1] in COMPANY_SUFFIXES:
            words = words[:-1]
        short = " ".join(words)
        if short and short != full:
            variants.append(short)
    for v in variants:
        if len(v) >= 2:
            t = re.sub(rf"(?<![a-z0-9]){re.escape(v)}s?(?![a-z0-9])", token, t)
    return t


def company_key(name: str | None) -> str:
    """'Stripe, Inc.' and 'Stripe' compare equal."""
    words = normalize(name).split()
    while words and words[-1] in COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


def content_tokens(t: str) -> set[str]:
    return {w for w in t.split() if w not in STOPWORDS}


def similarity(label: str, alias: str) -> float:
    """Score in [0, 1] between two *normalized* strings."""
    if label == alias:
        return 1.0
    seq = SequenceMatcher(None, label, alias).ratio()
    ta, tb = content_tokens(label), content_tokens(alias)
    if not ta or not tb:
        return seq
    jac = len(ta & tb) / len(ta | tb)
    score = 0.6 * jac + 0.4 * seq
    # Alias fully contained in a longer label ("salary expectations" inside
    # "what are your salary expectations for this role"): scale by coverage.
    # Extra label words may change the meaning ("years of RUST experience"), so
    # partial coverage stays below the accept threshold and goes to review.
    if len(tb) >= 2 and tb <= ta:
        score = max(score, 0.55 + 0.4 * len(tb) / len(ta))
    return score


def phrase_in(phrase: str, text: str) -> bool:
    """Whole-word (plural-tolerant) containment on normalized strings."""
    if not phrase:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}s?(?![a-z0-9])", text) is not None


# --------------------------------------------------------------------------- geography

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico",
}
_STATE_BY_NAME = {normalize(v): v for v in US_STATES.values()}

COUNTRY_ALIASES = {
    "United States": ["us", "usa", "united states", "united states of america", "america", "u s", "u s a",
                      "the us", "the united states", "the usa"],
    "United Kingdom": ["uk", "united kingdom", "great britain", "britain", "england", "gb", "the uk"],
    "Canada": ["canada"], "Mexico": ["mexico"], "Brazil": ["brazil"],
    "China": ["china", "prc", "peoples republic of china", "mainland china"],
    "Hong Kong": ["hong kong", "hong kong sar"], "Taiwan": ["taiwan"], "Japan": ["japan"],
    "South Korea": ["south korea", "korea", "republic of korea"], "India": ["india"],
    "Singapore": ["singapore"], "Australia": ["australia"], "New Zealand": ["new zealand"],
    "Germany": ["germany", "deutschland"], "France": ["france"], "Netherlands": ["netherlands", "the netherlands", "holland"],
    "Ireland": ["ireland"], "Switzerland": ["switzerland"], "Sweden": ["sweden"], "Denmark": ["denmark"],
    "Norway": ["norway"], "Finland": ["finland"], "Spain": ["spain"], "Italy": ["italy"], "Portugal": ["portugal"],
    "Poland": ["poland"], "Belgium": ["belgium"], "Austria": ["austria"], "Israel": ["israel"],
    "United Arab Emirates": ["uae", "united arab emirates"],
}
_COUNTRY_BY_ALIAS = {normalize(a): c for c, aliases in COUNTRY_ALIASES.items() for a in aliases}


def canonical_country(text: str | None) -> str | None:
    """Map 'USA' / 'U.S.' / 'United States of America' to one name; None if unknown."""
    n = normalize(text)
    if not n:
        return None
    if n in _COUNTRY_BY_ALIAS:
        return _COUNTRY_BY_ALIAS[n]
    # "us for any employer" / "the country where this job is located": try prefixes.
    words = n.split()
    for size in range(len(words) - 1, 0, -1):
        prefix = " ".join(words[:size])
        if prefix in _COUNTRY_BY_ALIAS:
            return _COUNTRY_BY_ALIAS[prefix]
    return None


def canonical_state(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip()
    if t.upper() in US_STATES:
        return US_STATES[t.upper()]
    return _STATE_BY_NAME.get(normalize(t))


# --------------------------------------------------------------------------- field instructions

# "If it doesn't apply to you, please enter "None"." / "Enter N/A if not applicable".
_NOT_APPLICABLE = r"(?:not appl\w*|n/a|doesn't apply|does not apply|not currently employed|not employed|you don't have)"
_ENTER = r"(?:enter|type|write|put|answer|use|input|respond with|fill in)"
_NA_TOKEN = r"(?:\"([^\"]{1,15})\"|'([^']{1,15})'|\b(none|n/a|na|0)\b)"
_NA_PATTERNS = [re.compile(rf"\b(?:if|when)\b[^.;]*?{_NOT_APPLICABLE}[^.;]*?\b{_ENTER}\s*:?\s*{_NA_TOKEN}", re.I),
                re.compile(rf"\b{_ENTER}\s*:?\s*{_NA_TOKEN}[^.;]*?\b(?:if|when)\b[^.;]*?{_NOT_APPLICABLE}", re.I)]


def not_applicable_answer(instructions: str | None) -> str | None:
    """What the form says to enter when the question does not apply to the candidate ("None", "N/A")."""
    t = (instructions or "").translate(_TRANSLATE)
    for pat in _NA_PATTERNS:
        m = pat.search(t)
        if m:
            token = next(g for g in m.groups() if g)
            return token.strip() if not token.islower() else token.upper() if token in ("n/a", "na") else \
                token.capitalize()
    return None


# --------------------------------------------------------------------------- options

def names_same(option: str, value: str) -> bool:
    """``option`` is ``value``, or ``value`` with a qualifier a form adds: "Job Boards > LinkedIn",
    "United States of America (+1)", "Python (Programming Language)". A shared word is not enough:
    "University of Wisconsin" is not "University of Wisconsin-Madison", SQL is not "Interactive SQL"."""
    w = normalize(value)
    if not w:
        return False
    if normalize(option) == w or normalize(re.split(r"\s+>\s+|:\s+", option)[-1]) == w:
        return True
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", option)
    return bool(m) and w in (normalize(m.group(1)), normalize(m.group(2)))


def closest_options(value: str, options: list, n: int = 10) -> list[str]:
    """The ``n`` options most like ``value`` (to show the candidate when none is exactly it)."""
    v = normalize(value)
    labels = [option_label(o) for o in options]
    scored = sorted(labels, key=lambda l: -SequenceMatcher(None, v, normalize(l)).ratio())
    return scored[:n]


def option_label(option) -> str:
    if isinstance(option, dict):
        return str(option.get("label") or option.get("text") or option.get("value") or "")
    return str(option)


def classify_option(option_norm: str, choice_set: dict) -> dict[str, float]:
    """Score how strongly an option expresses each canonical value.

    Score = length of the longest synonym found in the option (+1000 for an
    exact match), so longer, more specific phrases win.
    """
    text = option_norm
    for pattern in choice_set.get("_ignore", ()):
        text = pattern.sub(" ", text)
    text = " ".join(text.split())
    scores: dict[str, float] = {}
    for canon, synonyms in choice_set["_values"].items():
        best = 0.0
        for syn in synonyms:
            if text == syn:
                best = max(best, 1000.0 + len(syn))
            elif phrase_in(syn, text):
                best = max(best, float(len(syn)))
        if best:
            scores[canon] = best
    return scores


_NEGATION = re.compile(r"\b(not|no|never|dont|doesnt|wont|cannot|cant|without|none)\b")


def choose_enum_option(target: str, options: list, choice_set: dict):
    """Return the option expressing canonical ``target`` (or None)."""
    labels = [option_label(o) for o in options]
    norms = [normalize(l) for l in labels]
    best, best_margin = None, None
    for opt, norm in zip(options, norms):
        scores = classify_option(norm, choice_set)
        if target not in scores:
            continue
        others = max((s for c, s in scores.items() if c != target), default=0.0)
        margin = scores[target] - others
        if margin < 0 and not re.search(r"\b(or|and)\b", norm):
            continue  # a different value fits this option better
        if best_margin is None or margin > best_margin:
            best, best_margin = opt, margin
    if best is not None:
        return best
    # Two unlabeled options like "I will require sponsorship" / "I will not ...".
    if target in ("true", "false") and len(options) == 2:
        neg = [bool(_NEGATION.search(n)) for n in norms]
        if neg[0] != neg[1]:
            want_neg = target == "false"
            return options[neg.index(want_neg)]
    return None


_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)")
_PLUS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\+|or more|plus|and above|or greater|and up)")
_MORE = re.compile(r"(?:more than|over|greater than|at least|above)\s*(\d+(?:\.\d+)?)")
_LESS = re.compile(r"(?:less than|under|fewer than|below|up to)\s*(\d+(?:\.\d+)?)")
_SINGLE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?)?\s*$")


def parse_range(label: str) -> tuple[float, float, bool] | None:
    """Parse '1-3 years', '5+', 'Less than 1 year' -> (low, high, high_exclusive)."""
    s = label.lower().replace(",", "")
    if re.search(r"\b(none|no experience)\b", s):
        return (0.0, 0.0, False)
    if m := _RANGE.search(s):
        return (float(m.group(1)), float(m.group(2)), False)
    if m := _PLUS.search(s):
        return (float(m.group(1)), math.inf, False)
    if m := _MORE.search(s):
        return (float(m.group(1)) + 1e-9, math.inf, False)
    if m := _LESS.search(s):
        exclusive = "up to" not in s
        return (0.0, float(m.group(1)), exclusive)
    if m := _SINGLE.match(s):
        v = float(m.group(1))
        return (v, v, False)
    return None


def choose_range_option(number: float, options: list):
    hits = []
    for opt in options:
        r = parse_range(option_label(opt))
        if r is None:
            continue
        lo, hi, excl = r
        if lo <= number and (number < hi or (number == hi and not excl)):
            # Prefer the range where the number is not sitting on the upper edge
            # ("3" -> "3-5" rather than "1-3").
            hits.append((number == hi, hi - lo, opt))
    if not hits:
        return None
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits[0][2]


def choose_text_option(value: str, options: list, kind: str | None = None):
    """Pick the option that means ``value`` (country/state aware, then fuzzy)."""
    if kind == "country":
        target = canonical_country(value) or value
        for opt in options:
            if (canonical_country(option_label(opt)) or option_label(opt)) == target:
                return opt
    if kind == "state":
        target = canonical_state(value) or value
        for opt in options:
            lab = option_label(opt)
            if (canonical_state(lab) or lab) == target:
                return opt
    v = normalize(value)
    if not v:
        return None
    norms = [normalize(option_label(o)) for o in options]
    for opt, n in zip(options, norms):
        if n == v:
            return opt
    contained = [(len(n), opt) for opt, n in zip(options, norms) if n and (phrase_in(v, n) or phrase_in(n, v))]
    if contained:
        contained.sort(key=lambda c: -c[0])
        return contained[0][1]
    scored = [(SequenceMatcher(None, v, n).ratio(), opt) for opt, n in zip(options, norms)]
    scored.sort(key=lambda s: -s[0])
    if scored and scored[0][0] >= 0.85:
        return scored[0][1]
    return None


# --------------------------------------------------------------------------- dates

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]


_MONTH_NAME = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?", re.I)
_MONTH_INDEX = {m[:3].lower(): i + 1 for i, m in enumerate(_MONTHS)}
ONGOING = re.compile(r"^\s*(present|current|currently|now|ongoing|today|to date)\s*$", re.I)


def _valid(y: int, mo: int | None, d: int | None):
    if not 1900 <= y <= 2100 or (mo is not None and not 1 <= mo <= 12):
        return None
    if d is not None:
        try:
            date(y, mo or 1, d)
        except ValueError:
            return None
    return y, mo, d


def parse_date(value) -> tuple[int, int | None, int | None] | None:
    """Read a date written almost any common way; returns (year, month|None, day|None).

    Accepts 2025, 2025-09, 2025/9, 2025.09.01, 9/2025, 09-2025, 09/01/2025 (US month first; day first
    when the first number is over 12), Sep 2025, Sep/2025, Sep,2025, September 1, 2025, 1 Sep 2025,
    2025 Sep. Two-digit years are rejected as ambiguous.
    """
    if isinstance(value, date):
        return value.year, value.month, value.day
    s = str(value or "").strip()
    if not s:
        return None
    if m := re.fullmatch(r"(\d{4})(?:\s*[-/.]\s*(\d{1,2})(?:\s*[-/.]\s*(\d{1,2}))?)?", s):
        y, mo, d = m.groups()
        return _valid(int(y), int(mo) if mo else None, int(d) if d else None)
    if m := re.fullmatch(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})", s):
        a, b, y = map(int, m.groups())
        mo, d = (b, a) if a > 12 >= b else (a, b)
        return _valid(y, mo, d)
    if m := re.fullmatch(r"(\d{1,2})\s*[-/.]\s*(\d{4})", s):
        return _valid(int(m.group(2)), int(m.group(1)), None)
    name, year = _MONTH_NAME.search(s), re.search(r"\b(19|20)\d{2}\b", s)
    if name and year:
        rest = (s[:name.start()] + " " + s[name.end():]).replace(year.group(0), " ")
        day = re.search(r"\b(\d{1,2})(st|nd|rd|th)?\b", rest)
        return _valid(int(year.group(0)), _MONTH_INDEX[name.group(1).lower()], int(day.group(1)) if day else None)
    return None


def iso_date(value) -> str | None:
    """Canonical storage form, keeping the precision given: 2025 / 2025-09 / 2025-09-01."""
    p = parse_date(value)
    if p is None:
        return None
    y, mo, d = p
    return f"{y:04d}" + (f"-{mo:02d}" if mo else "") + (f"-{d:02d}" if mo and d else "")


def month_number(text: str) -> int | None:
    """'September', 'Sep', 'Sept.', '09', '9', '09 - September' -> 9."""
    if m := _MONTH_NAME.search(text or ""):
        return _MONTH_INDEX[m.group(1).lower()]
    if m := re.fullmatch(r"\s*0?(\d{1,2})\s*", text or ""):
        n = int(m.group(1))
        return n if 1 <= n <= 12 else None
    return None


_FMT_TOKEN = r"(?:yyyy|yy|mmmm|month|mmm|mon|mm|m|dd|d)"
_FMT_RE = re.compile(rf"(?<![a-z]){_FMT_TOKEN}(?:\s*[/\-., ]\s*{_FMT_TOKEN}){{1,2}}(?![a-z])", re.I)
_FMT_MAP = {"yyyy": "YYYY", "yy": "YY", "mmmm": "MMMM", "month": "MMMM", "mmm": "MMM", "mon": "MMM",
            "mm": "MM", "m": "M", "dd": "DD", "d": "D"}


def detect_date_format(*texts: str | None) -> str | None:
    """The date format a form asks for, from its label or placeholder.

    Token style ("MM/YYYY", "mm/dd/yyyy", "Month YYYY") or an example ("e.g. 09/2025", "Sep 2025").
    """
    for text in texts:
        if not text:
            continue
        for m in _FMT_RE.finditer(text):
            raw = m.group(0)
            tokens = re.findall(_FMT_TOKEN, raw, re.I)
            low = [t.lower() for t in tokens]
            if not any(t.startswith("y") for t in low) or not any(t[0] == "m" for t in low):
                continue   # needs a year and a month, so "m d" in prose is not a format
            return re.sub(_FMT_TOKEN, lambda t: _FMT_MAP[t.group(0).lower()], raw, flags=re.I)
        for pattern, fmt in ((r"\b\d{1,2}/\d{1,2}/\d{4}\b", "MM/DD/YYYY"), (r"\b\d{4}-\d{2}-\d{2}\b", "YYYY-MM-DD"),
                             (r"\b\d{1,2}/\d{4}\b", "MM/YYYY"), (r"\b\d{4}-\d{1,2}\b", "YYYY-MM"),
                             (r"\b\d{4}/\d{1,2}\b", "YYYY/MM")):
            if re.search(pattern, text):
                return fmt
        if m := re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)([a-z]*)\.?,?\s+(19|20)\d{2}\b", text, re.I):
            sep = "," if "," in m.group(0) else ""
            return ("MMMM" if len(m.group(1) + m.group(2)) > 4 else "MMM") + sep + " YYYY"
    return None


def strip_date_format(text: str) -> str:
    """Remove format hints from a label: 'Start (MM/YYYY)' -> 'Start', 'Date e.g. 09/2025' -> 'Date'."""
    def is_format(m):
        low = [t.lower() for t in re.findall(_FMT_TOKEN, m.group(0), re.I)]
        return any(t.startswith("y") for t in low) and any(t[0] == "m" for t in low)
    out = _FMT_RE.sub(lambda m: " " if is_format(m) else m.group(0), text or "")
    out = re.sub(r"\b(e\.?g\.?|format:?|for example)\s*[\d/.\-]+", " ", out, flags=re.I)
    out = re.sub(r"\(\s*\)", " ", out)
    return " ".join(out.split())


def date_part(label: str, options: list | None = None) -> str | None:
    """Which piece of a date a field wants: 'month', 'year', 'day', or None for the whole date.

    From the label ("Start month", "Graduation year"), else from the options (twelve month names, a
    list of years). "Month/Year" or "MM/YYYY" asks for the whole date.
    """
    n = normalize(label)
    asked = [p for p, rx in (("month", r"\b(month|mm|mon)\b"), ("year", r"\b(year|yyyy|yy)\b"), ("day", r"\b(day|dd)\b"))
             if re.search(rx, n)]
    if len(asked) == 1:
        return asked[0]
    if not asked and options:
        labels = [option_label(o) for o in options]
        if len(labels) >= 6 and sum(month_number(l) is not None and not re.search(r"\d{4}", l) for l in labels) >= 0.8 * len(labels) \
                and len(labels) <= 13:
            return "month"
        if sum(bool(re.fullmatch(r"\s*(19|20)\d{2}\s*", l)) for l in labels) >= 0.8 * len(labels):
            return "year"
    return None


def choose_date_option(parts: tuple, part: str | None, options: list):
    """The dropdown option for a date (or the requested piece of it)."""
    y, mo, d = parts
    for opt in options:
        lab = option_label(opt)
        if part == "month":
            if mo and month_number(lab) == mo:
                return opt
        elif part == "year":
            if re.search(rf"(?<!\d){y}(?!\d)", lab):
                return opt
        elif part == "day":
            if d and re.fullmatch(rf"\s*0?{d}(st|nd|rd|th)?\s*", lab):
                return opt
        else:
            p = parse_date(lab)
            if p and p[0] == y and (p[1] is None or p[1] == mo) and (p[2] is None or p[2] == d):
                return opt
    return None


def format_date(value, fmt: str | None) -> str | None:
    """Format a date with tokens YYYY YY MMMM MMM MM M DD D.

    A missing day becomes the 1st. Returns None when the format needs a month the value does not
    have (a year-only date cannot be written as MM/YYYY). Unparseable input passes through.
    """
    parts = parse_date(value)
    if not fmt or parts is None:
        return str(value)
    y, mo, d = parts
    if mo is None and re.search(r"M", fmt):
        return None
    mo = mo or 1
    d = d or 1
    tokens = {
        "YYYY": f"{y:04d}", "YY": f"{y % 100:02d}", "MMMM": _MONTHS[mo - 1], "MMM": _MONTHS[mo - 1][:3],
        "MM": f"{mo:02d}", "M": str(mo), "DD": f"{d:02d}", "D": str(d),
    }
    return re.sub(r"YYYY|YY|MMMM|MMM|MM|M|DD|D", lambda m: tokens[m.group(0)], fmt)
