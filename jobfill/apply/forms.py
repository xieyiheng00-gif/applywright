"""Read form fields from a page and type answers into them (Playwright, no AI)."""

from __future__ import annotations

import re
from pathlib import Path

from ..text import closest_options, names_same, normalize, search_queries

EXTRACT_JS = (Path(__file__).with_name("extract.js")).read_text(encoding="utf-8")

# Visible options of an open dropdown: the listbox the input controls, else any visible listbox.
# Options under a group heading read as "Group: Option"; each is tagged data-jobfill-opt=<n> so the
# n-th text can be clicked even when two options share a label.
OPTIONS_JS = r"""(id) => {
  const el = document.querySelector(`[data-jobfill-id="${id}"]`);
  const shown = (n) => n && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== "hidden";
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  let boxes = [];
  for (const attr of ["aria-controls", "aria-owns"]) {
    const ref = el && el.getAttribute(attr);
    if (ref && document.getElementById(ref)) boxes.push(document.getElementById(ref));
  }
  if (!boxes.length) boxes = Array.from(document.querySelectorAll("[role=listbox]")).filter(shown);
  let found = [];
  for (const b of boxes) found.push(...Array.from(b.querySelectorAll("[role=option]")).filter(shown));
  if (!found.length) found = Array.from(document.querySelectorAll("[role=option]")).filter(shown);
  document.querySelectorAll("[data-jobfill-opt]").forEach((o) => o.removeAttribute("data-jobfill-opt"));
  return found.map((o, i) => {
    o.setAttribute("data-jobfill-opt", String(i));
    const group = o.parentElement && o.parentElement.closest("[role=group], [class*='group' i]");
    const head = group && group.querySelector("[class*='group-heading' i], [class*='groupHeading'], [role=presentation]");
    const heading = head && !head.contains(o) && !head.querySelector("[role=option]") ? clean(head.textContent) : "";
    return heading ? `${heading}: ${clean(o.textContent)}` : clean(o.textContent);
  });
}"""

NOT_AN_OPTION = re.compile(r"^(no options?|no results?( found)?|loading\.*|type to search|start typing|searching\.*|"
                           r"no matches( found)?|select one|choose one|please select)$", re.I)

# The list a "Select One" button opens (aria-controls, else the visible listbox): every enabled option's
# text, scrolling through a long list in case it only renders the rows in view. null while it is not open.
LIST_JS = r"""async (id) => {
  const el = document.querySelector(`[data-jobfill-id="${id}"]`);
  const shown = (n) => n && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== "hidden";
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const ref = el && el.getAttribute("aria-controls");
  const list = (ref && document.getElementById(ref)) || Array.from(document.querySelectorAll("[role=listbox]")).find(shown);
  if (!shown(list)) return null;
  const read = () => Array.from(list.querySelectorAll("[role=option]"))
    .filter((o) => o.getAttribute("aria-disabled") !== "true").map((o) => clean(o.innerText || o.textContent)).filter(Boolean);
  const out = read();
  const start = list.scrollTop;
  for (let i = 0; i < 60 && list.scrollTop + list.clientHeight < list.scrollHeight - 1; i++) {
    list.scrollTop += list.clientHeight;
    await new Promise((r) => setTimeout(r, 100));
    for (const t of read()) if (!out.includes(t)) out.push(t);
  }
  list.scrollTop = start;
  return out;
}"""

# Find the option reading ``text`` in that list (scrolling until it is rendered), bring it into view and tag it
# data-jobfill-opt="pick" for the click.
MARK_JS = r"""async ([id, text]) => {
  const el = document.querySelector(`[data-jobfill-id="${id}"]`);
  const shown = (n) => n && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== "hidden";
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const ref = el && el.getAttribute("aria-controls");
  const list = (ref && document.getElementById(ref)) || Array.from(document.querySelectorAll("[role=listbox]")).find(shown);
  if (!shown(list)) return false;
  document.querySelectorAll("[data-jobfill-opt]").forEach((o) => o.removeAttribute("data-jobfill-opt"));
  const pause = () => new Promise((r) => setTimeout(r, 120));
  const find = () => Array.from(list.querySelectorAll("[role=option]")).find((o) => clean(o.innerText || o.textContent) === text);
  list.scrollTop = 0;
  await pause();
  for (let i = 0; i < 60; i++) {
    const hit = find();
    if (hit) {
      hit.scrollIntoView({block: "nearest"});
      await pause();
      const now = find();   // a list that renders only the rows in view redraws them on that scroll
      if (now) { now.setAttribute("data-jobfill-opt", "pick"); return true; }
      continue;
    }
    if (list.scrollTop + list.clientHeight >= list.scrollHeight - 1) return false;
    list.scrollTop += list.clientHeight;
    await pause();
  }
  return false;
}"""

