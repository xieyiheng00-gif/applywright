"""Command line interface. Agents (Claude Code, Codex, ...) drive jobfill through these commands.

Run ``python -m jobfill <command> --help`` for details. Data commands print JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db, report
from .catalog import PLAYBOOKS, RECORD_KINDS, choice_set
from .resolver import Resolver, ai_task_for, dumps
from .store import Store, ValidationError
from .text import company_key, normalize
from .writing import check_answer, mentions


def _out(obj) -> None:
    print(obj if isinstance(obj, str) else dumps(obj))


def _read_json(path: str):
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def _data_arg(args) -> dict:
    if getattr(args, "file", None):
        return _read_json(args.file)
    if getattr(args, "data", None):
        return json.loads(args.data)
    raise ValidationError("pass --data '<json>' or --file <path> (use --file on Windows to avoid quoting trouble)")


# ---------------------------------------------------------------------- commands

def cmd_init(store, args):
    from . import accounts
    info = db.seed(store.conn)
    _out({"db": str(args.db or db.default_db_path()), "seeded": info,
          "secret_file": str(accounts.ensure_secret(_data_dir(args))), "status": store.status()})


def cmd_status(store, args):
    s = store.status()
    if args.json:
        return _out(s)
    lines = [f"Setup complete: {'yes' if s['setup_complete'] else 'no'}"]
    for tier, levels in s["facts_by_tier"].items():
        lines.append(f"Tier {tier} facts  " + "  ".join(f"{k} {v}" for k, v in levels.items()))
    lines.append("Records: " + (", ".join(f"{k}={v}" for k, v in sorted(s["records"].items())) or "none"))
    if s["missing_required"]:
        lines.append("Missing (required): " + ", ".join(s["missing_required"]))
    if s["stale"]:
        lines.append("Due for review: " + ", ".join(s["stale"]))
    qb = s["question_bank"]
    lines.append(f"Question bank: {qb['builtin']} built-in + {qb['learned']} learned questions "
                 f"(tiers {qb['by_tier']}), {qb['aliases_builtin'] + qb['aliases_learned']} phrasings")
    lines.append(f"Pending new questions: {s['pending_questions']}   Applications: {s['applications']}")
    _out("\n".join(lines))


def cmd_missing(store, args):
    _out(store.missing(tier=args.tier, level=args.level))


def cmd_fact(store, args):
    if args.action == "get":
        _out({"key": args.key, "value": store.get_fact(args.key)})
    elif args.action == "set":
        value = json.loads(args.value) if args.json else args.value
        _out({"key": args.key, "value": store.set_fact(args.key, value, args.source)})
    elif args.action == "unset":
        _out({"key": args.key, "removed": store.unset_fact(args.key, args.source)})
    elif args.action == "confirm":
        _out({"key": args.key, "confirmed": store.confirm_fact(args.key)})
    elif args.action == "list":
        defs, rows = store.fact_defs(), store.fact_rows()
        out = [{"key": k, "tier": d["tier"], "label": d["label"], "set": k in rows,
                "value": rows[k]["value"] if k in rows else None,
                **({"choices": sorted(choice_set(d["choices"])["_values"])} if d["type"] == "enum" else {})}
               for k, d in defs.items()
               if (args.tier is None or d["tier"] == args.tier) and (not args.category or d["category"] == args.category)]
        _out(out)
    elif args.action == "define":
        _out(store.define_fact(args.key, args.tier, args.type, args.label, args.prompt, choices=args.choices,
                               multi=args.multi, sensitive=args.sensitive, review_days=args.review_days))
    elif args.action == "history":
        rows = store.conn.execute("SELECT old_value, new_value, source, changed_at FROM fact_history WHERE key=? "
                                  "ORDER BY id", (args.key,)).fetchall()
        _out([dict(r) for r in rows])


def cmd_record(store, args):
    if args.action == "list":
        _out(store.records(args.kind) if args.kind else store.all_records())
    elif args.action == "add":
        rid = store.add_record(args.kind, _data_arg(args), args.source)
        _out({"id": rid, "kind": args.kind, "data": store.get_record(rid)[1]})
    elif args.action == "update":
        _out({"id": args.id, "data": store.update_record(args.id, _data_arg(args), merge=not args.replace,
                                                         source=args.source)})
    elif args.action == "remove":
        _out({"id": args.id, "removed": store.remove_record(args.id)})
    elif args.action == "kinds":
        _out(RECORD_KINDS)


def cmd_import(store, args):
    patch = _read_json(args.file)
    replace = {r.strip() for r in (args.replace or "").split(",") if r.strip()}
    _out(store.import_patch(patch, dry_run=not args.apply, replace=replace, replace_all=args.replace_all))


def cmd_export(store, args):
    data = store.export(tier=args.tier)
    text = dumps(data) if args.format == "json" else _markdown(store, data)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        _out({"written": args.out})
    else:
        _out(text)


def _markdown(store, data) -> str:
    defs = store.fact_defs()
    lines = ["# Candidate profile (jobfill export)", ""]
    for tier, title in ((1, "Tier 1 - static"), (2, "Tier 2 - evolving")):
        rows = [(k, v) for k, v in data["facts"].items() if defs.get(k, {}).get("tier") == tier]
        if not rows:
            continue
        lines += [f"## {title}", ""]
        cat = None
        for k, v in rows:
            if defs[k]["category"] != cat:
                cat = defs[k]["category"]
                lines += ["", f"### {cat}"]
            shown = ", ".join(map(str, v)) if isinstance(v, list) else ("yes" if v is True else "no" if v is False else v)
            lines.append(f"- **{defs[k]['label']}** (`{k}`): {shown}")
        lines.append("")
    for kind, items in data["records"].items():
        lines += [f"## {kind.title()} ({len(items)})", ""]
        for r in items:
            head = r.get("name") or r.get("title") or r.get("school") or r.get("company")
            rest = {k: v for k, v in r.items() if v != head}
            lines.append(f"- **{head}**: " + "; ".join(f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}"
                                                      for k, v in rest.items()))
        lines.append("")
    return "\n".join(lines)


def cmd_match(store, args):
    from .matcher import Matcher
    m = Matcher.from_store(store).match(args.label, section=args.section, field_type=args.type,
                                        company=args.company, role=args.role)
    _out({"status": m.status, "question_id": m.question_id, "score": m.score, "params": m.params,
          "normalized": m.normalized, "candidates": m.candidates})


def cmd_resolve(store, args):
    payload = _read_json(args.file)
    fields = payload["fields"] if isinstance(payload, dict) else payload
    app_id = args.app or (payload.get("application_id") if isinstance(payload, dict) else None)
    result = Resolver(store, app_id, log=not args.no_log).resolve(fields)
    if args.compact:
        result["fields"] = [{k: v for k, v in f.items() if k not in ("match", "source", "raw")} for f in result["fields"]]
    _out(result)


def cmd_context(store, args):
    _out(ai_task_for(store, args.question, args.app, args.label, max_chars=args.max_chars, max_words=args.max_words))


def _check_answer(store, args, text: str) -> dict:
    """`writing.check_answer` with what only the database knows: other companies and earlier answers."""
    app = store.application(args.app)
    own = {company_key(r.get("company")) for r in store.records("experience")} | \
          {company_key(r.get("school")) for r in store.records("education")}
    posting = normalize(app.get("posting"))
    others = [a["company"] for a in store.applications()
              if company_key(a["company"]) not in own and not mentions(posting, company_key(a["company"]))]
    return check_answer(text, question_id=args.question, company=app["company"], max_chars=args.max_chars,
                        max_words=args.max_words, min_words=args.min_words, other_companies=others,
                        earlier=store.written_answers(exclude_app_id=args.app))


def cmd_answer(store, args):
    if args.action in ("record", "check"):
        text = Path(args.file).read_text(encoding="utf-8").strip() if args.file else args.text
        if not text:
            raise ValidationError("pass --text or --file")
        check = _check_answer(store, args, text)
        if args.action == "check":
            return _out(check)
        if not check["ok"] and args.source == "ai" and not args.force:
            return _out({"recorded": False, "check": check,
                         "next": "Fix every `fix` issue and record again (--force only if one is a false alarm)."})
        q = store.questions().get(args.question)
        rows = store.answers(args.app)
        label = args.label
        if not label:   # attach to the open question of this type on the form, if there is exactly one
            open_rows = [a for a in rows if a["question_id"] == args.question and a["status"] != "filled"]
            label = open_rows[0]["label"] if len(open_rows) == 1 else (q["text"] if q else args.question)
        row = next((a for a in rows if normalize(a["label"]) == normalize(label)), None)
        store.record_answer(args.app, row["field_key"] if row else store.answer_key(label), label, text,
                            question_id=args.question, source=args.source, tier=q["tier"] if q else None,
                            status="filled", section=row["section"] if row else None, idx=row["idx"] if row else None)
        _out({"recorded": True, "application_id": args.app, "question_id": args.question, "chars": len(text),
              "words": len(text.split()), "issues": check["issues"],
              "record": report.write(store, _data_dir(args), args.app)["markdown"]})
    elif args.action == "list":
        _out(store.answers(args.app))


def cmd_app(store, args):
    if args.action == "start":
        posting = Path(args.posting_file).read_text(encoding="utf-8") if args.posting_file else None
        app_id = store.start_application(args.company, args.role, url=args.url, ats=args.ats, country=args.country,
                                         source=args.source, referrer=args.referrer, posting=posting, notes=args.notes,
                                         resume=_resume_arg(store, args.resume))
        prior = store.same_company_apps(args.company, exclude_id=app_id)
        _out({"id": app_id, "company": args.company, "role": args.role,
              "previous_applications_to_company": [{"id": a["id"], "role": a["role"], "status": a["status"]} for a in prior]})
    elif args.action == "list":
        _out(store.applications())
    elif args.action == "show":
        app = store.application(args.id)
        app["posting"] = (app["posting"] or "")[:500] + ("..." if app["posting"] and len(app["posting"]) > 500 else "")
        _out({**app, "answers": store.answers(args.id)})
    elif args.action == "update":
        posting = Path(args.posting_file).read_text(encoding="utf-8") if args.posting_file else None
        app = store.update_application(args.id, role=args.role, url=args.url, ats=args.ats, country=args.country,
                                       source=args.source, referrer=args.referrer, posting=posting, notes=args.notes,
                                       status=args.status, resume=_resume_arg(store, args.resume))
        app["posting"] = (app["posting"] or "")[:200]
        _out({**app, "record": report.write(store, _data_dir(args), args.id)["markdown"]})
    elif args.action == "report":
        written = report.write(store, _data_dir(args), args.id)
        _out(report.markdown(report.build(store, args.id)) if args.print else written)
    elif args.action == "stats":
        _out(report.stats(store))


def _resume_arg(store, path: str | None) -> str | None:
    """--resume: the file must exist; stored relative to the project folder, like documents.resume_path."""
    if not path:
        return None
    from . import resumes
    try:
        return resumes.choose(store, db.ROOT, "", None, path)["file"]
    except FileNotFoundError as e:
        raise ValidationError(str(e))


def _data_dir(args) -> Path:
    """Folder that holds the database; per-application files live beside it."""
    return Path(args.db).resolve().parent if args.db else db.default_db_path().parent


def cmd_apply(store, args):
    from .apply import runner
    from .apply.detect import detect
    data_dir = _data_dir(args)
    try:
        if args.action == "detect":
            return _out(detect(args.url, network=not args.no_network).to_dict())
        if args.action == "start":
            return _out(runner.start(store, data_dir, args.url, company=args.company, role=args.role,
                                     source=args.source, referrer=args.referrer, ats=args.ats,
                                     network=not args.no_network, resume=args.resume))
        if args.action == "fill":
            return _out(runner.fill_command(store, data_dir, args.app, overwrite=args.overwrite,
                                            headless=args.headless, files=not args.no_files,
                                            dry_run=args.dry_run, here=args.here))
        if args.action == "set":
            if bool(args.value) == bool(args.file):
                raise ValidationError("pass --value or --file")
            text = Path(args.file).read_text(encoding="utf-8").strip() if args.file else args.value
            value = json.loads(text) if args.json else text
            return _out(runner.set_command(store, data_dir, args.app, args.field, value, headless=args.headless,
                                           force=args.force, here=args.here))
        if args.action == "watch":
            return _out(runner.watch_command(store, data_dir, args.app, timeout=args.timeout, headless=args.headless))
        if args.action == "next":
            return _out(runner.next_command(data_dir, args.app, headless=args.headless))
        if args.action == "submit":
            return _out(runner.submit_command(store, data_dir, args.app, confirm=args.confirm, headless=args.headless))
        if args.action == "status":
            report = runner.state_dir(data_dir, args.app) / "report.json"
            if not report.exists():
                raise runner.ApplyError("no fill report yet; run `apply fill` first")
            return _out(runner.summarize(json.loads(report.read_text(encoding="utf-8")), args.app))
        if args.action == "close":
            from .apply import browser
            return _out({"closed": browser.close(data_dir)})
    except runner.ApplyError as e:
        print(dumps(e.payload), file=sys.stderr)
        raise SystemExit(e.code)


TERMINAL_ONLY = ("`jobfill {cmd}` {why}, so it only runs when you start it yourself in a terminal "
                 "(it was started without one). Run it in your own terminal: python -m jobfill {usage}")


def _require_terminal(cmd: str, why: str, usage: str) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ValidationError(TERMINAL_ONLY.format(cmd=cmd, why=why, usage=usage))


def cmd_login(store, args):
    from urllib.parse import urlparse
    from . import accounts
    from .apply import adapters, browser, runner
    from .apply.login import login_flow
    from .mail import ImapMail
    _require_terminal("login", "types a password into a website", f"login --app {args.app}")
    data_dir = _data_dir(args)
    accounts.ensure_secret(data_dir)
    state = runner.load_state(data_dir, args.app)
    app = store.application(args.app)
    if store.get_fact("account.login_email") is None:
        default = store.get_fact("contact.email") or ""
        answer = input(f"Email for job-portal accounts [{default}]: ").strip() or default
        print(f"Saved: {store.set_fact('account.login_email', answer, 'login')} (change it with `jobfill account email --set`)")
    if store.get_fact("consent.account_terms") is None:
        answer = input("When a new account requires accepting the site's terms / privacy policy, may jobfill tick "
                       "that box for you? [y/N]: ")
        store.set_fact("consent.account_terms", answer.strip().lower() in ("y", "yes"), "login")
    mail = None if args.no_mail else ImapMail.from_env(accounts.load_env(data_dir / accounts.SECRET_FILE))
    adapter = adapters.get(state["ats"])
    with browser.session(data_dir, args.headless) as ctx:
        page, _ = runner.open_page(ctx, state, adapter)
        outcome = login_flow(page, adapter.frame(page), store, data_dir, accounts.account_key(page.url), app["company"],
                             mail=mail)
        state["page_url"] = page.url
    runner.save_state(data_dir, args.app, state)
    out = outcome.to_dict()
    out["mail_connector"] = "on" if mail else "off (optional: see data/secret.env)"
    if outcome.status in ("signed_in", "no_login_form"):
        out["next"] = f"Tell the agent to continue: python -m jobfill apply fill --app {args.app}"
    _out(out)


def cmd_portal(store, args):
    from . import resumes
    from .portal import server
    database = resumes.shown(Path(args.db) if args.db else db.default_db_path(), db.ROOT)
    server.serve(store, db.ROOT, database=database, port=args.port, open_browser=not args.no_browser,
                 keep_open=args.keep_open)


def cmd_choice(store, args):
    """Choices the candidate picked where a form had no option exactly matching a stored answer."""
    if args.action == "list":
        _out(store.all_learned_choices())
    elif args.action == "forget":
        _out({"forgotten": store.forget_choices(args.question, args.value, args.choice)})


def cmd_account(store, args):
    from . import accounts
    data_dir = _data_dir(args)
    if args.action == "email":
        if args.set:
            store.set_fact("account.login_email", args.set, "user")
        _out({"login_email": accounts.login_email(store),
              "source": "account.login_email" if store.get_fact("account.login_email") else "contact.email (default)"})
    elif args.action == "list":
        _out([{k: a[k] for k in ("host", "company_key", "email", "status", "version", "updated_at")}
              for a in store.accounts()])
    elif args.action == "password":
        row = accounts.find_account(store, accounts.account_key(args.host)) if args.host else None
        if row is None and not args.company:
            raise ValidationError("pass --host of an existing account (see `account list`) or --company for a new site")
        name, version = (row["company_key"], row["version"]) if row else (accounts.account_name(args.company), 1)
        if not args.show:
            return _out({"company_key": name, "version": version,
                         "note": "Run with --show in your own terminal to print the password."})
        _require_terminal("account password --show", "prints a password", "account password --show ...")
        print(accounts.derive_password(accounts.secret(data_dir), name, version))
    elif args.action in ("rotate", "forget"):
        row = accounts.find_account(store, accounts.account_key(args.host))
        if row is None:
            raise ValidationError(f"no account for {args.host} (see `jobfill account list`)")
        if args.action == "forget":
            return _out({"host": row["host"], "removed": store.forget_account(row["host"])})
        row = store.rotate_account(row["host"])
        _out({"host": row["host"], "version": row["version"],
              "note": "Set the site's password to the new one: `account password --host ... --show` (yourself)."})


def cmd_mail(store, args):
    from . import accounts
    from .mail import ImapMail
    path = accounts.ensure_secret(_data_dir(args))
    mail = ImapMail.from_env(accounts.load_env(path))
    if mail is None:
        return _out({"connected": False, "note": f"Optional. Fill the JOBFILL_IMAP_* lines in {path} to let "
                                                 "`jobfill login` read verification codes."})
    _out(mail.test())


def cmd_pending(store, args):
    _out(store.pending(None if args.all else "open"))


def cmd_learn(store, args):
    p = store.get_pending(args.pending_id)
    if args.ignore:
        store.close_pending(p["id"], "ignored", args.reason or "ignored")
        return _out({"pending_id": p["id"], "status": "ignored"})
    if args.alias_of:
        text = args.pattern or p["label"]
        added = store.add_alias(args.alias_of, text, is_pattern=bool(args.pattern), priority=args.priority)
        store.close_pending(p["id"], "learned", f"alias_of:{args.alias_of}")
        return _out({"pending_id": p["id"], "status": "learned", "alias_of": args.alias_of, "added": added})
    if args.new_question:
        if args.fact:
            answer, tier = {"fact": args.fact}, store.fact_defs()[args.fact]["tier"] if args.fact in store.fact_defs() else None
            if tier is None:
                raise ValidationError(f"unknown fact {args.fact!r}; run `jobfill fact define` first")
        elif args.ai:
            answer, tier = {"ai": {"mode": args.ai, "scope": args.scope, "guidance": args.guidance or
                                   "Answer from the candidate data and the posting.",
                                   "context": [c.strip() for c in args.context.split(",") if c.strip()],
                                   "themes": [], "deliverable": "text", "optional": "answer"}}, 3
            if args.playbook:
                answer["ai"]["playbook"] = args.playbook
        else:
            raise ValidationError("--new-question needs --fact KEY or --ai unique|adapt")
        q = store.add_question(args.new_question, tier, args.category, args.text or p["label"], answer,
                               section=args.section, choices=args.choices, aliases=[p["label"]])
        if args.pattern:
            store.add_alias(q["id"], args.pattern, is_pattern=True, priority=args.priority)
        store.close_pending(p["id"], "learned", f"new_question:{q['id']}")
        return _out({"pending_id": p["id"], "status": "learned", "question": q})
    raise ValidationError("choose one of --alias-of, --new-question, --ignore")


def cmd_question(store, args):
    qs = store.questions()
    if args.action == "list":
        out = [{"id": q["id"], "tier": q["tier"], "category": q["category"], "text": q["text"],
                "strategy": next(iter(q["answer"])), "section": q["section"], "origin": q["origin"]}
               for q in qs.values()
               if (args.tier is None or q["tier"] == args.tier) and (not args.category or q["category"] == args.category)
               and (not args.learned or q["origin"] == "learned")]
        _out(sorted(out, key=lambda q: (q["tier"], q["category"], q["id"])))
    elif args.action == "show":
        q = qs.get(args.id)
        if not q:
            raise ValidationError(f"unknown question {args.id!r}")
        q["aliases"] = [a["text"] for a in store.aliases() if a["question_id"] == args.id and not a["is_pattern"]]
        q["patterns"] = [a["text"] for a in store.aliases() if a["question_id"] == args.id and a["is_pattern"]]
        _out(q)
    elif args.action == "alias":
        _out({"question_id": args.id, "added": store.add_alias(args.id, args.text, is_pattern=args.pattern,
                                                               priority=args.priority)})


def cmd_catalog(store, args):
    if args.action == "export-learned":
        conn = store.conn
        data = {
            "fact_defs": [dict(r) for r in conn.execute("SELECT * FROM fact_defs WHERE origin='learned'")],
            "questions": [dict(r) for r in conn.execute("SELECT * FROM questions WHERE origin='learned'")],
            "aliases": [dict(r) for r in conn.execute(
                "SELECT question_id, text, normalized, is_pattern, priority FROM question_aliases WHERE origin='learned'")],
        }
        text = dumps(data)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            return _out({"written": args.out, "questions": len(data["questions"]), "aliases": len(data["aliases"])})
        _out(text)
    elif args.action == "import-learned":
        data = _read_json(args.file)
        added = {"fact_defs": 0, "questions": 0, "aliases": 0}
        existing_defs, existing_q = store.fact_defs(), store.questions()
        for d in data.get("fact_defs", []):
            if d["key"] not in existing_defs:
                store.define_fact(d["key"], d["tier"], d["type"], d["label"], d["prompt"], choices=d.get("choices"),
                                  multi=bool(d.get("multi")), sensitive=bool(d.get("sensitive")),
                                  review_days=d.get("review_days"), setup=d.get("setup", "optional"))
                added["fact_defs"] += 1
        for q in data.get("questions", []):
            if q["id"] not in existing_q:
                store.add_question(q["id"], q["tier"], q["category"], q["text"], json.loads(q["answer"]),
                                   section=q.get("section"), kinds=json.loads(q["kinds"]) if q.get("kinds") else None,
                                   choices=q.get("choices"), require=json.loads(q.get("require") or "[]"),
                                   exclude=json.loads(q.get("exclude") or "[]"))
                added["questions"] += 1
        for a in data.get("aliases", []):
            if store.add_alias(a["question_id"], a["text"], is_pattern=bool(a["is_pattern"]), priority=a["priority"]):
                added["aliases"] += 1
        _out(added)


# ---------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobfill", description="Tiered job-application answer database.")
    p.add_argument("--db", help="database path (default: data/jobfill.db or $JOBFILL_DB)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database and load the built-in question bank").set_defaults(fn=cmd_init)
    s = sub.add_parser("status", help="setup progress, stale facts, pending questions")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("missing", help="facts and records still to collect, with the prompt to ask")
    s.add_argument("--tier", type=int, choices=[1, 2])
    s.add_argument("--level", default="recommended", choices=["required", "recommended", "optional"])
    s.set_defaults(fn=cmd_missing)

    s = sub.add_parser("fact", help="get/set/unset/list/define single-valued facts")
    fs = s.add_subparsers(dest="action", required=True)
    x = fs.add_parser("get"); x.add_argument("key")
    x = fs.add_parser("set"); x.add_argument("key"); x.add_argument("value")
    x.add_argument("--json", action="store_true", help="parse VALUE as JSON"); x.add_argument("--source", default="user")
    x = fs.add_parser("unset"); x.add_argument("key"); x.add_argument("--source", default="user")
    x = fs.add_parser("confirm", help="mark a stored value as still correct (resets its review clock)")
    x.add_argument("key")
    x = fs.add_parser("list"); x.add_argument("--tier", type=int); x.add_argument("--category")
    x = fs.add_parser("history"); x.add_argument("key")
    x = fs.add_parser("define", help="add a new fact type learned from a form")
    x.add_argument("key"); x.add_argument("--tier", type=int, required=True, choices=[1, 2])
    x.add_argument("--type", required=True); x.add_argument("--label", required=True)
    x.add_argument("--prompt", required=True); x.add_argument("--choices")
    x.add_argument("--multi", action="store_true"); x.add_argument("--sensitive", action="store_true")
    x.add_argument("--review-days", type=int)
    s.set_defaults(fn=cmd_fact)

    s = sub.add_parser("record", help="list/add/update/remove repeating records")
    rs = s.add_subparsers(dest="action", required=True)
    x = rs.add_parser("list"); x.add_argument("kind", nargs="?")
    x = rs.add_parser("add"); x.add_argument("kind", choices=sorted(RECORD_KINDS))
    x.add_argument("--data"); x.add_argument("--file"); x.add_argument("--source", default="user")
    x = rs.add_parser("update"); x.add_argument("id", type=int); x.add_argument("--data"); x.add_argument("--file")
    x.add_argument("--replace", action="store_true", help="replace instead of merging fields")
    x.add_argument("--source", default="user")
    x = rs.add_parser("remove"); x.add_argument("id", type=int)
    rs.add_parser("kinds", help="show record kinds and their fields")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("import", help="merge a profile patch (dry run unless --apply)")
    s.add_argument("file"); s.add_argument("--apply", action="store_true")
    s.add_argument("--replace", help="comma-separated conflict ids to overwrite")
    s.add_argument("--replace-all", action="store_true")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("portal", help="fill the profile in a local web form laid out like a job application portal")
    s.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (a free one if taken)")
    s.add_argument("--no-browser", action="store_true", help="print the link instead of opening it")
    s.add_argument("--keep-open", action="store_true", help="keep serving after Submit (stop with Ctrl+C)")
    s.set_defaults(fn=cmd_portal)

    s = sub.add_parser("export", help="dump the profile as JSON or Markdown")
    s.add_argument("--format", default="json", choices=["json", "md"]); s.add_argument("--tier", type=int)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("match", help="debug: which question does a label map to?")
    s.add_argument("label"); s.add_argument("--section"); s.add_argument("--type")
    s.add_argument("--company"); s.add_argument("--role")
    s.set_defaults(fn=cmd_match)

    s = sub.add_parser("resolve", help="answer a list of form fields (JSON file or - for stdin)")
    s.add_argument("file"); s.add_argument("--app", type=int)
    s.add_argument("--no-log", action="store_true", help="do not write filled answers to the answer log")
    s.add_argument("--compact", action="store_true")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("context", help="compact AI context for one tier-3 question")
    s.add_argument("--question", required=True); s.add_argument("--app", type=int); s.add_argument("--label")
    s.add_argument("--max-chars", type=int); s.add_argument("--max-words", type=int)
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("answer", help="record an AI/user answer in the application log")
    an = s.add_subparsers(dest="action", required=True)
    for name in ("check", "record"):
        x = an.add_parser(name, help="check a written answer, then record it" if name == "record" else
                          "check a draft: limits, placeholders, other companies' names, reuse, cliches")
        x.add_argument("--app", type=int, required=True); x.add_argument("--question", required=True)
        x.add_argument("--text"); x.add_argument("--file")
        x.add_argument("--max-chars", type=int); x.add_argument("--max-words", type=int)
        x.add_argument("--min-words", type=int)
    # x is now the record parser.
    x.add_argument("--label", help="the question's wording on the form (links the answer to that question)")
    x.add_argument("--source", default="ai", choices=["ai", "user"])
    x.add_argument("--force", action="store_true", help="record although the check found `fix` issues")
    x = an.add_parser("list"); x.add_argument("--app", type=int, required=True)
    s.set_defaults(fn=cmd_answer)

    s = sub.add_parser("app", help="applications")
    ap = s.add_subparsers(dest="action", required=True)
    for name in ("start", "update"):
        x = ap.add_parser(name)
        if name == "start":
            x.add_argument("--company", required=True)
        else:
            x.add_argument("id", type=int); x.add_argument("--status")
        for opt in ("--role", "--url", "--ats", "--country", "--source", "--referrer", "--posting-file", "--notes"):
            x.add_argument(opt)
        x.add_argument("--resume", help="CV file this application uploads instead of documents.resume_path")
    ap.add_parser("list")
    x = ap.add_parser("show"); x.add_argument("id", type=int)
    x = ap.add_parser("report", help="write the result record: every question, its answer, who answered it")
    x.add_argument("id", type=int); x.add_argument("--print", action="store_true", help="print the Markdown")
    ap.add_parser("stats", help="plain Python vs AI vs you, across all applications")
    s.set_defaults(fn=cmd_app)

    s = sub.add_parser("apply", help="detect the application system of a job URL and fill its form")
    aps = s.add_subparsers(dest="action", required=True)
    x = aps.add_parser("detect", help="which system does this job URL use? (no browser)")
    x.add_argument("url"); x.add_argument("--no-network", action="store_true")
    x = aps.add_parser("start", help="detect + create the application; prints how to continue")
    x.add_argument("url"); x.add_argument("--company"); x.add_argument("--role"); x.add_argument("--source")
    x.add_argument("--referrer"); x.add_argument("--no-network", action="store_true")
    x.add_argument("--ats", help="override detection after checking the page (greenhouse|lever|icims|custom|...)")
    x.add_argument("--resume", help="CV for this application (default: a tailored CV under documents/resume/ "
                                    "named after the company and role, else documents.resume_path)")
    for name, helptext in (("fill", "fill the form from the database (Greenhouse/Lever/iCIMS/Ashby)"),
                           ("set", "type one value into one field"), ("next", "click Next/Continue (never submit)"),
                           ("submit", "click the final submit button; requires --confirm"),
                           ("watch", "wait until the candidate changes page (Next, Back, submit) in the window"),
                           ("status", "summary of the last fill")):
        x = aps.add_parser(name, help=helptext)
        x.add_argument("--app", type=int, required=True)
        x.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
        if name == "fill":
            x.add_argument("--overwrite", action="store_true",
                           help="re-type values jobfill typed earlier (never the candidate's own answers)")
            x.add_argument("--no-files", action="store_true", help="do not upload files")
            x.add_argument("--dry-run", action="store_true",
                           help="read and resolve the form without typing anything (preview)")
        if name in ("fill", "set"):
            x.add_argument("--here", action="store_true",
                           help="act on an earlier page the candidate went back to (only when they ask)")
        if name == "set":
            x.add_argument("--field", required=True); x.add_argument("--value")
            x.add_argument("--file", help="read the value from a file (written answers)")
            x.add_argument("--json", action="store_true", help="parse VALUE as JSON (lists, booleans)")
            x.add_argument("--force", action="store_true",
                           help="replace an answer the candidate typed in the window (only when they ask)")
        if name == "watch":
            x.add_argument("--timeout", type=float, default=600, help="seconds to wait (default 600)")
        if name == "submit":
            x.add_argument("--confirm", action="store_true",
                           help="the candidate explicitly approved submitting this application")
    aps.add_parser("close", help="close the jobfill browser window")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("login", help="create or sign in to this application's job-portal account (run it yourself)")
    s.add_argument("--app", type=int, required=True)
    s.add_argument("--no-mail", action="store_true", help="do not use the mail connector")
    s.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_login)

    s = sub.add_parser("account", help="job-portal accounts: login email, list, passwords (no passwords are stored)")
    acs = s.add_subparsers(dest="action", required=True)
    x = acs.add_parser("email", help="show or change the login email"); x.add_argument("--set")
    acs.add_parser("list")
    x = acs.add_parser("password", help="the derived password for a site (printed only with --show, in your terminal)")
    x.add_argument("--host"); x.add_argument("--company"); x.add_argument("--show", action="store_true")
    x = acs.add_parser("rotate", help="use the next password for a site after resetting it there"); x.add_argument("--host", required=True)
    x = acs.add_parser("forget", help="drop an account record"); x.add_argument("--host", required=True)
    s.set_defaults(fn=cmd_account)

    s = sub.add_parser("choice", help="choices picked where no option was exactly the stored answer (reused on "
                                      "other forms)")
    chs = s.add_subparsers(dest="action", required=True)
    chs.add_parser("list")
    x = chs.add_parser("forget", help="drop remembered choices (all for a question, or one stored answer / choice)")
    x.add_argument("--question", required=True); x.add_argument("--value"); x.add_argument("--choice")
    s.set_defaults(fn=cmd_choice)

    s = sub.add_parser("mail", help="optional mail connector for verification codes")
    ms = s.add_subparsers(dest="action", required=True)
    ms.add_parser("test", help="check the IMAP settings in data/secret.env")
    s.set_defaults(fn=cmd_mail)

    s = sub.add_parser("pending", help="questions seen in forms that the bank could not answer")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_pending)

    s = sub.add_parser("learn", help="teach the bank a pending question")
    s.add_argument("pending_id", type=int)
    s.add_argument("--alias-of", help="existing question id this is a rephrasing of")
    s.add_argument("--new-question", help="id for a new question")
    s.add_argument("--fact", help="fact the new question reads (tier 1/2)")
    s.add_argument("--ai", choices=["unique", "adapt"], help="make the new question tier 3")
    s.add_argument("--scope", default="application", choices=["company", "application"])
    s.add_argument("--context", default="summary,experience,projects,skills")
    s.add_argument("--guidance"); s.add_argument("--category", default="custom"); s.add_argument("--text")
    s.add_argument("--playbook", choices=sorted(PLAYBOOKS),
                   help="writing technique for a tier-3 question (default: from --category)")
    s.add_argument("--section"); s.add_argument("--choices")
    s.add_argument("--pattern", help="regex over normalized text instead of the literal label")
    s.add_argument("--priority", type=int, default=0)
    s.add_argument("--ignore", action="store_true"); s.add_argument("--reason")
    s.set_defaults(fn=cmd_learn)

    s = sub.add_parser("question", help="browse the question bank or add phrasings")
    qs = s.add_subparsers(dest="action", required=True)
    x = qs.add_parser("list"); x.add_argument("--tier", type=int); x.add_argument("--category")
    x.add_argument("--learned", action="store_true")
    x = qs.add_parser("show"); x.add_argument("id")
    x = qs.add_parser("alias"); x.add_argument("id"); x.add_argument("text")
    x.add_argument("--pattern", action="store_true"); x.add_argument("--priority", type=int, default=0)
    s.set_defaults(fn=cmd_question)

    s = sub.add_parser("catalog", help="share learned questions between machines")
    cs = s.add_subparsers(dest="action", required=True)
    x = cs.add_parser("export-learned"); x.add_argument("--out")
    x = cs.add_parser("import-learned"); x.add_argument("file")
    s.set_defaults(fn=cmd_catalog)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    conn = db.connect(args.db)
    try:
        args.fn(Store(conn), args)
    except (ValidationError, KeyError, ValueError, json.JSONDecodeError) as e:
        print(dumps({"error": str(e)}), file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0
