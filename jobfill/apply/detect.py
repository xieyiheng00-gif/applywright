"""Decide which application system a job URL uses, without AI.

Order: URL rules (no network), then the page HTML (embedded Greenhouse/Lever/iCIMS/Ashby/Workable forms,
schema.org JobPosting markup). Only when both are inconclusive does the agent look at the
page itself (``handler == "agent_check"``).

handler   meaning
python    Greenhouse / Lever / iCIMS / Ashby / Workday / Workable: jobfill's Playwright filler does the work
ai        company portal or other ATS: the agent fills it in the built-in browser
follow    job board listing (LinkedIn, Indeed...): find the employer's apply link first
unsupported  known but not handled yet
agent_check  page could not be classified from its HTML; the agent decides
none      not a job posting -> error
"""

from __future__ import annotations

import html as htmllib
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, urlparse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

PYTHON_ATS = {"greenhouse", "lever", "icims", "ashby", "workday", "workable"}

# Other ATS products: recognised, filled by the agent in the browser.
OTHER_ATS = {
    "smartrecruiters": r"(^|\.)smartrecruiters\.com$",
    "jobvite": r"(^|\.)jobvite\.com$", "taleo": r"(^|\.)taleo\.net$",
    "successfactors": r"(successfactors\.(com|eu)|sapsf\.(com|eu))$", "bamboohr": r"(^|\.)bamboohr\.com$",
    "recruitee": r"(^|\.)recruitee\.com$",
    "rippling": r"(^|\.)rippling\.com$", "jazzhr": r"(^|\.)applytojob\.com$",
    "breezy": r"(^|\.)breezy\.hr$", "eightfold": r"(^|\.)eightfold\.ai$", "oracle": r"oraclecloud\.com$",
    "phenom": r"(^|\.)phenompeople\.com$",
}

# Companies with their own application portal.
CUSTOM_PORTALS = {
    "amazon": (r"(^|\.)amazon\.jobs$", "Amazon"),
    "tiktok": (r"(^|\.)(lifeattiktok\.com|careers\.tiktok\.com)$", "TikTok"),
    "bytedance": (r"(^|\.)jobs\.bytedance\.com$", "ByteDance"),
    "google": (r"(^|\.)(careers\.google\.com|google\.com)$", "Google"),
    "meta": (r"(^|\.)metacareers\.com$", "Meta"),
    "apple": (r"(^|\.)jobs\.apple\.com$", "Apple"),
    "microsoft": (r"(^|\.)(careers\.microsoft\.com|jobs\.careers\.microsoft\.com)$", "Microsoft"),
    "netflix": (r"(^|\.)(jobs\.netflix\.com|explore\.jobs\.netflix\.net)$", "Netflix"),
    "ibm": (r"(^|\.)careers\.ibm\.com$", "IBM"),
    "uber": (r"(^|\.)uber\.com$", "Uber"),
}

AGGREGATORS = {
    "linkedin": (r"(^|\.)linkedin\.com$", r"^/jobs/(view|collections)/|currentJobId="),
    "indeed": (r"(^|\.)indeed\.com$", r"^/(viewjob|rc/clk|m/viewjob)|jk="),
    "glassdoor": (r"(^|\.)glassdoor\.(com|co\.uk|ca)$", r"^/job-listing/|jobListingId="),
    "handshake": (r"(^|\.)joinhandshake\.com$", r"/jobs/\d+"),
    "wellfound": (r"(^|\.)wellfound\.com$", r"/jobs/\d+"),
    "ziprecruiter": (r"(^|\.)ziprecruiter\.com$", r"/(jobs|c)/"),
}

JOB_PATH = re.compile(r"/(jobs?|careers?|positions?|openings?|opportunit(y|ies)|vacanc(y|ies)|requisitions?|"
                      r"role|posting|apply)(/|$|-|\?|=)|[?&](gh_jid|jobid|job_id|jid|req(uisition)?_?id)=", re.I)


