"""The portal's form: its pages with each field's widget and current value, and saving what comes back.

Everything is written through ``Store`` (``set_fact``, ``add_record``...), so values are checked and
canonicalized exactly as `jobfill fact set` and `jobfill import` would. A save checks the whole
submission first and writes nothing when any field is wrong; a blank field removes the stored value.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import resumes
from ..catalog import CHOICE_SETS, RECORD_KINDS
from ..store import RECORD_SETUP, ValidationError
from ..text import ONGOING, iso_date
from . import layout

# Widget for a fact type when the layout names none (enums: select, or checkboxes when multi).
WIDGETS = {"string": "text", "text": "textarea", "bool": "yesno", "number": "number", "date": "date",
           "list": "list", "url": "url", "email": "email", "phone": "tel", "path": "file"}
FULL_WIDTH = {"textarea", "lines", "checkboxes", "file"}
YES_NO = [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]
REQUIRED = "This field is required."

# Where an attached file is kept, per fact; files never leave documents/.
UPLOAD_FOLDERS = {"documents.resume_path": "resume", "documents.transcript_path": "transcripts",
                  "documents.portfolio_path": "other"}
UPLOAD_TYPES = resumes.EXTENSIONS
MAX_UPLOAD = 15 * 1024 * 1024


# ---------------------------------------------------------------------- schema

def _humanize(value: str) -> str:
    return value.replace("_", " ").capitalize()


def options(set_name: str) -> list[dict]:
    """A choice set as the page's options, in the catalog's order."""
    labels = {**CHOICE_SETS[set_name].get("labels", {}), **layout.CHOICE_LABELS.get(set_name, {})}
    return [{"value": v, "label": labels.get(v) or _humanize(v)} for v in CHOICE_SETS[set_name]["values"]]


def _fact_field(d: dict, item: dict | None) -> dict:
    item = item or {"label": d["label"], "help": d["prompt"]}
    widget = item.get("widget") or (("checkboxes" if d["multi"] else "select") if d["type"] == "enum"
                                    else WIDGETS.get(d["type"], "text"))
    label = item.get("label") or d["label"]
    field = {"type": "fact", "key": d["key"], "label": label, "help": item.get("help"), "widget": widget,
             "placeholder": item.get("placeholder"), "required": d["setup"] == "required",
             "recommended": d["setup"] == "recommended", "sensitive": d["sensitive"],
             "wide": bool(item.get("wide")) or widget in FULL_WIDTH or len(label) > 60}
    if widget == "yesno":
        field["options"] = YES_NO
    elif widget in ("select", "checkboxes"):
        field["options"] = options(d["choices"])
    if widget == "file":
        field["accept"] = ",".join(sorted(UPLOAD_TYPES))
    for opt in ("none_label", "default_from"):
        if item.get(opt):
            field[opt] = item[opt]
    return field


def _record_block(item: dict) -> dict:
    fields = []
    for f in item["fields"]:
        f = dict(f)
        opts = f.pop("options")
        if f["widget"] == "yesno":
            f["options"] = YES_NO
        elif isinstance(opts, str):
            f["options"] = options(opts)
        elif opts:
            f["options"] = [{"value": v, "label": lab} for v, lab in opts]
        f["wide"] = f["wide"] or f["widget"] in FULL_WIDTH
        fields.append(f)
    level = RECORD_SETUP.get(item["record"], "optional")
    return {**item, "fields": fields, "required": level == "required", "recommended": level == "recommended",
            "key_field": RECORD_KINDS[item["record"]]["key"][0]}


def schema(store) -> dict:
    """Every page, section and field the portal shows, with options and required marks."""
    defs = store.fact_defs()
    placed, pages = set(), []
    for page in layout.PAGES:
        sections = []
        for sec in page["sections"]:
            items = []
            for item in sec["items"]:
                if item["type"] == "records":
                    items.append(_record_block(item))
                elif item["key"] in defs:
                    placed.add(item["key"])
                    items.append(_fact_field(defs[item["key"]], item))
            sections.append({"title": sec["title"], "intro": sec.get("intro"), "items": items})
        pages.append({"id": page["id"], "title": page["title"], "intro": page.get("intro"), "sections": sections})
    extra = [_fact_field(d, None) for k, d in defs.items() if k not in placed]
    if extra:
        page = next(p for p in pages if p["id"] == layout.EXTRA_PAGE)
        page["sections"].append({"title": "More Questions", "intro": "Questions jobfill learned from forms.",
                                 "items": extra})
    return {"pages": pages}


def _items(sch: dict, type_: str):
    for page in sch["pages"]:
        for sec in page["sections"]:
            yield from (i for i in sec["items"] if i["type"] == type_)


# ---------------------------------------------------------------------- current values

def _shown(widget: str, value):
    """A stored value as the page's widget holds it."""
    if widget == "checkboxes":
        return [] if value is None else value if isinstance(value, list) else [value]
    if value is None:
        return ""
    if widget in ("yesno", "checkbox"):
        return ("yes" if value else "no") if widget == "yesno" else bool(value)
    if isinstance(value, list):
        return "\n".join(map(str, value)) if widget == "lines" else ", ".join(map(str, value))
    if widget == "date" and (m := re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", str(value))):
        y, mo, d = m.groups()    # stored 2025-09 / 2025-09-01, shown the US way the placeholders ask for
        return f"{mo}/{d}/{y}" if d else f"{mo}/{y}"
    return str(value)


def state(store, root: Path) -> dict:
    """The candidate's stored answers in the page's shape, plus setup progress."""
    sch = schema(store)
    have = store.facts()
    values = {}
    for f in _items(sch, "fact"):
        v = have.get(f["key"])
        # An empty list is an answer only where the page offers "None"; elsewhere it shows as blank.
        values[f["key"]] = [] if v == [] and f.get("none_label") else _shown(f["widget"], v)
    records = {}
    for block in _items(sch, "records"):
        records[block["record"]] = [{"_id": r["_id"], **{f["name"]: _shown(f["widget"], r.get(f["name"]))
                                                         for f in block["fields"]}}
                                    for r in store.records(block["record"])]
    files = {f["key"]: {"exists": _file(root, values[f["key"]]) is not None}
             for f in _items(sch, "fact") if f["widget"] == "file" and values[f["key"]]}
    s = store.status()
    rec = store.missing(level="recommended")
    return {"values": values, "records": records, "files": files, "status": {
        "setup_complete": s["setup_complete"], "missing_required": s["missing_required"],
        "missing_recommended": [f["key"] for f in rec["facts"] if f["setup"] == "recommended"]
                               + [f"record:{r['kind']}" for r in rec["records"] if r["setup"] == "recommended"]}}


def _file(root: Path, value: str) -> Path | None:
    p = Path(value)
    p = p if p.is_absolute() else Path(root) / p
    return p if p.is_file() else None


# ---------------------------------------------------------------------- saving

def _blank(value) -> bool:
    return value is None or value == [] or (isinstance(value, str) and not value.strip())


def _message(key: str, e: Exception) -> str:
    """A store error without its key prefix ("contact.email does not look like...")."""
    msg = str(e)
    for prefix in (f"{key}: ", f"{key} "):
        if msg.startswith(prefix):
            msg = msg[len(prefix):]
            break
    return msg[:1].upper() + msg[1:]


def _split(raw, sep: str) -> list[str]:
    parts = raw if isinstance(raw, list) else (str(raw).splitlines() if sep == "\n" else str(raw).split(sep))
    return [str(p).strip() for p in parts if str(p).strip()]


def _record_value(field: dict, raw):
    """One record field from the page: the value to store, or None for blank."""
    w = field["widget"]
    if w == "checkbox":
        return True if raw is True or str(raw).lower() in ("true", "on", "yes") else None
    if _blank(raw):
        return None
    if w == "yesno":
        return str(raw).lower() == "yes"
    if w in ("lines", "list", "checkboxes"):
        return _split(raw, "\n" if w == "lines" else ",") or None
    s = str(raw).strip()
    if w == "number":
        try:
            n = float(s.replace(",", ""))
        except ValueError:
            raise ValidationError("Enter a number.") from None
        return int(n) if n == int(n) else n
    if w == "date" and not ONGOING.match(s) and iso_date(s) is None:
        raise ValidationError("Enter a date such as 09/2025, Sep 2025 or 2025-09.")
    return s


def _plan_records(store, submission: dict, blocks: dict, submit: bool):
    """Record changes: (operations, errors, entries per kind afterwards).

    Each kind's list mirrors the cards on the page. A card sent as just ``{"_id": n}`` was not changed
    there, so a page left open never overwrites a record changed elsewhere since it loaded. Records
    are removed only when listed under ``submission["removed"]`` or emptied on the page; a record the
    page never showed (added elsewhere meanwhile) is kept.
    """
    submitted, removed_ids = submission.get("records") or {}, submission.get("removed") or {}
    ops, errors, counts = [], {}, {}
    for kind, block in blocks.items():
        current = store.records(kind)
        by_id = {r["_id"]: r for r in current}
        removed = {rid for rid in removed_ids.get(kind) or () if rid in by_id}
        shown_fields = {f["name"] for f in block["fields"]}
        added = 0
        for i, entry in enumerate(submitted.get(kind) or []):
            old = by_id.get(entry.get("_id"))
            if old is not None and set(entry) <= {"_id"}:
                continue                    # unchanged on the page
            data, bad = {}, False
            for f in block["fields"]:
                try:
                    v = _record_value(f, entry.get(f["name"]))
                except ValidationError as e:
                    errors[f"record:{kind}:{i}:{f['name']}"] = str(e)
                    bad = True
                    continue
                if v is not None:
                    data[f["name"]] = v
            if not data and not bad:
                if old is not None:         # every field emptied on the page: the entry goes
                    removed.add(old["_id"])
                continue                    # an empty new card: nothing to add
            for f in block["fields"]:
                name = f["name"]
                if name not in data and f"record:{kind}:{i}:{name}" not in errors and                         (name == block["key_field"] or (submit and f["required"])):
                    errors[f"record:{kind}:{i}:{name}"] = REQUIRED
                    bad = True
            if old is None:
                added += 1
            if bad:
                continue
            stored = {k: v for k, v in old.items() if not k.startswith("_")} if old else {}
            # Fields the page does not show (e.g. extra keys an import added) are kept as they are.
            merged = {**{k: v for k, v in stored.items() if k not in shown_fields}, **data}
            try:
                clean = store._clean_record(kind, merged)
            except ValidationError as e:
                errors[f"record:{kind}:{i}"] = str(e)
                continue
            if old is None:
                ops.append(("add", kind, clean))
            elif clean != stored:
                ops.append(("update", old["_id"], clean))
        ops += [("remove", rid) for rid in sorted(removed)]
        counts[kind] = len(current) - len(removed) + added
    return ops, errors, counts


def save(store, root: Path, submission: dict, *, submit: bool = False, source: str = "portal") -> dict:
    """Check the page's answers and store them; ``submit`` also requires every required field.

    ``submission``: ``{"facts": {key: value}, "records": {kind: [entry, ...]}, "removed": {kind: [id]}}``.
    The page sends only the facts the candidate changed; facts it does not name are left as stored.
    """
    sch = schema(store)
    facts = {f["key"]: f for f in _items(sch, "fact")}
    blocks = {b["record"]: b for b in _items(sch, "records")}
    have = store.facts()
    errors, after, fact_ops = {}, dict(have), []
    for key, raw in (submission.get("facts") or {}).items():
        f = facts.get(key)
        if f is None:
            continue
        if raw == [] and f.get("none_label"):
            value = []
        elif _blank(raw):
            if not _blank(have.get(key)):
                fact_ops.append((key, None))
                after.pop(key, None)
            continue
        else:
            if f["widget"] == "lines":
                raw = _split(raw, "\n")
            try:
                value = store.coerce(key, raw)
            except ValidationError as e:
                errors[f"fact:{key}"] = _message(key, e)
                continue
        if have.get(key) != value:
            fact_ops.append((key, value))
            after[key] = value
    record_ops, record_errors, counts = _plan_records(store, submission, blocks, submit)
    errors.update(record_errors)
    if submit:
        for key, f in facts.items():
            if _blank(after.get(key)) and f.get("default_from") and not _blank(after.get(f["default_from"])):
                fact_ops.append((key, after[f["default_from"]]))
                after[key] = after[f["default_from"]]
            if f["required"] and _blank(after.get(key)) and f"fact:{key}" not in errors:
                errors[f"fact:{key}"] = REQUIRED
        for kind, block in blocks.items():
            if block["required"] and not counts[kind]:
                errors[f"record:{kind}"] = f"Add at least one {block['entry'].lower()}."
    if errors:
        return {"ok": False, "errors": errors}
    for key, value in fact_ops:
        if value is None:
            store.unset_fact(key, source)
        else:
            store.set_fact(key, value, source)
    for op in record_ops:
        if op[0] == "add":
            store.add_record(op[1], op[2], source)
        elif op[0] == "update":
            store.update_record(op[1], op[2], merge=False, source=source)
        else:
            store.remove_record(op[1])
    changed = {"facts": len(fact_ops), "records": len(record_ops)}
    return {"ok": True, "changed": changed, "state": state(store, root)}


def save_upload(root: Path, key: str, filename: str, data: bytes) -> str:
    """Keep an attached file under documents/; returns the path to store (relative to ``root``).

    A file with the same name and other content is never replaced: the new one gets " (2)" etc.
    """
    folder = UPLOAD_FOLDERS.get(key)
    if folder is None:
        raise ValidationError(f"{key} does not take a file")
    name = re.sub(r"[^\w .()\-]+", "_", Path(filename or "").name).strip(" ._")
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_TYPES:
        raise ValidationError(f"Attach one of: {', '.join(sorted(UPLOAD_TYPES))}")
    if not data:
        raise ValidationError("The file is empty.")
    if len(data) > MAX_UPLOAD:
        raise ValidationError(f"The file is larger than {MAX_UPLOAD // (1024 * 1024)} MB.")
    target_dir = Path(root) / "documents" / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    stem, target, n = Path(name).stem, target_dir / name, 1
    while target.exists() and target.read_bytes() != data:
        n += 1
        target = target_dir / f"{stem} ({n}){suffix}"
    if not target.exists():
        target.write_bytes(data)
    return resumes.shown(target, root)
