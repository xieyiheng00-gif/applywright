"""Read/write access to the candidate profile, the question bank and the answer log."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from .catalog import RECORD_KINDS, choice_set
from .db import now
from .text import ONGOING, classify_option, company_key, iso_date, normalize

TRUE_WORDS = {"yes", "y", "true", "1", "on"}
FALSE_WORDS = {"no", "n", "false", "0", "off"}

# Record fields holding dates; stored as 2025 / 2025-09 / 2025-09-01 whatever format they arrive in.
DATE_FIELDS = {"start_date", "end_date", "date", "expires"}
DATE_HINT = "Use any common form, e.g. 2025-09, Sep 2025, 9/2025, 2025/9 or 09/01/2025."

# Records the setup flow asks for: kind -> setup level.
RECORD_SETUP = {"education": "required", "language": "required", "experience": "recommended",
                "project": "recommended", "skill": "recommended", "story": "recommended",
                "certification": "optional", "award": "optional", "publication": "optional",
                "reference": "optional"}


class ValidationError(ValueError):
    pass


def _loads(value):
    return None if value is None else json.loads(value)


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._defs: dict[str, dict] | None = None

    # ------------------------------------------------------------------ fact schema
    def fact_defs(self) -> dict[str, dict]:
        if self._defs is None:
            self._defs = {}
            for r in self.conn.execute("SELECT * FROM fact_defs ORDER BY tier, category, key"):
                d = dict(r)
                d["multi"] = bool(d["multi"])
                d["sensitive"] = bool(d["sensitive"])
                self._defs[d["key"]] = d
        return self._defs

    def define_fact(self, key: str, tier: int, type_: str, label: str, prompt: str, *, choices=None,
                    multi=False, sensitive=False, review_days=None, setup="optional", notes=None) -> dict:
        if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", key):
            raise ValidationError(f"fact key must look like 'category.name', got {key!r}")
        if tier not in (1, 2):
            raise ValidationError("facts are tier 1 (static) or tier 2 (evolving); tier 3 is never stored")
        if type_ not in {"string", "text", "bool", "enum", "number", "date", "list", "url", "email", "phone", "path"}:
            raise ValidationError(f"unknown fact type {type_!r}")
        if type_ == "bool" and choices is None:
            choices = "bool"
        if type_ == "enum":
            choice_set(choices)  # raises KeyError for an unknown set
        with self.conn:
            self.conn.execute(
                """INSERT INTO fact_defs (key, tier, category, type, label, prompt, setup, choices, multi, sensitive,
                                          review_days, notes, origin)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'learned')""",
                (key, tier, key.split(".")[0], type_, label, prompt, setup, choices, int(multi), int(sensitive),
                 review_days, notes))
        self._defs = None
        return self.fact_defs()[key]

    # ------------------------------------------------------------------ facts
    def facts(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.conn.execute("SELECT key, value FROM facts")}

    def fact_rows(self) -> dict[str, dict]:
        return {r["key"]: {"value": json.loads(r["value"]), "source": r["source"], "updated_at": r["updated_at"]}
                for r in self.conn.execute("SELECT * FROM facts")}

    def get_fact(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def coerce(self, key: str, value):
        d = self.fact_defs().get(key)
        if d is None:
            raise ValidationError(f"unknown fact {key!r}; define it first with `jobfill fact define`")
        if isinstance(value, str) and value.strip().startswith(("[", "{")) and d["type"] in ("list", "enum"):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        t = d["type"]
        if t == "bool":
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in TRUE_WORDS:
                return True
            if s in FALSE_WORDS:
                return False
            raise ValidationError(f"{key} expects yes/no, got {value!r}")
        if t == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            s = str(value).strip().lower().replace(",", "").replace("$", "").replace(" ", "")
            mult = 1000 if s.endswith("k") else 1
            s = s.rstrip("k")
            try:
                n = float(s) * mult
            except ValueError:
                raise ValidationError(f"{key} expects a number, got {value!r}") from None
            return int(n) if n == int(n) else n
        if t == "date":
            iso = iso_date(value)
            if iso is None:
                raise ValidationError(f"{key}: cannot read {value!r} as a date. {DATE_HINT}")
            return iso
        if t == "list":
            if isinstance(value, str):
                value = [v for v in (p.strip() for p in value.split(",")) if v]
            if not isinstance(value, list):
                raise ValidationError(f"{key} expects a list")
            return [str(v).strip() for v in value if str(v).strip()]
        if t == "enum":
            values = value if isinstance(value, list) else (
                [p.strip() for p in value.split(",")] if d["multi"] and isinstance(value, str) else [value])
            out = [self._canonical_choice(key, d["choices"], v) for v in values if str(v).strip()]
            if not out:
                raise ValidationError(f"{key} needs a value")
            if not d["multi"] and len(out) > 1:
                raise ValidationError(f"{key} takes a single value")
            return out if d["multi"] else out[0]
        s = str(value).strip()
        if t == "email" and "@" not in s:
            raise ValidationError(f"{key} does not look like an email address: {value!r}")
        return s

    @staticmethod
    def _canonical_choice(key, set_name, value):
        cs = choice_set(set_name)
        v = str(value).strip()
        if v in cs["_values"]:
            return v
        scores = classify_option(normalize(v), cs)
        if scores:
            return max(scores, key=scores.get)
        raise ValidationError(f"{key}: {value!r} is not one of {sorted(cs['_values'])}")

    def set_fact(self, key: str, value, source: str = "user") -> object:
        value = self.coerce(key, value)
        old = self.conn.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        new_json = json.dumps(value, ensure_ascii=False)
        if old and old["value"] == new_json:
            return value
        ts = now()
        with self.conn:
            self.conn.execute(
                """INSERT INTO facts (key, value, source, updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source,
                   updated_at=excluded.updated_at""", (key, new_json, source, ts))
            self.conn.execute(
                "INSERT INTO fact_history (key, old_value, new_value, source, changed_at) VALUES (?,?,?,?,?)",
                (key, old["value"] if old else None, new_json, source, ts))
        return value

    def confirm_fact(self, key: str, source: str = "user") -> bool:
        """Candidate says a stored value is still right: restart its review clock."""
        row = self.conn.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        if not row:
            return False
        ts = now()
        with self.conn:
            self.conn.execute("UPDATE facts SET updated_at=? WHERE key=?", (ts, key))
            self.conn.execute(
                "INSERT INTO fact_history (key, old_value, new_value, source, changed_at) VALUES (?,?,?,?,?)",
                (key, row["value"], row["value"], f"{source}:confirmed", ts))
        return True

    def unset_fact(self, key: str, source: str = "user") -> bool:
        old = self.conn.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        if not old:
            return False
        with self.conn:
            self.conn.execute("DELETE FROM facts WHERE key=?", (key,))
            self.conn.execute(
                "INSERT INTO fact_history (key, old_value, new_value, source, changed_at) VALUES (?,?,NULL,?,?)",
                (key, old["value"], source, now()))
        return True

    # ------------------------------------------------------------------ records
    def records(self, kind: str) -> list[dict]:
        rows = [dict(json.loads(r["data"]), _id=r["id"], _sort=r["sort_order"])
                for r in self.conn.execute("SELECT id, data, sort_order FROM records WHERE kind=?", (kind,))]
        return sorted(rows, key=_record_order)

    def all_records(self) -> dict[str, list[dict]]:
        kinds = [r["kind"] for r in self.conn.execute("SELECT DISTINCT kind FROM records")]
        return {k: self.records(k) for k in sorted(kinds)}

    def get_record(self, record_id: int) -> tuple[str, dict] | None:
        r = self.conn.execute("SELECT kind, data FROM records WHERE id=?", (record_id,)).fetchone()
        return (r["kind"], json.loads(r["data"])) if r else None

    @staticmethod
    def _clean_record(kind: str, data: dict) -> dict:
        if kind not in RECORD_KINDS:
            raise ValidationError(f"unknown record kind {kind!r}; known: {sorted(RECORD_KINDS)}")
        if not isinstance(data, dict):
            raise ValidationError("record data must be a JSON object")
        out = {}
        for k, v in data.items():
            if k.startswith("_") or v is None or v == "" or v == []:
                continue
            if k in DATE_FIELDS:
                if k == "end_date" and ONGOING.match(str(v)):
                    # "Present": the job is current / the degree is still in progress.
                    out["current" if kind == "experience" else "expected"] = True
                    continue
                iso = iso_date(v)
                if iso is None:
                    raise ValidationError(f"{kind}.{k}: cannot read {v!r} as a date. {DATE_HINT}")
                v = iso
            if k in ("current", "expected", "is_coursework", "may_contact") and not isinstance(v, bool):
                v = str(v).strip().lower() in TRUE_WORDS
            if k in ("years", "years_known") and isinstance(v, str):
                try:
                    v = float(v)
                    v = int(v) if v == int(v) else v
                except ValueError:
                    pass
            out[k] = v
        missing = [f for f in RECORD_KINDS[kind]["key"][:1] if f not in out]
        if missing:
            raise ValidationError(f"{kind} record needs {missing}")
        return out

    def add_record(self, kind: str, data: dict, source: str = "user", sort_order: int | None = None) -> int:
        data = self._clean_record(kind, data)
        ts = now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO records (kind, data, sort_order, source, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (kind, json.dumps(data, ensure_ascii=False), sort_order, source, ts, ts))
        return cur.lastrowid

    def update_record(self, record_id: int, data: dict, *, merge: bool = True, source: str = "user") -> dict:
        found = self.get_record(record_id)
        if not found:
            raise ValidationError(f"no record with id {record_id}")
        kind, current = found
        new = {**current, **data} if merge else data
        new = {k: v for k, v in new.items() if v is not None}
        new = self._clean_record(kind, new)
        with self.conn:
            self.conn.execute("UPDATE records SET data=?, source=?, updated_at=? WHERE id=?",
                              (json.dumps(new, ensure_ascii=False), source, now(), record_id))
        return new

    def remove_record(self, record_id: int) -> bool:
        with self.conn:
            return self.conn.execute("DELETE FROM records WHERE id=?", (record_id,)).rowcount > 0

    def find_record(self, kind: str, data: dict) -> dict | None:
        key_fields = RECORD_KINDS[kind]["key"]
        want = tuple(normalize(str(data.get(f, ""))) for f in key_fields)
        if not want[0]:
            return None
        for r in self.records(kind):
            have = tuple(normalize(str(r.get(f, ""))) for f in key_fields)
            # Fields missing on either side do not block a match ("same school, degree unknown").
            if all(w == h or not w or not h for w, h in zip(want, have)) and want[0] == have[0]:
                return r
        return None

    # ------------------------------------------------------------------ import / merge
    def import_patch(self, patch: dict, *, dry_run: bool = True, replace: set[str] | None = None,
                     replace_all: bool = False) -> dict:
        """Merge ``{"facts": {...}, "records": {kind: [...]}}`` into the profile.

        New information is additive and applied. Anything that disagrees with a
        stored value is a conflict and is only applied when its id is listed in
        ``replace`` (or ``replace_all``). Re-running the same patch is a no-op.
        """
        replace = replace or set()
        source = patch.get("source", "import")
        report = {"additive": [], "conflicts": [], "unchanged": 0, "errors": [], "applied": not dry_run}
        current = self.facts()
        for key, raw in (patch.get("facts") or {}).items():
            try:
                value = self.coerce(key, raw)
            except ValidationError as e:
                report["errors"].append({"id": f"fact:{key}", "error": str(e)})
                continue
            cid = f"fact:{key}"
            if key not in current:
                report["additive"].append({"id": cid, "new": value})
                if not dry_run:
                    self.set_fact(key, value, source)
            elif current[key] == value:
                report["unchanged"] += 1
            else:
                report["conflicts"].append({"id": cid, "current": current[key], "proposed": value})
                if not dry_run and (replace_all or cid in replace):
                    self.set_fact(key, value, source)
        for kind, items in (patch.get("records") or {}).items():
            for item in items or []:
                try:
                    clean = self._clean_record(kind, item)
                except ValidationError as e:
                    report["errors"].append({"id": f"record:{kind}", "error": str(e)})
                    continue
                existing = self.find_record(kind, clean)
                label = clean.get(RECORD_KINDS[kind]["key"][0])
                if existing is None:
                    report["additive"].append({"id": f"record:{kind}:new", "label": label, "new": clean})
                    if not dry_run:
                        self.add_record(kind, clean, source)
                    continue
                rid = existing["_id"]
                adds, changes = {}, {}
                for f, v in clean.items():
                    if f not in existing:
                        adds[f] = v
                    elif existing[f] != v:
                        changes[f] = v
                if not adds and not changes:
                    report["unchanged"] += 1
                    continue
                if adds:
                    report["additive"].append({"id": f"record:{kind}#{rid}", "label": label, "new_fields": adds})
                accepted = {}
                for f, v in changes.items():
                    cid = f"record:{kind}#{rid}.{f}"
                    report["conflicts"].append({"id": cid, "label": label, "current": existing[f], "proposed": v})
                    if replace_all or cid in replace:
                        accepted[f] = v
                if not dry_run and (adds or accepted):
                    self.update_record(rid, {**adds, **accepted}, source=source)
        return report

    def export(self, tier: int | None = None) -> dict:
        defs = self.fact_defs()
        rows = self.fact_rows()
        facts = {k: v["value"] for k, v in sorted(rows.items())
                 if tier is None or defs.get(k, {}).get("tier") == tier}
        records = {kind: [{k: v for k, v in r.items() if not k.startswith("_")} for r in items]
                   for kind, items in self.all_records().items()
                   if tier is None or RECORD_KINDS.get(kind, {}).get("tier") == tier}
        return {"facts": facts, "records": records}

    # ------------------------------------------------------------------ setup progress
    def missing(self, tier: int | None = None, level: str = "recommended") -> dict:
        levels = {"required": ["required"], "recommended": ["required", "recommended"],
                  "optional": ["required", "recommended", "optional"]}[level]
        have = self.facts()
        facts = [{"key": d["key"], "tier": d["tier"], "category": d["category"], "label": d["label"],
                  "prompt": d["prompt"], "setup": d["setup"], "type": d["type"],
                  "choices": sorted(choice_set(d["choices"])["_values"]) if d["type"] == "enum" else None,
                  "multi": d["multi"], "sensitive": d["sensitive"]}
                 for d in self.fact_defs().values()
                 if d["key"] not in have and d["setup"] in levels and (tier is None or d["tier"] == tier)]
        counts = dict(self.conn.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind").fetchall())
        records = [{"kind": k, "setup": lvl, "tier": RECORD_KINDS[k]["tier"], "fields": RECORD_KINDS[k]["fields"]}
                   for k, lvl in RECORD_SETUP.items()
                   if not counts.get(k) and lvl in levels and (tier is None or RECORD_KINDS[k]["tier"] == tier)]
        return {"facts": facts, "records": records}

    def stale(self) -> list[dict]:
        out = []
        today = datetime.now(timezone.utc)
        defs = self.fact_defs()
        for key, row in self.fact_rows().items():
            days = defs.get(key, {}).get("review_days")
            if not days:
                continue
            age = (today - datetime.fromisoformat(row["updated_at"])).days
            if age >= days:
                out.append({"key": key, "label": defs[key]["label"], "value": row["value"], "age_days": age,
                            "review_days": days})
        return out

    def status(self) -> dict:
        have = self.facts()
        by_tier = {}
        for d in self.fact_defs().values():
            t = by_tier.setdefault(d["tier"], {"required": [0, 0], "recommended": [0, 0], "optional": [0, 0]})
            t[d["setup"]][1] += 1
            if d["key"] in have:
                t[d["setup"]][0] += 1
        counts = dict(self.conn.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind").fetchall())
        q = dict(self.conn.execute("SELECT origin, COUNT(*) FROM questions GROUP BY origin").fetchall())
        qt = dict(self.conn.execute("SELECT tier, COUNT(*) FROM questions GROUP BY tier").fetchall())
        aliases = dict(self.conn.execute("SELECT origin, COUNT(*) FROM question_aliases GROUP BY origin").fetchall())
        missing_required = self.missing(level="required")
        return {
            "setup_complete": not missing_required["facts"] and not missing_required["records"],
            "facts_by_tier": {str(k): {lvl: f"{a}/{b}" for lvl, (a, b) in v.items()} for k, v in sorted(by_tier.items())},
            "records": counts,
            "missing_required": [f["key"] for f in missing_required["facts"]]
                                + [f"record:{r['kind']}" for r in missing_required["records"]],
            "stale": [s["key"] for s in self.stale()],
            "pending_questions": self.conn.execute(
                "SELECT COUNT(*) FROM pending_questions WHERE status='open'").fetchone()[0],
            "question_bank": {"by_tier": {str(k): v for k, v in sorted(qt.items())}, "builtin": q.get("builtin", 0),
                              "learned": q.get("learned", 0), "aliases_builtin": aliases.get("builtin", 0),
                              "aliases_learned": aliases.get("learned", 0)},
            "applications": self.conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
        }

    # ------------------------------------------------------------------ question bank
    def questions(self) -> dict[str, dict]:
        out = {}
        for r in self.conn.execute("SELECT * FROM questions"):
            d = dict(r)
            d["answer"] = json.loads(d["answer"])
            d["kinds"] = _loads(d["kinds"])
            d["require"] = json.loads(d["require"])
            d["exclude"] = json.loads(d["exclude"])
            d["global_ok"] = bool(d["global_ok"])
            out[d["id"]] = d
        return out

    def aliases(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT id, question_id, text, normalized, is_pattern, priority, origin FROM question_aliases")]

    def add_alias(self, question_id: str, text: str, *, is_pattern: bool = False, priority: int = 0,
                  origin: str = "learned") -> bool:
        if question_id not in self.questions():
            raise ValidationError(f"unknown question {question_id!r}")
        norm = text if is_pattern else normalize(text)
        if is_pattern:
            re.compile(norm)
        if not norm:
            raise ValidationError("alias is empty after normalization")
        with self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO question_aliases (question_id, text, normalized, is_pattern, priority, origin,
                                                           created_at) VALUES (?,?,?,?,?,?,?)""",
                (question_id, text, norm, int(is_pattern), priority, origin, now()))
        return cur.rowcount > 0

    def add_question(self, qid: str, tier: int, category: str, text: str, answer: dict, *, section=None,
                     kinds=None, choices=None, require=(), exclude=(), aliases=()) -> dict:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", qid):
            raise ValidationError("question id must be snake_case")
        if tier == 3 and "ai" not in answer:
            raise ValidationError("tier-3 questions must use an ai answer")
        if "ai" in answer and tier != 3:
            raise ValidationError("ai answers are tier 3")
        if "fact" in answer and answer["fact"] not in self.fact_defs():
            raise ValidationError(f"unknown fact {answer['fact']!r}; define it first")
        if "ai" in answer and kinds is None:
            kinds = ["text", "textarea"]
        with self.conn:
            self.conn.execute(
                """INSERT INTO questions (id, tier, category, text, answer, section, global_ok, kinds, choices, require,
                                          exclude, origin, created_at) VALUES (?,?,?,?,?,?,0,?,?,?,?, 'learned', ?)""",
                (qid, tier, category, text, json.dumps(answer), section, json.dumps(kinds) if kinds else None,
                 choices, json.dumps(list(require)), json.dumps(list(exclude)), now()))
        for a in [text, *aliases]:
            self.add_alias(qid, a)
        return self.questions()[qid]

    # ------------------------------------------------------------------ pending (unanswered) questions
    def add_pending(self, label: str, normalized: str, *, field_type=None, options=None, candidates=None,
                    company=None, url=None) -> int:
        ts = now()
        with self.conn:
            self.conn.execute(
                """INSERT INTO pending_questions (label, normalized, field_type, options, candidates, company, url,
                                                  first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(normalized) DO UPDATE SET seen_count = seen_count + 1, last_seen = excluded.last_seen,
                     options = COALESCE(excluded.options, options), candidates = excluded.candidates""",
                (label, normalized, field_type, json.dumps(options, ensure_ascii=False) if options else None,
                 json.dumps(candidates) if candidates else None, company, url, ts, ts))
        return self.conn.execute("SELECT id FROM pending_questions WHERE normalized=?", (normalized,)).fetchone()[0]

    def pending(self, status: str | None = "open") -> list[dict]:
        sql = "SELECT * FROM pending_questions" + (" WHERE status=?" if status else "") + " ORDER BY seen_count DESC, id"
        rows = self.conn.execute(sql, (status,) if status else ())
        return [dict(r, options=_loads(r["options"]), candidates=_loads(r["candidates"])) for r in rows]

    def get_pending(self, pending_id: int) -> dict:
        r = self.conn.execute("SELECT * FROM pending_questions WHERE id=?", (pending_id,)).fetchone()
        if not r:
            raise ValidationError(f"no pending question {pending_id}")
        return dict(r, options=_loads(r["options"]), candidates=_loads(r["candidates"]))

    def close_pending(self, pending_id: int, status: str, resolution: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE pending_questions SET status=?, resolution=? WHERE id=?",
                              (status, resolution, pending_id))

    # ------------------------------------------------------------------ portal accounts (no passwords)
    def account(self, host: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM accounts WHERE host=?", (host.lower(),)).fetchone()
        return dict(r) if r else None

    def accounts(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM accounts ORDER BY updated_at DESC")]

    def upsert_account(self, host: str, company_key: str, email: str, status: str) -> dict:
        ts = now()
        with self.conn:
            self.conn.execute(
                """INSERT INTO accounts (host, company_key, email, status, created_at, updated_at) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(host) DO UPDATE SET email=excluded.email, status=excluded.status,
                     updated_at=excluded.updated_at""", (host.lower(), company_key, email, status, ts, ts))
        return self.account(host)

    def set_account_status(self, host: str, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE accounts SET status=?, updated_at=? WHERE host=?", (status, now(), host.lower()))

    def rotate_account(self, host: str) -> dict:
        """After resetting the password on the site: move to the next derived password."""
        with self.conn:
            n = self.conn.execute("UPDATE accounts SET version = version + 1, updated_at=? WHERE host=?",
                                  (now(), host.lower())).rowcount
        if not n:
            raise ValidationError(f"no account for {host}")
        return self.account(host)

    def forget_account(self, host: str) -> bool:
        with self.conn:
            return self.conn.execute("DELETE FROM accounts WHERE host=?", (host.lower(),)).rowcount > 0

    # ------------------------------------------------------------------ applications & answer log
    def start_application(self, company: str, role: str | None = None, **extra) -> int:
        cols = {k: v for k, v in extra.items()
                if k in {"url", "ats", "country", "source", "referrer", "posting", "notes", "resume"} and v}
        ts = now()
        names = ["company", "role", *cols, "created_at", "updated_at"]
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO applications ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                (company, role, *cols.values(), ts, ts))
        return cur.lastrowid

    def application(self, app_id: int) -> dict:
        r = self.conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        if not r:
            raise ValidationError(f"no application {app_id}")
        return dict(r)

    def applications(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT id, company, role, status, ats, url, created_at FROM applications ORDER BY id DESC")]

    def update_application(self, app_id: int, **fields) -> dict:
        allowed = {"role", "url", "ats", "country", "source", "referrer", "posting", "notes", "status", "resume"}
        fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if fields:
            with self.conn:
                self.conn.execute(
                    f"UPDATE applications SET {', '.join(f'{k}=?' for k in fields)}, updated_at=? WHERE id=?",
                    (*fields.values(), now(), app_id))
        return self.application(app_id)

    def same_company_apps(self, company: str, exclude_id: int | None = None) -> list[dict]:
        target = company_key(company)
        return [a for a in (dict(r) for r in self.conn.execute("SELECT * FROM applications"))
                if company_key(a["company"]) == target and a["id"] != exclude_id]

    @staticmethod
    def answer_key(label: str, section: str | None = None, index: int | None = None) -> str:
        """Stable key for a question on one form: its wording, plus the block index if repeated."""
        key = normalize(label)[:200] or "unlabeled"
        return f"{key}#{index or 0}" if section else key

    def record_answer(self, app_id: int, field_key: str, label: str, value, *, question_id=None,
                      source="python", tier=None, status="filled", fill=None, section=None, idx=None) -> None:
        """Insert or update one question of an application's result record."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO answers (application_id, field_key, question_id, label, value, source, tier, created_at,
                                        status, fill, section, idx)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(application_id, field_key) DO UPDATE SET question_id=excluded.question_id,
                     label=excluded.label, value=excluded.value, source=excluded.source, tier=excluded.tier,
                     created_at=excluded.created_at, status=excluded.status,
                     fill=COALESCE(excluded.fill, answers.fill), section=excluded.section, idx=excluded.idx
                   -- never let a later re-read wipe an answer the AI or the candidate gave
                   WHERE NOT (answers.status = 'filled' AND answers.source IN ('ai', 'user')
                              AND excluded.status != 'filled')""",
                (app_id, field_key, question_id, label, json.dumps(value, ensure_ascii=False), source, tier, now(),
                 status, fill, section, idx))

    def fill_attempts(self, app_id: int, field_key: str, value) -> int:
        """How many times in a row typing ``value`` into this field has failed (0 for a new value)."""
        r = self.conn.execute("SELECT attempts, attempt_value FROM answers WHERE application_id=? AND field_key=?",
                              (app_id, field_key)).fetchone()
        return r["attempts"] if r and r["attempt_value"] == json.dumps(value, ensure_ascii=False) else 0

    def note_fill(self, app_id: int, field_key: str, fill: str, value=None) -> int:
        """Record what happened when jobfill typed ``value``; returns consecutive failures for that value.

        A failure counts against the value it was for, so a corrected value starts again from zero. A
        later pass that only *kept* the value does not hide that jobfill typed it earlier.
        """
        v = json.dumps(value, ensure_ascii=False)
        attempts = self.fill_attempts(app_id, field_key, value)
        with self.conn:
            if fill.startswith("failed"):
                attempts += 1
                self.conn.execute("UPDATE answers SET fill=?, attempts=?, attempt_value=? WHERE application_id=? "
                                  "AND field_key=?", (fill, attempts, v, app_id, field_key))
            elif fill == "ok":
                attempts = 0
                self.conn.execute("UPDATE answers SET fill='ok', attempts=0, attempt_value=NULL WHERE application_id=? "
                                  "AND field_key=?", (app_id, field_key))
            else:
                self.conn.execute("UPDATE answers SET fill=? WHERE application_id=? AND field_key=? "
                                  "AND NOT (? LIKE 'kept%' AND fill = 'ok')", (fill, app_id, field_key, fill))
        return attempts

    # ------------------------------------------------------------------ learned choices
    def remember_choice(self, question_id: str, value, choice, company: str | None = None) -> None:
        """The candidate picked ``choice`` where no option was exactly the stored ``value``."""
        if not question_id or value in (None, "") or choice in (None, "", []):
            return
        with self.conn:
            self.conn.execute(
                "INSERT INTO learned_choices (question_id, value, choice, company, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(question_id, value, choice) DO UPDATE SET company=excluded.company, "
                "updated_at=excluded.updated_at",
                (question_id, normalize(str(value)), str(choice), company, now()))

    def learned_choices(self, question_id: str | None, value) -> list[str]:
        """Choices picked before for this stored answer, the latest first."""
        if not question_id or value in (None, ""):
            return []
        return [r["choice"] for r in self.conn.execute(
            "SELECT choice FROM learned_choices WHERE question_id=? AND value=? ORDER BY updated_at DESC, rowid DESC",
            (question_id, normalize(str(value))))]

    def all_learned_choices(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT question_id, value, choice, company, updated_at FROM learned_choices ORDER BY question_id, value")]

    def forget_choices(self, question_id: str, value=None, choice: str | None = None) -> int:
        sql, args = "DELETE FROM learned_choices WHERE question_id=?", [question_id]
        if value is not None:
            sql, args = sql + " AND value=?", args + [normalize(str(value))]
        if choice is not None:
            sql, args = sql + " AND choice=?", args + [choice]
        with self.conn:
            return self.conn.execute(sql, args).rowcount

    def answers(self, app_id: int) -> list[dict]:
        return [dict(r, value=json.loads(r["value"])) for r in self.conn.execute(
            "SELECT field_key, question_id, label, value, source, tier, status, fill, section, idx, attempts, created_at "
            "FROM answers WHERE application_id=? ORDER BY id", (app_id,))]

    def written_answers(self, exclude_app_id: int | None = None) -> list[dict]:
        """Tier-3 answers written for applications (by the agent or the candidate), with each one's company."""
        return [dict(r, value=json.loads(r["value"])) for r in self.conn.execute(
            """SELECT a.application_id, a.question_id, a.value, p.company FROM answers a
               JOIN applications p ON p.id = a.application_id
               WHERE a.tier = 3 AND a.source IN ('ai', 'user') AND a.status = 'filled' AND a.application_id != ?""",
            (exclude_app_id or 0,))]

    def prior_answer(self, question_id: str, scope: str, app: dict | None, label: str | None = None) -> dict | None:
        """Earlier tier-3 answer that may be reused under the question's scope rule.

        ``label`` restricts reuse to answers given for the same wording (catch-all questions).
        """
        if not app:
            return None
        if scope == "application":
            app_ids = [app["id"]]
        else:  # company
            app_ids = [app["id"], *(a["id"] for a in self.same_company_apps(app["company"], exclude_id=app["id"]))]
        marks = ",".join("?" * len(app_ids))
        rows = self.conn.execute(
            f"""SELECT a.value, a.label, a.application_id, a.source FROM answers a
                WHERE a.question_id=? AND a.source IN ('ai', 'user') AND a.status='filled'
                  AND a.application_id IN ({marks})
                ORDER BY a.created_at DESC, a.id DESC""", (question_id, *app_ids)).fetchall()
        for r in rows:
            if label is None or normalize(r["label"]) == normalize(label):
                return {"value": json.loads(r["value"]), "application_id": r["application_id"], "source": r["source"]}
        return None


def _record_order(r: dict):
    end = r.get("end_date") or ""
    ongoing = bool(r.get("current") or r.get("expected")) or (not end and r.get("start_date"))
    sort = r["_sort"] if r.get("_sort") is not None else 10**6
    # Most recent first: ongoing, then latest end date, then latest start date.
    return (sort, not ongoing, _neg(end), _neg(r.get("start_date") or ""), -r["_id"])


def _neg(s: str) -> str:
    # Invert digits so ascending sort yields descending dates.
    return "".join(chr(ord("9") - ord(c) + ord("0")) if c.isdigit() else c for c in s) or "~"