@dataclass
class Detection:
    url: str
    kind: str = "unknown"            # job | not_job | unknown
    ats: str | None = None
    handler: str = "agent_check"
    apply_url: str | None = None
    company: str | None = None
    role: str | None = None
    location: str | None = None
    posting: str | None = None
    reason: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["posting"] and len(d["posting"]) > 300:
            d["posting_chars"] = len(d["posting"])
            d["posting"] = d["posting"][:300] + "..."
        return d


def _host_matches(host: str, pattern: str) -> bool:
    return re.search(pattern, host) is not None


def _titleize(slug: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", slug) if w)


def _strip_html(text: str) -> str:
    text = htmllib.unescape(text or "")
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|div|ul|ol|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmllib.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def fetch(url: str, timeout: float = 15.0, max_bytes: int = 3_000_000) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes).decode(r.headers.get_content_charset() or "utf-8", errors="replace")


def fetch_json(url: str, timeout: float = 30.0):
    # Whole-board APIs (Ashby) can be tens of MB for big employers; a cut-off JSON is useless.
    return json.loads(fetch(url, timeout, max_bytes=100_000_000))


# --------------------------------------------------------------------------- per-ATS URL rules

def _greenhouse(d: Detection, u, network: bool) -> Detection:
    q = parse_qs(u.query)
    m = re.match(r"^/([\w.-]+)/jobs/(\d+)", u.path)
    if u.path.startswith("/embed/job_app") and q.get("token"):
        board, job_id = (q.get("for") or [""])[0], q["token"][0]
    elif m:
        board, job_id = m.group(1), m.group(2)
    elif q.get("gh_jid") and q.get("for"):
        board, job_id = q["for"][0], q["gh_jid"][0]
    else:
        return _not_job(d, "Greenhouse board or search page, not a single job (expected /<company>/jobs/<id>).")
    d.kind, d.ats, d.handler = "job", "greenhouse", "python"
    # The Greenhouse page itself when we have it, else the embeddable form for a company-site job.
    d.apply_url = u.geturl() if (m or u.path.startswith("/embed/")) else \
        f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={job_id}"
    d.company = _titleize(board) if board else None
    d.extra = {"board": board, "job_id": job_id}
    if network and board:
        try:
            info = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}")
            d.role = info.get("title")
            d.company = info.get("company_name") or d.company
            d.location = (info.get("location") or {}).get("name")
            d.posting = _strip_html(info.get("content", ""))
        except Exception as e:  # the form itself may still load
            d.extra["api_error"] = str(e)[:200]
    return d


def _lever(d: Detection, u, network: bool) -> Detection:
    m = re.match(rf"^/([\w.-]+)/({UUID})(/apply)?/?$", u.path, re.I)
    if not m:
        return _not_job(d, "Lever company page, not a single job (expected /<company>/<posting-id>).")
    company, posting_id = m.group(1), m.group(2)
    d.kind, d.ats, d.handler = "job", "lever", "python"
    d.apply_url = f"https://{u.netloc}/{company}/{posting_id}/apply"
    d.company = _titleize(company)
    d.extra = {"site": company, "posting_id": posting_id}
    if network:
        api = "api.eu.lever.co" if ".eu." in u.netloc else "api.lever.co"
        try:
            info = fetch_json(f"https://{api}/v0/postings/{company}/{posting_id}")
            d.role = info.get("text")
            d.location = (info.get("categories") or {}).get("location")
            parts = [info.get("descriptionPlain", "")]
            for block in info.get("lists") or []:
                parts.append(f"{block.get('text', '')}\n{_strip_html(block.get('content', ''))}")
            parts.append(info.get("additionalPlain", ""))
            d.posting = "\n\n".join(p for p in parts if p).strip()
        except Exception as e:
            d.extra["api_error"] = str(e)[:200]
    return d


