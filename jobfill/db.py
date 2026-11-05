"""SQLite schema, connection and catalog seeding.

The database file holds personal data and is gitignored. Its default location
is ``<repo>/data/jobfill.db``; override with ``--db`` or ``JOBFILL_DB``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .catalog import FACTS, QUESTIONS
from .text import normalize

SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- What can be stored about the candidate (built-in + learned).
CREATE TABLE IF NOT EXISTS fact_defs (
    key          TEXT PRIMARY KEY,
    tier         INTEGER NOT NULL CHECK (tier IN (1, 2)),
    category     TEXT NOT NULL,
    type         TEXT NOT NULL,
    label        TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    setup        TEXT NOT NULL DEFAULT 'optional',
    choices      TEXT,
    multi        INTEGER NOT NULL DEFAULT 0,
    sensitive    INTEGER NOT NULL DEFAULT 0,
    review_days  INTEGER,
    notes        TEXT,
    origin       TEXT NOT NULL DEFAULT 'builtin'
);

-- The candidate's single-valued answers. value is JSON.
CREATE TABLE IF NOT EXISTS facts (
    key         TEXT PRIMARY KEY REFERENCES fact_defs(key),
    value       TEXT NOT NULL,
    source      TEXT,
    updated_at  TEXT NOT NULL
);

-- Every change to a fact, so an update can always be traced or undone.
CREATE TABLE IF NOT EXISTS fact_history (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    source      TEXT,
    changed_at  TEXT NOT NULL
);

-- Repeating data: education, experience, project, skill, language, story ...
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    data        TEXT NOT NULL,
    sort_order  INTEGER,
    source      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_kind ON records(kind);

-- Question types (built-in + learned while applying).
CREATE TABLE IF NOT EXISTS questions (
    id          TEXT PRIMARY KEY,
    tier        INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    category    TEXT NOT NULL,
    text        TEXT NOT NULL,
    answer      TEXT NOT NULL,
    section     TEXT,
    global_ok   INTEGER NOT NULL DEFAULT 0,
    kinds       TEXT,
    choices     TEXT,
    require     TEXT NOT NULL DEFAULT '[]',
    exclude     TEXT NOT NULL DEFAULT '[]',
    origin      TEXT NOT NULL DEFAULT 'builtin',
    created_at  TEXT NOT NULL
);

-- Phrasings of a question. is_pattern=1 rows hold a regex over normalized text.
CREATE TABLE IF NOT EXISTS question_aliases (
    id           INTEGER PRIMARY KEY,
    question_id  TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    normalized   TEXT NOT NULL,
    is_pattern   INTEGER NOT NULL DEFAULT 0,
    priority     INTEGER NOT NULL DEFAULT 0,
    origin       TEXT NOT NULL DEFAULT 'builtin',
    hits         INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    UNIQUE (question_id, normalized, is_pattern)
);
CREATE INDEX IF NOT EXISTS aliases_normalized ON question_aliases(normalized);

CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY,
    company     TEXT NOT NULL,
    role        TEXT,
    url         TEXT,
    ats         TEXT,
    country     TEXT,
    source      TEXT,
    referrer    TEXT,
    posting     TEXT,
    notes       TEXT,
    resume      TEXT,               -- this application's CV (tailored); NULL = documents.resume_path
    status      TEXT NOT NULL DEFAULT 'in_progress',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- One row per question seen on an application's form: the result record of that application.
-- Tier-3 answers live only here. field_key is the normalized label (+ #index in repeated blocks).
CREATE TABLE IF NOT EXISTS answers (
    id              INTEGER PRIMARY KEY,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    field_key       TEXT NOT NULL,
    question_id     TEXT,
    label           TEXT NOT NULL,
    value           TEXT NOT NULL,             -- JSON; null while the question is open
    source          TEXT NOT NULL,             -- fact:/derive:/record:/template (python), ai, user, answer_log:appN
    tier            INTEGER,
    created_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'filled',  -- filled | needs_ai | needs_user | uncertain | unmatched | skipped
    fill            TEXT,                      -- ok | failed: ... | kept ... | NULL (entered by the agent or not typed)
    section         TEXT,
    idx             INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0, -- consecutive failed fills of attempt_value
    attempt_value   TEXT,                       -- JSON of the value those failures were for
    UNIQUE (application_id, field_key)
);

-- One row per jobfill command and turn boundary: `app timing` works out from it who spent what (timing.py).
CREATE TABLE IF NOT EXISTS timing (
    id              INTEGER PRIMARY KEY,
    session         TEXT NOT NULL DEFAULT '',  -- the agent session it ran in
    application_id  INTEGER,                   -- NULL: not about one application
    name            TEXT NOT NULL,             -- "apply fill", "answer record", ...; pause / resume: turn boundaries
    started         REAL NOT NULL,             -- seconds since 1970
    seconds         REAL,                      -- NULL: still running, or it was killed
    detail          TEXT                       -- JSON: how it ended, phases, slowest fields, question, words, ...
);

-- Job-portal accounts jobfill created or signed in to. No passwords: they are derived from
-- secret.env (HMAC-SHA256 of company_key, plus #version after a reset).
CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY,
    host         TEXT NOT NULL UNIQUE,     -- e.g. nvidia.wd5.myworkdayjobs.com
    company_key  TEXT NOT NULL,            -- name the password is derived from
    email        TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL,            -- pending | created | verified | active | failed
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Questions seen in a form that the bank could not answer yet.
-- What the candidate picked when a form had no option exactly matching a stored answer (a school, a field
-- of study): reused on later forms whose list offers the same choice.
CREATE TABLE IF NOT EXISTS learned_choices (
    question_id  TEXT NOT NULL,
    value        TEXT NOT NULL,            -- the stored answer, normalized
    choice       TEXT NOT NULL,            -- the option picked for it, as the form wrote it
    company      TEXT,                     -- where it was picked
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (question_id, value, choice)
);

CREATE TABLE IF NOT EXISTS pending_questions (
    id            INTEGER PRIMARY KEY,
    label         TEXT NOT NULL,
    normalized    TEXT NOT NULL UNIQUE,
    field_type    TEXT,
    options       TEXT,
    candidates    TEXT,
    company       TEXT,
    url           TEXT,
    seen_count    INTEGER NOT NULL DEFAULT 1,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    resolution    TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_db_path() -> Path:
    env = os.environ.get("JOBFILL_DB")
    return Path(env) if env else ROOT / "data" / "jobfill.db"


def connect(path: str | Path | None = None, seed_catalog: bool = True) -> sqlite3.Connection:
    path = Path(path) if path else default_db_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    if seed_catalog and _catalog_version(conn) != _catalog_fingerprint():
        seed(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by older versions up to the current schema."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(answers)")}
    with conn:
        for column, ddl in (("status", "TEXT NOT NULL DEFAULT 'filled'"), ("fill", "TEXT"), ("section", "TEXT"),
                            ("idx", "INTEGER"), ("attempts", "INTEGER NOT NULL DEFAULT 0"), ("attempt_value", "TEXT")):
            if column not in have:
                conn.execute(f"ALTER TABLE answers ADD COLUMN {column} {ddl}")
        if "resume" not in {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}:
            conn.execute("ALTER TABLE applications ADD COLUMN resume TEXT")


def _canonicalize_choice_facts(conn: sqlite3.Connection, ts: str) -> None:
    """A fact that became a choice (pronouns used to be free text) keeps its meaning: 'she/her' -> she_her."""
    from .catalog import choice_set
    from .text import classify_option
    rows = conn.execute("SELECT f.key, f.value, d.choices, d.multi FROM facts f JOIN fact_defs d ON d.key = f.key "
                        "WHERE d.type = 'enum' AND d.choices IS NOT NULL").fetchall()
    for r in rows:
        cs = choice_set(r["choices"])
        value = json.loads(r["value"])
        vals = value if isinstance(value, list) else [value]
        if all(str(v) in cs["_values"] for v in vals):
            continue
        mapped = []
        for v in vals:
            scores = classify_option(normalize(str(v)), cs)
            if not scores:
                break   # cannot tell: leave it for the candidate to fix
            mapped.append(max(scores, key=scores.get))
        else:
            new = json.dumps(mapped if r["multi"] else mapped[0])
            conn.execute("UPDATE facts SET value=? WHERE key=?", (new, r["key"]))
            conn.execute("INSERT INTO fact_history (key, old_value, new_value, source, changed_at) "
                         "VALUES (?,?,?, 'migration', ?)", (r["key"], r["value"], new, ts))


def meta_value(conn: sqlite3.Connection, key: str, create=None) -> str | None:
    """A value kept in the meta table; ``create()`` makes and stores it the first time."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row or create is None:
        return row["value"] if row else None
    value = create()
    with conn:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, value))
    return value