class NoExactChoice(Exception):
    """The list has no option that is exactly the answer (a school, a field of study). jobfill leaves the field
    empty rather than pick a look-alike: the candidate picks, and the pick is remembered for other forms."""

    def __init__(self, value: str, offered: list[str]):
        super().__init__(f"no option is exactly {value!r}")
        self.value, self.offered = value, offered


INPUT_TYPES = {"text": "text", "email": "email", "tel": "tel", "url": "url", "number": "number", "date": "date",
               "search": "text", "month": "text", "password": "password"}


def sel(field_id: str) -> str:
    return f'[data-jobfill-id="{field_id.removeprefix("g-")}"]'


def extract(frame, root_selector: str | None = None, read_options: bool = True,
            known: set[str] | None = None) -> list[dict]:
    """Every field on the page. ``read_options`` opens dropdowns whose options only exist while open
    (comboboxes, "Select One" buttons), except the ``known`` ones, already read in an earlier pass."""
    fields = frame.evaluate(EXTRACT_JS, root_selector)
    fields = [f for f in fields if f.get("type") != "password"]
    if read_options:
        for f in fields:
            if f.get("options") or f["id"] in (known or ()):
                continue
            if f["kind"] == "combobox":
                f["options"] = combobox_options(frame, f["id"])
            elif f["kind"] == "dropdown":
                f["options"] = dropdown_options(frame, f)
    return fields


def combobox_options(frame, field_id: str) -> list[str]:
    loc = frame.locator(sel(field_id))
    try:
        loc.click(timeout=3000)
        frame.wait_for_timeout(350)
        opts = frame.evaluate(OPTIONS_JS, field_id)
        if not opts:
            loc.press("ArrowDown")
            frame.wait_for_timeout(350)
            opts = frame.evaluate(OPTIONS_JS, field_id)
        loc.press("Escape")
    except Exception:
        return []
    out = []
    for o in opts:
        if o and not NOT_AN_OPTION.match(o) and o not in out:
            out.append(o)
    return out