def _ashby(d: Detection, u, network: bool) -> Detection:
    m = re.match(rf"^/([\w.%-]+)/({UUID})(/application)?/?$", u.path, re.I)
    if not m:
        return _not_job(d, "Ashby job board page, not a single job (expected /<company>/<job-id>).")
    board, job_id = m.group(1), m.group(2).lower()
    d.kind, d.ats, d.handler = "job", "ashby", "python"
    d.apply_url = f"https://jobs.ashbyhq.com/{board}/{job_id}/application"
    d.company = _titleize(board)
    d.extra = {"board": board, "job_id": job_id}
    if network:
        try:
            jobs = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}").get("jobs") or []
            job = next((j for j in jobs if str(j.get("id", "")).lower() == job_id), None)
            if job:   # unlisted jobs are missing from the board API but can still be valid
                d.role = job.get("title")
                d.location = job.get("location")
                d.posting = job.get("descriptionPlain") or _strip_html(job.get("descriptionHtml", ""))
        except Exception as e:
            d.extra["api_error"] = str(e)[:200]
        try:   # the page title carries the real company name: "Research Engineer @ OpenAI"
            title = re.search(r"(?is)<title[^>]*>(.*?)</title>", fetch(f"https://jobs.ashbyhq.com/{board}/{job_id}"))
            name = title and re.search(r"@\s*(.+?)\s*$", htmllib.unescape(title.group(1)).strip())
            if name:
                d.company = name.group(1)
        except Exception:
            pass
    return d


# Workable: apply.workable.com/<account>/j/<shortcode>/ (the form at .../apply/), the short link
# apply.workable.com/j/<shortcode>, or <account>.workable.com/j/<shortcode>. The public API gives the posting
# (/api/v2/accounts/<account>/jobs/<shortcode>, 404 once it is removed) and the company's name
# (/api/v1/accounts/<account>).
WORKABLE_JOB = re.compile(r"^/(?:(?P<account>[\w.-]+)/)?j/(?P<code>[0-9A-Za-z]{6,16})(?:/apply)?/?$")
WORKABLE_LINK = re.compile(r"https?://(?:apply\.workable\.com/(?:(?P<account>[\w.-]+)/)?|(?P<sub>[\w-]+)\.workable\.com/)"
                           r"j/(?P<code>[0-9A-Za-z]{6,16})\b")
WORKABLE_EMBED = re.compile(r"whr_embed\s*\(|workable\.com/assets/embed\.js|apply\.workable\.com/api/v1/widget/accounts/")
WORKABLE_SITE = {"www", "help", "resources", "blog", "engineering", "app", "id", "api", "status", "developers",
                 "support", "marketplace"}   # Workable's own sites, not a company's job list
WORKABLE_BOARD = ("a Workable job list, not a single job. Open the job and pass its link "
                  "(apply.workable.com/<company>/j/<code>/).")


def _workable_account(html: str, code: str | None = None) -> tuple[str | None, str | None]:
    """(account, shortcode) a Workable page names itself by: its canonical link, else its subdomain meta."""
    m = re.search(r'rel="canonical"\s+href="https://apply\.workable\.com/([\w.-]+)/j/([0-9A-Za-z]{6,16})', html)
    if m and (code is None or m.group(2).upper() == code):
        return m.group(1), m.group(2).upper()
    sub = re.search(r'<meta\s+name="subdomain"\s+content="([\w.-]+)"', html)
    return (sub.group(1) if sub else None), code


def _workable_location(job: dict) -> str | None:
    loc = job.get("location") or {}
    place = ", ".join(p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p)
    work = {"remote": "Remote", "hybrid": "Hybrid", "on_site": "On-site"}.get(job.get("workplace") or "")
    work = work or ("Remote" if job.get("remote") else "")
    return f"{place} ({work})" if place and work else (place or work or None)