def _catalog_fingerprint() -> str:
    digest = hashlib.sha1(json.dumps([FACTS, QUESTIONS], sort_keys=True, default=str).encode()).hexdigest()
    return f"{SCHEMA_VERSION}:{digest[:16]}"


def _catalog_version(conn) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'catalog'").fetchone()
    return row["value"] if row else None


def seed(conn: sqlite3.Connection) -> dict:
    """Upsert the built-in catalog. Learned rows (origin='learned') are never touched."""
    ts = now()
    with conn:
        for f in FACTS:
            conn.execute(
                """INSERT INTO fact_defs (key, tier, category, type, label, prompt, setup, choices, multi,
                                          sensitive, review_days, notes, origin)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'builtin')
                   ON CONFLICT(key) DO UPDATE SET tier=excluded.tier, category=excluded.category,
                     type=excluded.type, label=excluded.label, prompt=excluded.prompt, setup=excluded.setup,
                     choices=excluded.choices, multi=excluded.multi, sensitive=excluded.sensitive,
                     review_days=excluded.review_days, notes=excluded.notes
                   WHERE fact_defs.origin = 'builtin'""",
                (f["key"], f["tier"], f["category"], f["type"], f["label"], f["prompt"], f["setup"],
                 f["choices"], int(f["multi"]), int(f["sensitive"]), f["review_days"], f["notes"]))
        for q in QUESTIONS:
            conn.execute(
                """INSERT INTO questions (id, tier, category, text, answer, section, global_ok, kinds, choices,
                                          require, exclude, origin, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, 'builtin', ?)
                   ON CONFLICT(id) DO UPDATE SET tier=excluded.tier, category=excluded.category,
                     text=excluded.text, answer=excluded.answer, section=excluded.section,
                     global_ok=excluded.global_ok, kinds=excluded.kinds, choices=excluded.choices,
                     require=excluded.require, exclude=excluded.exclude
                   WHERE questions.origin = 'builtin'""",
                (q["id"], q["tier"], q["category"], q["text"], json.dumps(q["answer"]), q["section"],
                 int(q["global_ok"]), json.dumps(q["kinds"]) if q["kinds"] else None, q["choices"],
                 json.dumps(q["require"]), json.dumps(q["exclude"]), ts))
            wanted = {(normalize(a), 0): (a, 0) for a in [q["text"], *q["aliases"]] if normalize(a)}
            wanted.update({(p["regex"], 1): (p["regex"], p["priority"]) for p in q["patterns"]})
            existing = {(r["normalized"], r["is_pattern"]): r["id"] for r in conn.execute(
                "SELECT id, normalized, is_pattern FROM question_aliases WHERE question_id=? AND origin='builtin'",
                (q["id"],))}
            for key, alias_id in existing.items():
                if key not in wanted:
                    conn.execute("DELETE FROM question_aliases WHERE id=?", (alias_id,))
            for (norm, is_pattern), (text, priority) in wanted.items():
                conn.execute(
                    """INSERT INTO question_aliases (question_id, text, normalized, is_pattern, priority, origin,
                                                     created_at)
                       VALUES (?,?,?,?,?, 'builtin', ?)
                       ON CONFLICT(question_id, normalized, is_pattern) DO UPDATE SET priority=excluded.priority""",
                    (q["id"], text, norm, is_pattern, priority, ts))
        _canonicalize_choice_facts(conn, ts)
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('catalog', ?)", (_catalog_fingerprint(),))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    return {"facts": len(FACTS), "questions": len(QUESTIONS)}