def _list_options(frame, field_id: str, wait_ms: int = 3000) -> list[str]:
    """The open list's options, once it shows some (the site may fill it a moment after it opens)."""
    for _ in range(max(1, wait_ms // 200)):
        texts = frame.evaluate(LIST_JS, field_id)
        texts = [t for t in (texts or []) if not NOT_AN_OPTION.match(t)]
        if texts:
            return list(dict.fromkeys(texts))
        frame.wait_for_timeout(200)
    return []


def _button(frame, f: dict):
    """The dropdown's button; found again by its id if the site re-rendered it (losing jobfill's tag)."""
    loc = frame.locator(sel(f["id"]))
    if not loc.count() and f.get("html_id"):
        loc = frame.locator(f'[id="{f["html_id"]}"]')
    return loc.first


def _close_list(frame, f: dict) -> None:
    """Close the list (left open, it covers the next field): Escape, else the button again."""
    for close in ("Escape", "click"):
        button = _button(frame, f)
        if button.get_attribute("aria-expanded") != "true":
            return
        if close == "Escape":
            button.press("Escape")
        else:
            button.click(timeout=3000)
        frame.wait_for_timeout(250)


def dropdown_options(frame, f: dict) -> list[str]:
    """Open a "Select One" button's list, read its options, and close it again without choosing."""
    try:
        button = _button(frame, f)
        if button.get_attribute("aria-expanded") != "true":
            button.click(timeout=3000)
        opts = _list_options(frame, f["id"])
        _close_list(frame, f)
        return opts
    except Exception:
        return []


def to_resolver_field(f: dict) -> dict:
    kind = f["kind"]
    if kind == "input":
        rtype = INPUT_TYPES.get(f["type"], "text")
    elif kind == "select":
        rtype = "multiselect" if f.get("multiple") else "select"
    elif kind in ("combobox", "dropdown"):
        rtype = "select" if f.get("options") else "text"
    elif kind == "selectinput":
        rtype = "text"    # the plain answer ("LinkedIn"); the adapter finds it in the menu, at any level
    elif kind == "buttons":
        rtype = "radio"   # toggle-button group behaves like a radio group
    else:
        rtype = kind  # textarea, radio, checkbox, file
    out = {"id": f["id"], "label": f["label"], "type": rtype, "required": bool(f.get("required"))}
    if kind == "selectinput":
        out["picks"] = True   # a list answer (skills) stays a list: each entry is searched and picked on its own
    if f.get("options") and kind != "input":
        out["options"] = f["options"]
    for k in ("max_chars", "section", "index", "placeholder", "description"):
        if f.get(k) not in (None, ""):
            out[k] = f[k]
    if rtype == "date":
        out["format"] = "YYYY-MM-DD"       # <input type=date> always takes ISO
    elif f.get("type") == "month":
        out["format"] = "YYYY-MM"          # <input type=month>
    return out


def is_empty(f: dict) -> bool:
    v = f.get("value")
    return v in (None, "", [], False)


def _best_option(texts: list[str], wanted: str) -> int | None:
    w = normalize(wanted)
    norms = [normalize(t) for t in texts]
    for i, n in enumerate(norms):
        if n == w:
            return i
    for i, n in enumerate(norms):
        if n.startswith(w) or w.startswith(n):
            return i
    words = [x for x in w.split() if len(x) > 1]
    if words:
        for i, n in enumerate(norms):
            if all(x in n.split() for x in words):
                return i
        for i, n in enumerate(norms):
            if words[0] in n.split():
                return i
    return None


def _fill_combobox(frame, loc, field_id: str, value) -> tuple[bool, str]:
    for v in (value if isinstance(value, list) else [value]):
        v = str(v)
        loc.click(timeout=5000)
        try:
            loc.fill("")
        except Exception:
            pass
        # "Job Board: LinkedIn" is a grouped option: the menu filters on the option's own text.
        loc.type(v.rsplit(": ", 1)[-1][:60], delay=15)
        texts: list[str] = []
        for _ in range(16):  # up to ~4s for async (search-as-you-type) dropdowns
            frame.wait_for_timeout(250)
            texts = frame.evaluate(OPTIONS_JS, field_id)
            if any(not NOT_AN_OPTION.match(t) for t in texts):
                break
        i = _best_option([t if not NOT_AN_OPTION.match(t) else "" for t in texts], v)
        if i is None:
            loc.press("Escape")
            return False, f"no dropdown option matches {v!r}" + (f" (saw {texts[:5]})" if texts else "")
        try:
            frame.locator(f'[data-jobfill-opt="{i}"]').click(timeout=3000)
        except Exception:
            loc.press("Enter")
        frame.wait_for_timeout(150)
    return True, ""


def search_combobox(frame, field_id: str, value: str, alternatives=()) -> tuple[str | None, list[str]]:
    """Find ``value`` in a search-as-you-type list that shows only its first entries when opened (a catalogue
    of thousands of schools): type the candidate's earlier picks for it, the name, then its distinctive words,
    and click a result only if it *is* the name or such a pick. "Wisconsin" also shows "...-Milwaukee"; that
    is never picked. Returns (the option picked or None, every option the searches showed)."""
    loc = frame.locator(sel(field_id))
    is_it = lambda t: names_same(t, value) or any(normalize(t) == normalize(a) for a in alternatives)
    seen: list[str] = []
    try:
        for q in [*alternatives, *search_queries(value)]:
            loc.click(timeout=5000)
            loc.fill("")
            frame.wait_for_timeout(250)
            before = frame.evaluate(OPTIONS_JS, field_id)
            loc.type(q[:60], delay=15)
            texts = before
            for n in range(16):  # up to ~4s for the results of this search (not the list before it)
                frame.wait_for_timeout(250)
                texts = frame.evaluate(OPTIONS_JS, field_id)
                if texts != before and (any(not NOT_AN_OPTION.match(t) for t in texts) or n >= 5):
                    break        # results, or still nothing after ~1.5s: this search finds nothing
            texts = [t if not NOT_AN_OPTION.match(t) else "" for t in texts]
            seen += [t for t in texts if t and t not in seen]
            i = next((k for k, t in enumerate(texts) if t and is_it(t)), None)
            if i is not None:
                frame.locator(f'[data-jobfill-opt="{i}"]').click(timeout=3000)
                frame.wait_for_timeout(150)
                return texts[i], seen
        loc.press("Escape")
        loc.fill("")
    except Exception:
        pass
    return None, seen


def _fill_dropdown(frame, f: dict, value) -> tuple[bool, str]:
    """Pick ``value`` in a "Select One" button's list: open it, click the option, and check that the button
    now shows it (only then has the site taken the choice)."""
    want = str(value[0] if isinstance(value, list) and value else value)
    shows = lambda: _button(frame, f).inner_text().strip()
    if normalize(shows()) == normalize(want):
        return True, ""
    try:
        button = _button(frame, f)
        if button.get_attribute("aria-expanded") != "true":
            button.click(timeout=5000)
        texts = _list_options(frame, f["id"])
        if not texts:
            return False, "its list did not open"
        i = next((k for k, t in enumerate(texts) if names_same(t, want)), None)
        for alt in ([] if i is not None else f.get("alternatives") or []):   # picked for it on an earlier form
            i = next((k for k, t in enumerate(texts) if normalize(t) == normalize(alt)), None)
            if i is not None:
                break
        if i is None:
            raise NoExactChoice(want, closest_options(want, texts))
        target = texts[i]
        # The list is rebuilt as it loads: find the option again right before each click.
        for _ in range(3):
            if frame.evaluate(MARK_JS, [f["id"], target]):
                try:
                    frame.locator('[data-jobfill-opt="pick"]').click(timeout=2000)
                    break
                except Exception:
                    pass
            frame.wait_for_timeout(300)
        else:
            return False, f"could not click {target!r} in its list"
        for _ in range(15):
            frame.wait_for_timeout(200)
            if normalize(shows()) == normalize(target):
                return True, ""
        return False, f"clicked {target!r}, but the field still shows {shows()!r}"
    finally:
        try:
            _close_list(frame, f)
        except Exception:
            pass


def _click_choice(frame, ref: str) -> bool:
    loc = frame.locator(f'[data-jobfill-id="{ref}"]')
    try:
        loc.check(timeout=3000, force=True)
    except Exception:
        pass
    if loc.is_checked():
        return True
    # Custom-styled inputs: click the label the user would click.
    frame.evaluate("""(ref) => { const e = document.querySelector(`[data-jobfill-id="${ref}"]`);
        const l = (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)) || e.closest('label');
        (l || e).click(); }""", ref)
    return loc.is_checked()


def fill(frame, f: dict, value, *, root: Path, after=None, custom=None) -> tuple[bool, str]:
    """Put ``value`` (as returned by the resolver) into field ``f``.

    ``custom(frame, field, value)`` lets an adapter take over a widget it knows; it returns
    ``(ok, note)``, or None to use the generic handling.
    """
    kind = f["kind"]
    loc = frame.locator(sel(f["id"]))
    try:
        if custom is not None:
            handled = custom(frame, f, value)
            if handled is not None:
                return handled
        if kind == "file":
            path = Path(str(value))
            if not path.is_absolute():
                path = root / path
            if not path.exists():
                return False, f"file not found: {path}"
            loc.set_input_files(str(path))
        elif kind == "select":
            values = [str(v) for v in (value if isinstance(value, list) else [value])]
            if f.get("option_index") and all(v in f["options"] for v in values):
                # By position: grouped options ("Job Board: LinkedIn") show only "LinkedIn" in the page.
                index = [f["option_index"][f["options"].index(v)] for v in values]
                loc.select_option(index=index if f.get("multiple") else index[0], timeout=5000)
            else:
                loc.select_option(label=values if f.get("multiple") else values[0], timeout=5000)
        elif kind == "buttons":
            refs = dict((label, ref) for label, ref in f["option_refs"])
            if str(value) not in refs:
                return False, f"no option {value!r}"
            button = frame.locator(f'[data-jobfill-id="{refs[str(value)]}"]')
            if button.get_attribute("aria-pressed") != "true":
                button.click(timeout=5000)
            if button.get_attribute("aria-pressed") != "true":
                return False, f"could not press {value!r}"
        elif kind in ("radio", "checkbox") and f.get("option_refs"):
            wanted = value if isinstance(value, list) else [value]
            refs = dict((label, ref) for label, ref in f["option_refs"])
            missing = [w for w in wanted if w not in refs]
            if missing:
                return False, f"no option {missing}"
            for w in wanted:
                if not _click_choice(frame, refs[w]):
                    return False, f"could not select {w!r}"
        elif kind == "checkbox":
            on = value is True or str(value).strip().lower() in ("yes", "true", "1")
            if on != loc.is_checked():
                if not _click_choice(frame, f["id"]) and on:
                    return False, "could not tick the box"
        elif kind == "combobox":
            ok, note = _fill_combobox(frame, loc, f["id"], value)
            if not ok:
                return ok, note
        elif f.get("spin"):
            # A spin button (Workday's Month / Year) takes typed keys; a value set from outside is reset.
            loc.click(timeout=5000)
            loc.press("Control+A")
            loc.type(str(value), delay=60)
            frame.wait_for_timeout(150)
            got = loc.input_value()
            if got.lstrip("0") != str(value).lstrip("0"):
                return False, f"typed {value!r}, but it shows {got!r}"
        elif kind == "dropdown":
            ok, note = _fill_dropdown(frame, f, value)
            if not ok:
                return ok, note
        elif kind == "selectinput":
            # Typing alone is only a search that Workday throws away; never report it as filled.
            return False, "this menu needs the Workday handler (only a clicked choice counts)"
        else:
            loc.fill(str(value), timeout=5000)
            loc.dispatch_event("blur")
        if after:
            after(frame, f, value)
        return True, ""
    except NoExactChoice:
        raise   # not a failure to retry: a question for the candidate
    except Exception as e:
        return False, str(e).splitlines()[0][:200]