def _workable(d: Detection, u, network: bool) -> Detection:
    host = u.netloc.lower().split(":")[0]
    if host == "jobs.workable.com":
        if u.path.startswith("/view/"):
            d.kind, d.ats, d.handler = "job", "workable", "follow"
            d.reason = ("Jobs by Workable is Workable's job board. Open the listing, click Apply (it leads to "
                        "apply.workable.com/<company>/j/<code>/), and run `apply start` on that link.")
            return d
        return _not_job(d, "Jobs by Workable page, but not a single job listing.")
    sub = host.split(".")[0] if host != "apply.workable.com" else None
    m = WORKABLE_JOB.match(u.path)
    account = (m.group("account") if m else None) or sub
    if not m or (sub and m.group("account")):
        if "not_found=true" in u.query:
            return _not_job(d, "Workable says this job does not exist any more (removed or closed).")
        if sub and network:   # an older link form: the page names the job it shows
            try:
                acc, code = _workable_account(fetch(u.geturl()))
            except Exception:
                acc, code = None, None
            if acc and code:
                return _workable(d, urlparse(f"https://apply.workable.com/{acc}/j/{code}/"), network)
        return _not_job(d, f"This is {WORKABLE_BOARD}")
    code = m.group("code").upper()
    if not account and network:   # the short link apply.workable.com/j/<code>: its page names the company
        try:
            account, _ = _workable_account(fetch(f"https://apply.workable.com/j/{code}"), code)
        except Exception as e:
            d.extra["page_error"] = str(e)[:200]
    d.kind, d.ats, d.handler = "job", "workable", "python"
    d.apply_url = (f"https://apply.workable.com/{account}/j/{code}/apply/" if account
                   else f"https://apply.workable.com/j/{code}/apply/")
    d.company = _titleize(account) if account else None
    d.extra = {"account": account, "shortcode": code, **d.extra}
    if network and account:
        try:
            job = fetch_json(f"https://apply.workable.com/api/v2/accounts/{account}/jobs/{code}")
            d.role = job.get("title")
            d.location = _workable_location(job)
            d.posting = "\n\n".join(_strip_html(job[k]) for k in ("description", "requirements", "benefits")
                                    if job.get(k)) or None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return _not_job(d, "Workable says this job does not exist any more (removed or closed).")
            d.extra["api_error"] = str(e)[:200]
        except Exception as e:   # the form itself may still load
            d.extra["api_error"] = str(e)[:200]
        try:
            d.company = fetch_json(f"https://apply.workable.com/api/v1/accounts/{account}").get("name") or d.company
        except Exception:
            pass
    return d


def _icims(d: Detection, u) -> Detection:
    m = re.match(r"^(/jobs/(\d+)(?:/[^/?#]+)?)", u.path)
    if not m or m.group(1).rstrip("/").endswith(("/search", "/intro")):
        return _not_job(d, "iCIMS search or landing page, not a single job (expected /jobs/<id>/...).")
    base = m.group(1)
    if base.endswith("/job"):
        base = base[: -len("/job")]
    d.kind, d.ats, d.handler = "job", "icims", "python"
    d.apply_url = f"{u.scheme}://{u.netloc}{base}/login"
    sub = u.netloc.split(".")[0]
    sub = re.sub(r"^(careers?|jobs|uscareers|external|hr)-", "", sub)
    sub = re.sub(r"^(apac|emea|amer|americas|us|uk|eu|na|latam|global|intl|international)-", "", sub)
    d.company = _titleize(sub)
    d.extra = {"job_id": m.group(2)}
    return d


def _workday(d: Detection, u) -> Detection:
    """Workday: every company is a tenant (acme.wd5.myworkdayjobs.com) with its own applicant account."""
    d.kind, d.ats, d.handler, d.apply_url = "job", "workday", "python", u.geturl()
    tenant = u.netloc.split(".")[0]
    d.company = d.company or _titleize(tenant)
    m = re.search(r"/job/[^/]+/([^/?#]+)", u.path)
    if m:
        d.role = re.sub(r"_[A-Z]*-?\d+$", "", m.group(1)).replace("-", " ").strip() or None
    d.extra = {"tenant": tenant}
    d.reason = ("Workday needs an applicant account for this company: when `apply fill` reports login_required, "
                "the candidate runs `python -m jobfill login --app <ID>`.")
    return d


