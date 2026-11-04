// jobfill setup portal: renders the pages /api/form describes and saves through /api/save.
// The server checks and stores every value (jobfill.portal.form); this page only checks required fields
// so it can stop on a page the way application portals do.
"use strict";
(() => {
  const TOKEN = new URLSearchParams(location.search).get("t") || "";
  const app = document.getElementById("app");
  const REQUIRED = "This field is required.";
  // Browser autofill hints for the facts browsers know.
  const AUTOCOMPLETE = {
    "identity.first_name": "given-name", "identity.middle_name": "additional-name", "identity.last_name": "family-name",
    "identity.preferred_name": "nickname", "identity.date_of_birth": "bday", "contact.email": "email",
    "contact.phone": "tel-national", "contact.phone_country_code": "tel-country-code", "contact.website": "url",
    "address.line1": "address-line1", "address.line2": "address-line2", "address.city": "address-level2",
    "address.state": "address-level1", "address.postal_code": "postal-code", "address.country": "country-name",
  };

  const S = {
    pages: [], facts: {}, blocks: {}, where: {}, values: {}, records: {}, files: {}, status: {},
    page: 0, visited: new Set(), errors: {}, dirty: false, busy: false, savedAt: null, done: null, keepOpen: false,
    database: "", touched: new Set(), removed: {}, problem: null,
  };

  // ---------------------------------------------------------------- helpers
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "value" || k === "checked" || k === "disabled") el[k] = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
    return el;
  }

  async function api(path, opts = {}) {
    let res;
    try {
      res = await fetch(path, { ...opts, headers: { "X-Jobfill-Token": TOKEN, ...(opts.headers || {}) } });
    } catch (e) {
      throw Object.assign(new Error("jobfill is not running"), { stopped: true });
    }
    let data = {};
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
    return data;
  }

  const domId = (prefix, ...parts) => prefix + "-" + parts.join("-").replace(/[^\w-]/g, "-");
  const factId = key => domId("f", key);
  const recordId = (kind, i, name) => domId("r", kind, i, name);

  function isBlank(v, item) {
    if (item && item.none_label && Array.isArray(v)) return false;   // "None" is an answer
    if (v == null || v === false) return true;
    if (Array.isArray(v)) return v.length === 0;
    return typeof v === "string" && !v.trim();
  }
  const entryBlank = (block, e) => block.fields.every(f => isBlank(e[f.name], f));
  const newEntry = block => Object.fromEntries(block.fields.map(f =>
    [f.name, f.widget === "checkboxes" ? [] : f.widget === "checkbox" ? false : ""]));

  function display(item, v) {
    if (item.none_label && Array.isArray(v)) return item.none_label;
    if (isBlank(v, item)) return "";
    const label = x => ((item.options || []).find(o => o.value === x) || {}).label || x;
    if (Array.isArray(v)) return v.map(label).join(", ");
    if (item.widget === "select" || item.widget === "yesno") return label(v);
    if (item.widget === "checkbox") return "Yes";
    if (item.widget === "file") return String(v).split("/").pop();
    return String(v);
  }
  const clip = (s, n = 220) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

  // ---------------------------------------------------------------- loading and state
  async function load() {
    try {
      const data = await api("/api/form");
      S.keepOpen = !!data.keep_open;
      S.database = data.database || "data/jobfill.db";
      S.pages = [...data.schema.pages, { id: "review", title: "Review", sections: [] }];
      S.pages.forEach((page, p) => page.sections.forEach(sec => sec.items.forEach(item => {
        if (item.type === "fact") { S.facts[item.key] = item; S.where["fact:" + item.key] = p; }
        else { S.blocks[item.record] = item; S.where["record:" + item.record] = p; }
      })));
      applyState(data.state);
      const fromHash = S.pages.findIndex(p => "#" + p.id === location.hash);
      S.page = fromHash >= 0 ? fromHash : 0;
      render();
    } catch (e) {
      app.replaceChildren(h("div", { class: "card" },
        h("h2", { text: "The portal could not load" }), h("p", { text: e.message }),
        h("p", { class: "muted" }, "Open the link that ", h("code", { text: "python -m jobfill portal" }),
          " printed (it carries the key), or run the command again.")));
    }
  }

  function applyState(st) {
    // Keep cards the candidate added but has not filled in yet: the server drops empty ones.
    const pending = {};
    for (const [kind, list] of Object.entries(S.records)) {
      pending[kind] = list.filter(e => !e._id && entryBlank(S.blocks[kind], e));
    }
    S.values = st.values;
    S.files = st.files || {};
    S.status = st.status;
    S.records = {};
    for (const [kind, list] of Object.entries(st.records)) S.records[kind] = [...list, ...(pending[kind] || [])];
  }

  // ---------------------------------------------------------------- required checks
  function pageErrors(page) {
    const errs = {};
    for (const sec of page.sections) {
      for (const item of sec.items) {
        if (item.type === "fact") {
          if (item.required && isBlank(S.values[item.key], item)) errs["fact:" + item.key] = REQUIRED;
          continue;
        }
        const list = S.records[item.record] || [];
        const filled = list.filter(e => !entryBlank(item, e));
        if (item.required && !filled.length) errs["record:" + item.record] = `Add at least one ${item.entry.toLowerCase()}.`;
        list.forEach((e, i) => {
          if (entryBlank(item, e)) return;
          for (const f of item.fields) {
            if ((f.required || f.name === item.key_field) && isBlank(e[f.name], f)) {
              errs[`record:${item.record}:${i}:${f.name}`] = REQUIRED;
            }
          }
        });
      }
    }
    return errs;
  }
  const formPages = () => S.pages.slice(0, -1);
  const pageOf = errKey => S.where[errKey.split(":").slice(0, 2).join(":")];

  function errorLabel(errKey) {
    const [type, a, i, name] = errKey.split(":");
    if (type === "fact") return (S.facts[a] || {}).label || a;
    const block = S.blocks[a];
    if (!block) return a;
    if (i === undefined) return block.title;
    const f = block.fields.find(x => x.name === name);
    return `${block.entry} ${Number(i) + 1}` + (f ? `: ${f.label}` : "");
  }

  function focusError(errKey) {
    const [type, a, i, name] = errKey.split(":");
    const id = type === "fact" ? factId(a) : name ? recordId(a, i, name) : i !== undefined ? domId("entry", a, i)
      : domId("records", a);
    const el = document.getElementById(id) || document.querySelector(`[data-err="${CSS.escape(errKey)}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    const target = el.matches("input,select,textarea,button") ? el : el.querySelector("input,select,textarea,button");
    if (target) setTimeout(() => target.focus({ preventScroll: true }), 250);
  }

  // Clear a field's error as soon as it is answered (no full re-render, so typing keeps focus).
  function touched(errKey) {
    S.dirty = true;
    setSaveState();
    if (!S.errors[errKey]) return;
    delete S.errors[errKey];
    const wrap = document.querySelector(`[data-err="${CSS.escape(errKey)}"]`);
    if (wrap) {
      wrap.classList.remove("invalid");
      const msg = wrap.querySelector(":scope > .error");
      if (msg) msg.textContent = "";
    }
  }

  // ---------------------------------------------------------------- rendering
  function render() {
    const page = S.pages[S.page];
    history.replaceState(null, "", location.pathname + location.search + "#" + page.id);
    document.title = `${S.done ? "Submitted" : page.title} - Candidate Profile - jobfill`;
    const body = S.done ? doneView() : h("div", { class: "card", id: "card" }, problemBanner(), ...pageView(page));
    app.replaceChildren(hero(), S.done ? "" : stepper(), body, toastEl);
  }

  function refresh() {
    const y = window.scrollY;
    render();
    window.scrollTo(0, y);
  }

  function hero() {
    const missing = formPages().reduce((n, p) => n + Object.keys(pageErrors(p)).length, 0);
    return h("section", { class: "hero" }, h("div", { class: "hero-inner" },
      h("p", { class: "eyebrow", text: "Candidate setup" }),
      h("h1", { text: "Candidate Profile" }),
      h("ul", { class: "chips" },
        h("li", { class: "chip", text: "Fill once, reused on every application" }),
        h("li", { class: "chip", text: `Saved to ${S.database}` }),
        S.done || !missing ? h("li", { class: "chip good", text: "All required questions answered" })
          : h("li", { class: "chip warn", text: `${missing} required ${missing === 1 ? "item" : "items"} left` }))));
  }

  function stepper() {
    const ol = h("ol", { class: "steps" });
    S.pages.forEach((p, i) => {
      const last = i === S.pages.length - 1;
      const complete = !last && !Object.keys(pageErrors(p)).length;
      const seen = S.visited.has(i) || pageHasAnswers(p);
      const cls = i === S.page ? "current" : complete && seen ? "done" : S.visited.has(i) && !last ? "attention" : "";
      ol.append(h("li", { class: `step ${cls}` },
        h("button", { type: "button", onclick: () => go(i), "aria-current": i === S.page ? "step" : null,
                      title: p.title },
          h("span", { class: "dot", text: cls === "done" ? "✓" : String(i + 1) }),
          h("span", { class: "step-label", text: p.title }))));
    });
    return h("nav", { class: "stepper", "aria-label": "Application steps" },
      h("div", { class: "stepper-meta", text: `Step ${S.page + 1} of ${S.pages.length}: ${S.pages[S.page].title}` }), ol);
  }

  function pageHasAnswers(page) {
    return page.sections.some(sec => sec.items.some(item => item.type === "fact"
      ? !isBlank(S.values[item.key], item) : (S.records[item.record] || []).length > 0));
  }

  function pageView(page) {
    if (page.id === "review") return reviewView();
    const out = [h("div", { class: "page-head" }, h("h2", { text: page.title }),
      h("p", { class: "legend" }, h("span", { class: "req", text: "*" }), " Indicates a required field"))];
    if (page.intro) out.push(h("p", { class: "page-intro", text: page.intro }));
    const banner = errorBanner(Object.keys(S.errors).filter(k => pageOf(k) === S.page));
    if (banner) out.push(banner);
    for (const sec of page.sections) out.push(sectionView(sec));
    out.push(actions());
    return out;
  }

  function errorBanner(keys) {
    if (!keys.length) return null;
    return h("div", { class: "banner", role: "alert", id: "banner" },
      h("h3", { text: `Errors Found (${keys.length})` }),
      h("ul", {}, keys.map(k => h("li", {},
        h("button", { type: "button", class: "link", onclick: () => goToError(k) },
          `${errorLabel(k)}: ${S.errors[k]}`)))));
  }

  function goToError(errKey) {
    const p = pageOf(errKey);
    if (p !== undefined && p !== S.page) { S.page = p; render(); }
    focusError(errKey);
  }

  function sectionView(sec) {
    const el = h("section", { class: "section" }, h("h3", { text: sec.title }));
    if (sec.intro) el.append(h("p", { class: "section-intro", text: sec.intro }));
    let grid = null;
    for (const item of sec.items) {
      if (item.type === "records") { grid = null; el.append(recordsView(item)); continue; }
      if (!grid) { grid = h("div", { class: "grid" }); el.append(grid); }
      grid.append(field({
        id: factId(item.key), item, errKey: "fact:" + item.key, value: S.values[item.key],
        set: v => { S.values[item.key] = v; S.touched.add(item.key); touched("fact:" + item.key); },
        file: S.files[item.key], autocomplete: AUTOCOMPLETE[item.key],
      }));
    }
    return el;
  }

  // One question: label, help, the control for its widget, and its error line.
  function field(o) {
    const { id, item, errKey } = o;
    const err = S.errors[errKey];
    const helpId = item.help ? id + "-help" : null;
    const describedBy = [helpId, err ? id + "-err" : null].filter(Boolean).join(" ") || null;
    const wrap = h("div", { class: `field${item.wide ? " wide" : ""}${err ? " invalid" : ""}`, "data-err": errKey });
    const star = item.required ? h("span", { class: "req", "aria-hidden": "true", text: " *" }) : null;
    if (item.widget !== "checkbox") {
      const grouped = item.widget === "yesno" || item.widget === "checkboxes" || item.widget === "file";
      wrap.append(grouped ? h("div", { class: "label", id: id + "-label" }, item.label, star)
                          : h("label", { class: "label", for: id }, item.label, star));
      if (item.help) wrap.append(h("p", { class: "help", id: helpId, text: item.help }));
    }
    wrap.append(control(o, { describedBy, invalid: !!err }));
    if (item.widget === "checkbox" && item.help) wrap.append(h("p", { class: "help", id: helpId, text: item.help }));
    wrap.append(h("p", { class: "error", id: id + "-err", text: err || "" }));
    return wrap;
  }

  function control(o, a) {
    const { id, item, set } = o;
    const value = o.value;
    const common = { id, "aria-required": item.required ? "true" : null, "aria-invalid": a.invalid ? "true" : null,
                     "aria-describedby": a.describedBy };
    switch (item.widget) {
      case "textarea":
      case "lines": {
        const counter = item.widget === "textarea" ? h("p", { class: "counter" }) : null;
        const count = v => { if (counter) { const n = v.trim() ? v.trim().split(/\s+/).length : 0; counter.textContent = `${n} ${n === 1 ? "word" : "words"}`; } };
        const ta = h("textarea", { ...common, rows: item.widget === "lines" ? 4 : 5, value: value || "",
                                   placeholder: item.placeholder,
                                   oninput: e => { set(e.target.value); count(e.target.value); } });
        count(value || "");
        return counter ? h("div", {}, ta, counter) : ta;
      }
      case "select":
        return h("select", { ...common, onchange: e => set(e.target.value) },
          h("option", { value: "", text: "Select One" }),
          (item.options || []).map(opt => h("option", { value: opt.value, text: opt.label, selected: opt.value === value ? true : null })));
      case "yesno": {
        // Optional yes/no questions can be cleared again (a radio button cannot be unticked).
        const clear = item.required ? null : h("button", { type: "button", class: "link", text: "Clear", hidden: !value,
          onclick: () => { set(""); group.querySelectorAll("input").forEach(r => { r.checked = false; }); clear.hidden = true; } });
        const group = h("div", { class: "choices inline", role: "radiogroup", "aria-labelledby": id + "-label",
                                 ...ariaOnly(common) },
          (item.options || []).map((opt, n) => h("label", { class: "choice" },
            h("input", { type: "radio", name: id, id: n === 0 ? id : null, value: opt.value, checked: value === opt.value,
                         onchange: () => { set(opt.value); if (clear) clear.hidden = false; } }),
            h("span", { text: opt.label }))),
          clear);
        return group;
      }
      case "checkboxes": {
        let chosen = Array.isArray(value) ? [...value] : [];
        const many = (item.options || []).length > 6;
        const group = h("div", { class: `choices${many ? " columns" : ""}`, role: "group", "aria-labelledby": id + "-label",
                                 ...ariaOnly(common) },
          (item.options || []).map((opt, n) => h("label", { class: "choice" },
            h("input", { type: "checkbox", id: n === 0 ? id : null, value: opt.value, checked: chosen.includes(opt.value),
                         onchange: e => {
                           // Values the page has no box for (set by the agent) are kept.
                           chosen = chosen.filter(v => v !== opt.value);
                           if (e.target.checked) {
                             // "I don't wish to answer" excludes every other choice, and the other way round.
                             chosen = opt.value === "decline" ? ["decline"] : [...chosen.filter(v => v !== "decline"), opt.value];
                           }
                           group.querySelectorAll("input").forEach(box => { box.checked = chosen.includes(box.value); });
                           set([...chosen]);
                         } }),
            h("span", { text: opt.label }))));
        return group;
      }
      case "checkbox":
        return h("label", { class: "choice single" },
          h("input", { type: "checkbox", ...common, checked: !!value, onchange: e => set(e.target.checked) }),
          h("span", { text: item.label }));
      case "file":
        return fileControl(o, common);
      case "list":
        if (item.none_label) {
          const none = Array.isArray(value);
          const text = h("input", { type: "text", ...common, value: none ? "" : value || "", disabled: none,
                                    placeholder: item.placeholder, oninput: e => set(e.target.value) });
          return h("div", { class: "with-none" }, text,
            h("label", { class: "choice" },
              h("input", { type: "checkbox", checked: none, onchange: e => {
                set(e.target.checked ? [] : "");
                text.value = "";
                text.disabled = e.target.checked;
              } }),
              h("span", { text: item.none_label })));
        }
        // falls through: a comma-separated text box
      default: {
        const type = { email: "email", tel: "tel", url: "url" }[item.widget] || "text";
        return h("input", { type, ...common, value: value || "", placeholder: item.placeholder,
                            inputmode: item.widget === "number" ? "decimal" : null,
                            autocomplete: o.autocomplete || null, spellcheck: type === "text" ? null : "false",
                            oninput: e => set(e.target.value) });
      }
    }
  }
  const ariaOnly = c => ({ "aria-required": c["aria-required"], "aria-invalid": c["aria-invalid"],
                           "aria-describedby": c["aria-describedby"] });

  function fileControl(o, common) {
    const { id, item, set, value } = o;
    const input = h("input", { type: "file", accept: item.accept, hidden: true,
                               onchange: e => e.target.files[0] && upload(e.target.files[0]) });
    const zone = h("div", { class: "drop", role: "group", "aria-labelledby": id + "-label", ...ariaOnly(common) });
    async function upload(file) {
      zone.replaceChildren(h("span", { class: "drop-hint", text: `Uploading ${file.name}...` }));
      try {
        const res = await api(`/api/upload?key=${encodeURIComponent(item.key)}`, {
          method: "POST", body: file, headers: { "X-Filename": encodeURIComponent(file.name) } });
        S.files[item.key] = { exists: true };
        set(res.path);
        toast(`Attached ${res.path.split("/").pop()}`);
      } catch (e) {
        toast(e.message, true);
      }
      refresh();
    }
    const missing = value && o.file && !o.file.exists;
    zone.append(
      value ? h("div", { class: "drop-file" }, String(value).split("/").pop(),
                h("small", { class: missing ? "missing" : null, text: missing ? `Not found: ${value}` : value }))
            : h("span", { class: "drop-hint", text: "Attach a file, or drag and drop it here" }),
      h("div", { class: "drop-actions" },
        h("button", { type: "button", class: "btn secondary", id, text: value ? "Replace" : "Attach",
                      onclick: () => input.click() }),
        value ? h("button", { type: "button", class: "link danger", text: "Remove",
                              onclick: () => { set(""); refresh(); } }) : null),
      input);
    zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", e => {
      e.preventDefault();
      zone.classList.remove("over");
      if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
    });
    return zone;
  }

  function recordsView(block) {
    const list = S.records[block.record] || (S.records[block.record] = []);
    const errKey = "record:" + block.record;
    const err = S.errors[errKey];
    const wrap = h("div", { class: `records${err ? " invalid" : ""}`, id: domId("records", block.record), "data-err": errKey });
    if (block.help) wrap.append(h("p", { class: "help", text: block.help }));
    if (block.required) {
      wrap.append(h("p", { class: "block-note" }, h("span", { class: "req", text: "* " }),
        `At least one ${block.entry.toLowerCase()} is required.`));
    }
    list.forEach((entry, i) => wrap.append(entryView(block, list, entry, i)));
    wrap.append(h("p", { class: "error", text: err || "" }));
    wrap.append(h("button", { type: "button", class: "btn add", text: list.length ? `Add Another ${block.entry}` : `Add ${block.entry}`,
      onclick: () => {
        list.push(newEntry(block));
        S.dirty = true;
        delete S.errors[errKey];
        refresh();
        const first = document.getElementById(recordId(block.record, list.length - 1, block.fields[0].name));
        if (first) { first.scrollIntoView({ block: "center" }); first.focus({ preventScroll: true }); }
      } }));
    return wrap;
  }

  function entryView(block, list, entry, i) {
    const kind = block.record;
    const err = S.errors[`record:${kind}:${i}`];
    const card = h("div", { class: "entry", id: domId("entry", kind, i), "data-err": `record:${kind}:${i}` },
      h("div", { class: "entry-head" }, h("h4", { text: `${block.entry} ${i + 1}` }),
        h("button", { type: "button", class: "link danger", text: "Delete", onclick: () => {
          list.splice(i, 1);
          if (entry._id) (S.removed[kind] = S.removed[kind] || []).push(entry._id);
          S.dirty = true;
          // Errors are numbered by position, which just shifted.
          for (const k of Object.keys(S.errors)) if (k.startsWith(`record:${kind}:`)) delete S.errors[k];
          refresh();
          toast(`${block.entry} ${i + 1} deleted.`, false, ["Undo", () => {
            list.splice(i, 0, entry);
            if (entry._id) S.removed[kind] = S.removed[kind].filter(id => id !== entry._id);
            refresh();
          }]);
        } })));
    if (err) card.append(h("p", { class: "error", role: "alert", text: err }));
    const grid = h("div", { class: "grid" });
    for (const f of block.fields) {
      const errKey = `record:${kind}:${i}:${f.name}`;
      grid.append(field({ id: recordId(kind, i, f.name), item: { ...f, required: f.required || f.name === block.key_field },
                          errKey, value: entry[f.name],
                          set: v => { entry[f.name] = v; entry._changed = true; touched(errKey); } }));
    }
    card.append(grid);
    return card;
  }

  function actions() {
    const review = S.page === S.pages.length - 1;
    return h("div", { class: "actions" },
      S.page > 0 ? h("button", { type: "button", class: "btn secondary", text: "Back", onclick: () => go(S.page - 1) }) : h("span"),
      h("div", { class: "actions-right" },
        h("span", { class: "save-state", id: "save-state", "aria-live": "polite", text: saveStateText() }),
        review ? null : h("button", { type: "button", class: "btn secondary", text: "Save for Later",
                                      onclick: () => persist(false, true) }),
        review ? h("button", { type: "button", class: "btn primary", text: "Submit", onclick: submit })
               : h("button", { type: "button", class: "btn primary", text: "Save and Continue", onclick: next })));
  }

  function saveStateText() {
    if (S.busy) return "Saving...";
    if (S.dirty) return "Unsaved changes";
    if (S.savedAt) return `Saved at ${S.savedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    return "";
  }
  function setSaveState() {
    const el = document.getElementById("save-state");
    if (el) el.textContent = saveStateText();
  }

  // ---------------------------------------------------------------- review and done
  function reviewView() {
    const out = [h("div", { class: "page-head" }, h("h2", { text: "Review" })),
      h("p", { class: "page-intro" }, "Check your answers, then submit. Nothing is sent anywhere: Submit stores your ",
        "profile in ", h("code", { text: S.database }), " on this computer, and jobfill fills applications from it.")];
    const all = {};
    formPages().forEach(p => Object.assign(all, pageErrors(p)));
    Object.assign(all, S.errors);
    const keys = Object.keys(all);
    S.errors = all;
    const banner = errorBanner(keys);
    if (banner) out.push(banner);
    formPages().forEach((page, p) => out.push(reviewPage(page, p)));
    out.push(actions());
    return out;
  }

  function reviewPage(page, p) {
    const rows = [];
    let unanswered = [];
    for (const sec of page.sections) {
      for (const item of sec.items) {
        if (item.type === "fact") {
          const v = S.values[item.key];
          if (!isBlank(v, item)) rows.push(reviewRow(item.label, clip(display(item, v))));
          else if (item.required) rows.push(reviewRow(item.label, "Required", true));
          else if (item.recommended) unanswered.push(item.label);
          continue;
        }
        const entries = (S.records[item.record] || []).filter(e => !entryBlank(item, e));
        if (entries.length) {
          rows.push(reviewRow(item.title, h("ul", {}, entries.map(e => h("li", {
            text: item.summary.map(name => display(item.fields.find(f => f.name === name), e[name])).filter(Boolean).join(" · ") })))));
        } else if (item.required) rows.push(reviewRow(item.title, "Required: add at least one", true));
        else if (item.recommended) unanswered.push(item.title);
      }
    }
    return h("div", { class: "review-page" },
      h("div", { class: "review-head" }, h("h3", { text: page.title }),
        h("button", { type: "button", class: "link", text: "Edit", onclick: () => go(p) })),
      rows.length ? h("dl", { class: "review-list" }, rows) : h("p", { class: "review-more", text: "Nothing answered yet." }),
      unanswered.length ? h("details", { class: "review-more" },
        h("summary", { text: `${unanswered.length} recommended ${unanswered.length === 1 ? "question" : "questions"} not answered` }),
        h("p", { text: unanswered.join(" · ") })) : null);
  }

  function reviewRow(label, value, missing) {
    return h("div", { class: "review-row" }, h("dt", { text: label }),
      h("dd", { class: missing ? "missing" : null }, value));
  }

  function doneView() {
    const st = S.done;
    const names = (st.missing_recommended || []).map(k => k.startsWith("record:")
      ? (S.blocks[k.slice(7)] || {}).title || k.slice(7) : (S.facts[k] || {}).label || k);
    return h("div", { class: "card submitted" },
      h("div", { class: "done-icon", "aria-hidden": "true", text: "✓" }),
      h("h2", { text: "Profile submitted" }),
      h("p", { text: st.setup_complete
        ? "Setup is complete: jobfill can now answer every question it recognizes, on every application."
        : `Saved. Still missing: ${st.missing_required.join(", ")}.` }),
      names.length ? h("details", {}, h("summary", { text: `${names.length} recommended questions are still empty` }),
        h("p", { text: names.join(" · ") })) : null,
      h("div", { class: "next" }, h("h3", { text: "What happens next" }),
        h("ol", {},
          h("li", { text: "Go back to your agent. It turns your About You answers into the base material for written answers and asks you to approve it." }),
          h("li", {}, "Apply with ", h("code", { text: "/apply <job URL>" }), ". You review every application before it is submitted."),
          h("li", {}, "To change anything later, run ", h("code", { text: "python -m jobfill portal" }), " again."))),
      S.keepOpen
        ? h("p", {}, h("button", { type: "button", class: "btn secondary", text: "Keep editing",
                                   onclick: () => { S.done = null; S.page = 0; render(); } }))
        : h("p", { class: "muted", text: "The portal has stopped. You can close this tab." }));
  }

  // ---------------------------------------------------------------- navigation and saving
  // What a save sends: only the answers changed on this page since the last save, so a page left open
  // never overwrites what was stored elsewhere meanwhile. Unchanged cards go as {_id} to keep positions.
  function changes(submit) {
    const facts = {};
    for (const key of S.touched) facts[key] = S.values[key];
    const records = {};
    for (const [kind, list] of Object.entries(S.records)) {
      records[kind] = list.map(e => {
        if (e._id && !e._changed) return { _id: e._id };
        const { _changed, ...entry } = e;
        return entry;
      });
    }
    return { facts, records, removed: S.removed, submit };
  }

  function problemBanner() {
    if (!S.problem) return null;
    const stopped = S.problem === "stopped";
    return h("div", { class: "banner", role: "alert", id: "problem" },
      h("h3", { text: stopped ? "Not saved: jobfill is not running" : "Not saved" }),
      stopped
        ? h("p", {}, "The portal behind this page was stopped. Your answers are still here. Start it again in a ",
            "terminal with ", h("code", { text: "python -m jobfill portal" }),
            ", then come back to this tab and save again.")
        : h("p", {}, `${S.problem}.`));
  }

  async function persist(submit, announce) {
    if (!submit && !S.dirty) {
      if (announce) toast("All changes are saved.");
      return true;
    }
    S.busy = true;
    setSaveState();
    app.classList.add("wrap-busy");
    try {
      const res = await api("/api/save", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes(submit)) });
      if (!res.ok) {
        S.errors = res.errors;
        const first = Object.keys(res.errors)[0];
        const p = pageOf(first);
        if (p !== undefined) S.page = p;
        render();
        const banner = document.getElementById("banner");
        if (banner) banner.scrollIntoView({ behavior: "smooth", block: "center" });
        return false;
      }
      applyState(res.state);
      S.dirty = false;
      S.touched.clear();
      S.removed = {};
      S.problem = null;
      S.savedAt = new Date();
      refresh();   // the page's fields now point at the saved records (new ones got their ids)
      if (announce) toast("Saved.");
      return res;
    } catch (e) {
      if (e.stopped || e.status === 403) {
        // Not a field problem: keep saying so until a save goes through, and keep every answer on the page.
        S.problem = e.stopped ? "stopped" : e.message;
        refresh();
        document.getElementById("problem").scrollIntoView({ behavior: "smooth", block: "center" });
      } else {
        toast(`Could not save: ${e.message}`, true);
      }
      return false;
    } finally {
      S.busy = false;
      app.classList.remove("wrap-busy");
      setSaveState();
    }
  }

  async function go(i) {
    if (i === S.page) return;
    S.visited.add(S.page);
    if (!(await persist(false, false))) return;
    for (const k of Object.keys(S.errors)) if (pageOf(k) === S.page) delete S.errors[k];
    S.page = i;
    render();
    window.scrollTo(0, 0);
  }

  async function next() {
    const page = S.pages[S.page];
    S.visited.add(S.page);
    if (!(await persist(false, false))) return;
    const errs = pageErrors(page);
    if (Object.keys(errs).length) {
      S.errors = errs;
      refresh();
      document.getElementById("banner").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    S.errors = {};
    S.page += 1;
    render();
    window.scrollTo(0, 0);
  }

  async function submit() {
    const all = {};
    formPages().forEach(p => Object.assign(all, pageErrors(p)));
    if (Object.keys(all).length) {
      S.errors = all;
      refresh();
      document.getElementById("banner").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const res = await persist(true, false);
    if (res && res.ok) {
      S.done = res.state.status;
      render();
      window.scrollTo(0, 0);
    }
  }

  // ---------------------------------------------------------------- toast
  const toastEl = h("div", { class: "toast", role: "status", "aria-live": "polite" });
  let toastTimer = null;
  function toast(msg, isError, action) {
    toastEl.replaceChildren(h("span", { text: msg }),
      action ? h("button", { type: "button", class: "link", text: action[0],
                             onclick: () => { action[1](); toastEl.classList.remove("show"); } }) : null);
    toastEl.className = `toast show${isError ? " error" : ""}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), action ? 6000 : 2600);
  }

  window.addEventListener("beforeunload", e => {
    if (S.dirty && !S.done) { e.preventDefault(); e.returnValue = ""; }
  });

  load();
})();