def _not_job(d: Detection, reason: str) -> Detection:
    d.kind, d.handler, d.reason = "not_job", "none", reason
    return d


# --------------------------------------------------------------------------- page inspection

def _from_html(d: Detection, html: str, network: bool) -> Detection:
    u = urlparse(d.url)
    # Embedded Greenhouse form (company careers site with ?gh_jid=...).
    gh = re.search(r"https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/embed/job_app\?[^\"'<> ]+", html)
    gh_jid = (parse_qs(u.query).get("gh_jid") or [None])[0]
    board = (re.search(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", html)
             or re.search(r"(?:boards|job-boards|boards-api)\.greenhouse\.io/(?:v1/boards/)?([\w-]+)/jobs", html))
    if gh:
        return _greenhouse(d, urlparse(htmllib.unescape(gh.group(0))), network)
    if gh_jid and board:
        return _greenhouse(d, urlparse(f"https://job-boards.greenhouse.io/embed/job_app?for={board.group(1)}&token={gh_jid}"),
                           network)
    # Embedded Ashby board (company careers site with ?ashby_jid=...).
    ashby_jid = (parse_qs(u.query).get("ashby_jid") or [None])[0]
    ashby_board = re.search(r"jobs\.ashbyhq\.com/([\w.%-]+)", html)
    if ashby_jid and ashby_board and re.fullmatch(UUID, ashby_jid, re.I):
        return _ashby(d, urlparse(f"https://jobs.ashbyhq.com/{ashby_board.group(1)}/{ashby_jid}"), network)
    for host_re, handler in ((rf"https?://jobs(?:\.eu)?\.lever\.co/[\w.-]+/{UUID}", _lever),
                             (rf"https?://jobs\.ashbyhq\.com/[\w.%-]+/{UUID}", _ashby)):
        m = re.search(host_re, html, re.I)
        if m:
            return handler(d, urlparse(m.group(0)), network)
    # A Workable job linked (or its form framed) from the company's own page. Several jobs make it a list.
    workable_jobs = {m.group("code").upper(): m.group(0) for m in WORKABLE_LINK.finditer(html)
                     if (m.group("sub") or "").lower() not in WORKABLE_SITE}
    if len(workable_jobs) == 1:
        return _workable(d, urlparse(next(iter(workable_jobs.values()))), network)
    workable_list = bool(workable_jobs) or bool(WORKABLE_EMBED.search(html))
    m = re.search(r"https?://[\w.-]+\.icims\.com/jobs/\d+[^\"'<> ]*", html)
    if m:
        return _icims(d, urlparse(htmllib.unescape(m.group(0))))
    m = re.search(r"https?://[\w.-]+\.(?:myworkdayjobs|myworkdaysite)\.com/[^\"'<> ]*/job/[^\"'<> ]*", html)
    if m:
        return _workday(d, urlparse(htmllib.unescape(m.group(0))))

    posting = _json_ld_posting(html)
    if posting:
        d.kind, d.handler = "job", "ai"
        d.ats = d.ats or "custom"
        d.role = posting.get("title")
        org = posting.get("hiringOrganization")
        d.company = d.company or (org.get("name") if isinstance(org, dict) else org) or _host_company(u.netloc)
        d.posting = _strip_html(posting.get("description", ""))
        return d

    text = _strip_html(html)
    words = len(text.split())
    if words < 80:
        # Probably a JavaScript app shell; the HTML alone cannot tell.
        if workable_list:   # the company's Workable job list, drawn by Workable's widget
            return _not_job(d, f"The page shows {WORKABLE_BOARD}")
        if d.ats and JOB_PATH.search(d.url):
            d.kind, d.handler = "job", "ai"
            return d
        d.reason = "Page content is rendered by JavaScript; check it in the browser."
        return d
    jobish = len(re.findall(r"\b(responsibilities|qualifications|requirements|what you.ll do|about the role|"
                            r"apply now|apply for this job|submit (your )?application|job description|"
                            r"equal opportunity employer|benefits)\b", text, re.I))
    if jobish >= 2 and (JOB_PATH.search(d.url) or re.search(r"\bapply\b", text, re.I)):
        d.kind, d.handler = "job", "ai"
        d.ats = d.ats or "custom"
        d.company = d.company or _host_company(u.netloc)
        title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        d.role = _strip_html(title.group(1))[:120] if title else None
        d.posting = text[:20000]
        return d
    if workable_list:
        return _not_job(d, f"The page shows {WORKABLE_BOARD}")
    return _not_job(d, "No job posting found on this page (no job description, no application form).")


def _json_ld_posting(html: str) -> dict | None:
    for block in re.findall(r"(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                types = item.get("@type")
                if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
                    return item
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)
    return None


def _host_company(netloc: str) -> str:
    parts = [p for p in netloc.lower().split(".") if p not in ("www", "careers", "jobs", "job", "apply", "boards")]
    core = parts[-2] if len(parts) >= 2 else (parts[0] if parts else netloc)
    return _titleize(core)


# --------------------------------------------------------------------------- entry point

def detect(url: str, *, network: bool = True, html: str | None = None) -> Detection:
    """Classify ``url``. ``html`` may be passed in (tests, or a page the agent already has)."""
    url = (url or "").strip()
    d = Detection(url=url)
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc or "." not in u.netloc:
        return _not_job(d, "Not a web URL. Pass the job posting link, e.g. https://jobs.lever.co/acme/<id>.")
    host = u.netloc.lower().split(":")[0]

    if _host_matches(host, r"^(boards|job-boards)(\.eu)?\.greenhouse\.io$"):
        return _greenhouse(d, u, network)
    if _host_matches(host, r"^jobs(\.eu)?\.lever\.co$"):
        return _lever(d, u, network)
    if _host_matches(host, r"^jobs\.ashbyhq\.com$"):
        return _ashby(d, u, network)
    if host.endswith(".icims.com"):
        return _icims(d, u)
    if host.endswith(".workable.com") and host.split(".")[0] not in WORKABLE_SITE:
        return _workable(d, u, network)
    if re.search(r"(myworkdayjobs|myworkdaysite)\.com$|(^|\.)workday\.com$", host):
        if "/job/" in u.path or re.search(r"/details/", u.path):
            return _workday(d, u)
        return _not_job(d, "Workday search or landing page, not a single job.")
    for name, (host_re, path_re) in AGGREGATORS.items():
        if _host_matches(host, host_re):
            if re.search(path_re, u.path + "?" + u.query):
                d.kind, d.ats, d.handler = "job", name, "follow"
                d.reason = (f"{name.title()} is a job board. Open it, find the employer's own apply link "
                            "(not Easy Apply), and run `apply start` on that link.")
                return d
            return _not_job(d, f"{name.title()} page, but not a single job posting.")
    for name, host_re in OTHER_ATS.items():
        if _host_matches(host, host_re):
            d.ats = name
            break
    for name, (host_re, company) in CUSTOM_PORTALS.items():
        if _host_matches(host, host_re):
            if name == "google" and host.endswith("google.com") and "/careers" not in u.path and host != "careers.google.com":
                break
            d.ats, d.company = name, company
            break

    if html is None and network:
        try:
            html = fetch(url)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return _not_job(d, f"The page does not exist (HTTP {e.code}); the posting may have been removed.")
            d.reason = f"Could not fetch the page (HTTP {e.code}); check it in the browser."
            if d.ats and JOB_PATH.search(url):
                d.kind, d.handler = "job", "ai"
            return d
        except Exception as e:
            d.reason = f"Could not fetch the page ({str(e)[:120]}); check it in the browser."
            if d.ats and JOB_PATH.search(url):
                d.kind, d.handler = "job", "ai"
            return d
    if html is None:
        if d.ats and JOB_PATH.search(url):
            d.kind, d.handler = "job", "ai"
        return d
    return _from_html(d, html, network)
