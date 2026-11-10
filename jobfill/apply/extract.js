// Collect every fillable control on an application form, with the question text a human sees.
// Runs inside the page (Playwright frame.evaluate). Each control is tagged with a stable
// data-jobfill-id so the Python side can address it again in later passes.
(rootSelector) => {
  const root = (rootSelector && document.querySelector(rootSelector)) || document.body;
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const STRIP = "input,select,textarea,option,button,script,style,svg,noscript";
  // Web components (SmartRecruiters' spl-* fields) draw their controls and labels inside open shadow roots. Such a
  // page is read as it is drawn, the flat tree: a host's shadow root in place of its children, a <slot> as the
  // nodes assigned to it. On a page without shadow roots every helper below is the plain DOM call it replaces.
  const SHADOW = Array.from(document.querySelectorAll("*")).some((e) => e.shadowRoot);
  const kids = (n) => {
    if (n.shadowRoot) return n.shadowRoot.childNodes;
    if (n.tagName === "SLOT") { const a = n.assignedNodes(); if (a.length) return a; }
    return n.childNodes;
  };
  const all = (node, selector) => {
    if (!SHADOW) return Array.from(node.querySelectorAll(selector));
    const out = [];
    const walk = (n) => { for (const c of kids(n)) { if (c.nodeType === 1) { if (c.matches(selector)) out.push(c); walk(c); } } };
    walk(node);
    return out;
  };
  const up = (n) => n.assignedSlot || n.parentElement || (n.parentNode && n.parentNode.host) || null;
  const closest = (el, selector) => {
    if (!SHADOW) return el.closest(selector);
    for (let n = el; n; n = up(n)) if (n.nodeType === 1 && n.matches(selector)) return n;
    return null;
  };
  const holds = (a, b) => {   // a is b or draws it
    if (!SHADOW) return a.contains(b);
    for (let n = b; n; n = up(n)) if (n === a) return true;
    return false;
  };
  const chain = (n) => { const out = []; for (; n; n = up(n)) out.unshift(n); return out; };
  const after = (a, b) => {   // b comes after a's start on the page (inside a counts)
    if (!SHADOW) return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    const ca = chain(a), cb = chain(b);
    let i = 0;
    while (i < ca.length && i < cb.length && ca[i] === cb[i]) i++;
    if (i === 0) return false;
    if (i === ca.length) return true;
    if (i === cb.length) return false;
    const sibs = Array.from(kids(ca[i - 1]));
    return sibs.indexOf(ca[i]) < sibs.indexOf(cb[i]);
  };
  // Visible text only: status messages hidden with display:none ("Analyzing resume...") stay out.
  const textOf = (node, strip = STRIP) => {
    if (!node) return "";
    let out = "";
    const walk = (n) => {
      if (n.nodeType === 3) { out += n.nodeValue; return; }
      if (n.nodeType !== 1 || n.matches(strip)) return;
      const st = getComputedStyle(n);
      if (st.display === "none" || st.visibility === "hidden") return;
      for (const c of kids(n)) walk(c);
      if (/^(DIV|P|LI|BR|H\d|LABEL|LEGEND)$/.test(n.tagName)) out += " ";
    };
    walk(node);
    return clean(out);
  };
  // An id names an element of the same tree: the element's shadow root first, then the ones around it, the page.
  const byIdNear = (el, id) => {
    if (!id) return null;
    for (let r = el && el.getRootNode(); r; r = r.host ? r.host.getRootNode() : null) {
      const hit = r.getElementById && r.getElementById(id);
      if (hit || r === document) return hit || null;
    }
    return document.getElementById(id);
  };
  const shown = (el) => {
    if (!el) return false;
    const st = getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden") return false;
    return el.getClientRects().length > 0;
  };
  const ariaLabel = (el) => {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    return clean(ids.map((i) => textOf(byIdNear(el, i))).join(" ")) || clean(el.getAttribute("aria-label"));
  };
  const forLabel = (el) => {
    if (!el.id) return null;
    try { return el.getRootNode().querySelector(`label[for="${CSS.escape(el.id)}"]`); } catch (e) { return null; }
  };
  // The web components a control is drawn in, innermost first (none on a plain page).
  const hostsOf = (el, max = 4) => {
    const out = [];
    for (let r = el.getRootNode(); r && r.host && out.length < max; r = r.host.getRootNode()) out.push(r.host);
    return out;
  };
  // A control drawn inside a web component is labelled by the component: its `label` attribute, or the text put in
  // its label slot (SmartRecruiters' screening questions). Only a component around this control alone: the phone
  // field's label is not its country list's.
  const hostLabel = (el) => {
    for (const h of hostsOf(el, 3)) {
      if (all(h, CONTROL).some((c) => c !== el)) break;
      const own = clean(h.getAttribute("label"));
      if (own) return own;
      const slotted = Array.from(h.children).find((c) => /^label(-content)?$/.test(c.getAttribute("slot") || ""));
      if (slotted && textOf(slotted)) return textOf(slotted);
    }
    return "";
  };
  // Label of one control: aria-labelledby, <label for>, wrapping <label>, its component's label, aria-label. Parts
  // that only name the upload button (Workable labels its file input "Resume" + "Choose file", later "Replace file")
  // are left out.
  const directLabel = (el) => {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    const viaIds = clean(ids.map((i) => textOf(byIdNear(el, i))).filter((t) => !GENERIC.test(t)).join(" "));
    if (viaIds) return viaIds;
    const l = forLabel(el);
    if (l && textOf(l)) return textOf(l);
    const wrap = closest(el, "label");
    if (wrap) {
      // A wrapping label may hold the whole question block; its own label element is the question.
      const inner = all(wrap, ".application-label, [class*='label' i]")
        .find((c) => !holds(c, el) && !all(c, CONTROL).length && textOf(c));
      if (inner) return textOf(inner);
      if (textOf(wrap)) return textOf(wrap);
    }
    return hostLabel(el) || clean(el.getAttribute("aria-label"));
  };
  const GENERIC = /^(attach|upload|choose( a)? files?|browse|select( a)? files?|drop files? here|add( a)? files?|attach files?|upload( a)? files?|replace( a)? files?)$/i;
  // A radio / checkbox drawn as its own element (Workable: a role=radio or role=checkbox box holding an
  // aria-hidden input). The box is what a person clicks and what says whether it is on.
  const roleBox = (el) => closest(el, "[role=radio], [role=checkbox], [role=switch]");
  const isOn = (el) => !!el && (el.checked || (roleBox(el) || el).getAttribute("aria-checked") === "true");
  const LABELISH = "label, legend, .application-label, [class*='label' i], [class*='question' i], [class*='title' i], h3, h4, h5, p, span, div";
  const CONTROL = "input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea";
  // Question text from the surrounding block, used for radio/checkbox groups and unlabeled
  // inputs. Climbs only while the block contains nothing but this field's own controls, so a
  // neighbouring question's label is never borrowed.
  const blockLabel = (el, own) => {
    let node = el;
    // A web component nests a few levels per field: climb further on such a page.
    for (let depth = 0; depth < (SHADOW ? 14 : 7); depth++) {
      node = up(node);
      if (!node || node === document.body) break;
      if (node.nodeType !== 1) continue;
      const others = all(node, CONTROL).filter((c) => !own.includes(c));
      if (others.length) break;
      if (node.tagName === "FIELDSET") {
        // <legend>, or (Ashby) a <label> as the fieldset's own first-level child.
        const lg = node.querySelector(":scope > legend") ||
          Array.from(node.querySelectorAll(":scope > label")).find((l) => !own.some((o) => forLabel(o) === l));
        if (lg && textOf(lg)) return textOf(lg);
      }
      const role = node.getAttribute("role");
      if (role === "radiogroup" || role === "group") {
        const t = ariaLabel(node);
        if (t) return t;
      }
      const cands = all(node, LABELISH).filter((c) =>
        !own.some((o) => holds(c, o)) && !all(c, CONTROL).length && after(c, own[0]) && textOf(c) &&
        !own.some((o) => { const l = forLabel(o) || closest(o, "label"); return l && holds(l, c); }));
      // Prefer real label elements, then the nearest preceding text block.
      const strong = cands.filter((c) => c.matches("label, legend, .application-label, [class*='label' i], [class*='question' i]"));
      const pick = (strong.length ? strong : cands);
      if (pick.length) {
        // Outermost of the nearest candidates holds the full question text.
        let best = pick[pick.length - 1];
        while (up(best) && pick.includes(up(best))) best = up(best);
        return textOf(best);
      }
    }
    return "";
  };
  const sectionOf = (el) => {
    for (let a = up(el); a && a !== document.body; a = up(a)) {
      const ui = a.getAttribute("data-ui");   // Workable: <div data-ui="education"> holds its entry editor
      if (ui === "education" || ui === "experience") return ui;
      const cls = typeof a.className === "string" ? a.className : "";
      // aria-labelledby: Workday's sections are role=group blocks labelled "Work-Experience-section" etc.
      const key = `${cls} ${a.id || ""} ${a.getAttribute("data-qa") || ""} ${a.getAttribute("aria-labelledby") || ""}`.toLowerCase();
      if (/education/.test(key)) return "education";
      if (/work-?history|employment|experience-|-experience|experiences/.test(key)) return "experience";
      if (/references?\b/.test(key) && !/reference-?number/.test(key)) return "reference";
    }
    const idn = `${el.id} ${el.getAttribute("name") || ""}`.toLowerCase();
    if (/school|degree|discipline|education/.test(idn)) return "education";
    if (/workexperience|work-?history|employment/.test(idn)) return "experience";
    return null;
  };
  // Smallest fieldset/group around a checkbox that holds several checkboxes and nothing else.
  const choiceBlock = (el, type) => {
    let node = el;
    for (let depth = 0; depth < 6; depth++) {
      node = up(node);
      if (!node || node === document.body) return null;
      const controls = all(node, CONTROL);
      if (controls.some((c) => c.type !== type)) return null;
      if (controls.length > 1 && (node.tagName === "FIELDSET" || node.getAttribute("role") === "group" || depth >= 2)) return node;
    }
    return null;
  };
  // Split dates: controls labelled only "Month" / "Year" / "Day" (or MM / YYYY) take the heading of
  // the block that holds just those controls, e.g. "Expected graduation date Month".
  const PART = /^(month|year|day|mm|yyyy|yy|dd)\s*[*✱]?$/i;
  const partHeading = (el) => {
    let node = el;
    for (let depth = 0; depth < 5; depth++) {
      node = up(node);
      if (!node || node === document.body) break;
      const ctrls = all(node, CONTROL);
      if (ctrls.length > 3 || ctrls.some((c) => !PART.test(directLabel(c) || c.getAttribute("placeholder") || ""))) break;
      const heads = all(node, LABELISH).filter((c) =>
        !all(c, CONTROL).length && textOf(c) && !PART.test(textOf(c)) && !ctrls.some((o) => forLabel(o) === c) &&
        after(c, ctrls[0]));
      if (heads.length) {
        let best = heads[heads.length - 1];
        while (up(best) && heads.includes(up(best))) best = up(best);
        return textOf(best);
      }
    }
    return "";
  };
  // A dropdown's own input may say nothing: its required mark sits on the hidden input that carries the
  // chosen value for the site's validation (Workable, React-Select), in the same small block.
  const hiddenRequired = (el) => {
    let box = up(el);
    for (let i = 0; i < 4 && box && box !== document.body; i++, box = up(box)) {
      if (all(box, "input[aria-hidden=true][required]").length) return true;
      if (all(box, CONTROL).some((c) => c !== el && c.getAttribute("aria-hidden") !== "true")) break;
    }
    return false;
  };
  // A web component carries the required mark itself (SmartRecruiters' spl-date-field, spl-radio-group).
  const hostRequired = (el) => hostsOf(el, 2).some((h) => h.hasAttribute("required") && h.getAttribute("required") !== "false")
    || !!(SHADOW && closest(el, "[role=radiogroup][aria-required=true]"));
  const required = (el, label) =>
    el.required || el.getAttribute("aria-required") === "true" || /[*✱]|\(required\)/i.test(label)
    || (roleBox(el) || el).getAttribute("aria-required") === "true"
    || (el.getAttribute("role") === "combobox" && hiddenRequired(el)) || hostRequired(el);
  // Choices already picked in a Workday prompt, read from its pills.
  const pillsOf = (el) => {
    const box = closest(el, "[data-automation-id^='formField']") || closest(el, "[data-automation-id='multiSelectContainer']")
      || up(el);
    return all(box, "[data-automation-id='selectedItem']")
      .map((p) => clean((p.getAttribute("title") || p.innerText || "").replace(/[×✕]/g, ""))).filter(Boolean);
  };
  // A file already taken by the site: uploaders like Workday's clear the input once the upload is done and
  // list the file's name beside it instead.
  const uploadedName = (el) => {
    const box = closest(el, "[data-automation-id^='formField'], [data-automation-id$='FileUpload' i]") || up(el);
    // innerText, not textOf(): the name may sit on a button (open / remove the file). A web component's list of
    // files is drawn in its shadow root, which innerText does not reach: there every element's text is kept apart
    // ("resume.pdf" then "Remove", not "resume.pdfRemove").
    const spaced = (node) => {
      let out = "";
      const walk = (n) => {
        if (n.nodeType === 3) { out += n.nodeValue; return; }
        if (n.nodeType !== 1 || n.matches("input,select,textarea,script,style,svg,noscript")) return;
        const st = getComputedStyle(n);
        if (st.display === "none" || st.visibility === "hidden") return;
        for (const c of kids(n)) walk(c);
        out += " ";
      };
      walk(node);
      return out;
    };
    const text = box ? (SHADOW ? spaced(box) : box.innerText) : "";
    const m = clean(text).match(/(?:^|\s)([^\s\\/:*?"<>|]+\.(?:pdf|docx?|rtf|txt|odt|pages))\b/i);
    return m ? m[1] : "";
  };
  // React-Select shows the chosen option in a sibling element, not in the input itself.
  const comboValue = (el) => {
    let box = up(el);
    for (let i = 0; i < 4 && box; i++, box = up(box)) {
      const v = all(box, "[class*='single-value' i], [class*='singleValue'], [class*='multi-value__label' i]");
      if (v.length) return v.map((n) => clean(n.textContent)).filter(Boolean).join(", ");
      if (all(box, "[role=combobox]").length > 1) break;
    }
    return el.value || "";
  };
  // Test hooks around a control, innermost first (SmartRecruiters' data-test="location-autocomplete"): an adapter
  // tells its widgets apart by them.
  const testIds = (el) => {
    const out = [];
    for (let n = el, i = 0; n && n !== document.body && i < 40 && out.length < 6; n = up(n), i++) {
      const t = n.nodeType === 1 && (n.getAttribute("data-test") || n.getAttribute("data-testid"));
      if (t && !out.includes(t)) out.push(t);
    }
    return out;
  };

  // Ids never repeat on a page, even after tagged fields are gone (a saved Workable entry editor): a new field
  // must never pass for one read before.
  let next = window.__jobfillNext || 0;
  all(document.documentElement, "[data-jobfill-id]").forEach((e) => {
    next = Math.max(next, parseInt((e.dataset.jobfillId || "").replace("jf", ""), 10) || 0);
  });
  const tagged = new Map();   // id -> element, also for elements inside shadow roots
  const tag = (el) => {
    if (!el.dataset.jobfillId) { el.dataset.jobfillId = "jf" + ++next; window.__jobfillNext = next; }
    tagged.set(el.dataset.jobfillId, el);
    return el.dataset.jobfillId;
  };
  const byTag = (id) => tagged.get(id) || document.querySelector(`[data-jobfill-id="${id}"]`);

  const fields = [];
  const groups = new Map();
  const seen = new Map();
  for (const el of all(root, "input, select, textarea, button[aria-haspopup=listbox]")) {
    const t = el.tagName.toLowerCase();
    // A "Select One" button that opens a list (Workday's single-choice dropdowns: How Did You Hear About
    // Us?, Country, State, Phone Device Type). Its answer is the option clicked in that list, which the
    // button then shows as its text; the 0x0 input next to it is only for the site's own validation.
    if (t === "button") {
      if (!shown(el) || el.disabled) continue;
      if (!forLabel(el) && !closest(el, "[data-automation-id^='formField'], fieldset, form")) continue;   // e.g. a language picker
      const text = clean(el.innerText || el.textContent);
      const aria = clean(el.getAttribute("aria-label"));
      let label = directLabel(el);
      // No <label for> (Workday's questionnaires): the question is the fieldset's <legend>. The 0x0 input
      // beside the button belongs to it, so it must not stop the climb as "another question's control".
      const own = [el, ...(el.parentElement ? el.parentElement.querySelectorAll(":scope > input") : [])];
      if (!label || label === aria) label = blockLabel(el, own) || clean(text ? aria.split(text)[0] : aria);
      if (!label) continue;
      const f = { id: tag(el), kind: "dropdown", type: "dropdown", label, name: el.getAttribute("name") || "",
                  html_id: el.id || "", section: sectionOf(el),
                  required: required(el, label) || /\brequired\b/i.test(aria),
                  value: /^(select( one)?|choose( one)?|please select|-+|)$/i.test(text) ? "" : text };
      const hosts = hostsOf(el);
      if (hosts.length) f.hosts = hosts.map((h) => h.tagName.toLowerCase());
      fields.push(f);
      continue;
    }
    let type = t === "input" ? (el.getAttribute("type") || "text").toLowerCase() : t;
    if (["hidden", "submit", "button", "reset", "image"].includes(type) || el.disabled) continue;
    const choice = type === "radio" || type === "checkbox";
    // React-Select keeps an aria-hidden, tabindex=-1 input next to each dropdown for validation. A hidden
    // radio / checkbox inside a role=radio / role=checkbox box is the real answer behind that box (Workable).
    if (el.getAttribute("aria-hidden") === "true" && type !== "file" && !(choice && roleBox(el))) continue;
    const name = el.getAttribute("name") || "";
    // Bot traps: filling Workday's hidden "beecatcher" (or any honeypot) flags the applicant as a bot.
    if (/captcha|honeypot|recaptcha|hcaptcha|beecatcher/i.test(`${name} ${el.id} ${el.className} ${el.getAttribute("data-automation-id") || ""}`)) continue;
    if (closest(el, "[class*='captcha' i]")) continue;
    // "Autofill from resume" panes (Ashby, Greenhouse; SmartRecruiters' "Easy Apply") are shortcuts, not questions.
    if (closest(el, "[class*='autofill' i], [data-test*='easy-apply' i]")) continue;
    // The hidden input behind a toggle-button pair (Ashby yes/no) is read as the button group below.
    if (choice && el.parentElement && el.parentElement.querySelector(":scope > button[aria-pressed]")) continue;
    // Likewise the 0x0 input beside a "Select One" button: the button is the question.
    if (el.parentElement && el.parentElement.querySelector(":scope > button[aria-haspopup=listbox]")) continue;
    if (type !== "file") {
      const lab = forLabel(el) || closest(el, "label");
      if (!(shown(el) || (choice && lab && shown(lab)) || (choice && shown(roleBox(el))))) continue;
    }

    if (choice) {
      // Group by shared name; else (Ashby: one name per checkbox) by the block holding them.
      let members = name ? all(root, `input[name="${CSS.escape(name)}"]`) : [];
      let key = name;
      // Radios drawn one per web component (SmartRecruiters names only the chosen one): by their radiogroup.
      const radios = type === "radio" && el.getRootNode() !== document && closest(el, "[role=radiogroup], fieldset");
      if (radios) {
        members = all(radios, "input[type=radio]");
        key = "block:" + tag(radios);
      } else if (members.length < 2 && type === "checkbox" && el.getRootNode() === document) {
        // (A checkbox drawn by its own web component is its own question: SmartRecruiters' consents.)
        const box = choiceBlock(el, type);
        if (box) {
          members = all(box, `input[type=${type}]`);
          key = "block:" + tag(box);
        }
      }
      if (key && (type === "radio" || members.length > 1)) {
        if (groups.has(key)) { groups.get(key).option_refs.push([directLabel(el) || el.value, tag(el)]); continue; }
        const label = blockLabel(el, members) || ariaLabel(closest(el, "[role]") || el) || name;
        const f = { id: "g-" + tag(el), kind: type, type, label, name, html_id: el.id || "", group: true,
                    option_refs: [[directLabel(el) || el.value, tag(el)]], required: required(el, label),
                    section: sectionOf(el), value: null };
        groups.set(key, f);
        fields.push(f);
        continue;
      }
    }

    const role = (el.getAttribute("role") || "").toLowerCase();
    // Workday's prompt: a plain text input that only searches; the answer is a choice clicked in its
    // pop-up menu, shown afterwards as a pill while the box itself stays empty.
    const selectInput = el.getAttribute("data-uxi-widget-type") === "selectinput"
      || (t === "input" && !!closest(el, "[data-automation-id='multiSelectContainer'], [data-automation-id='multiselectInputContainer']"));
    const kind = type === "file" ? "file" : t === "select" ? "select" : t === "textarea" ? "textarea"
      : selectInput ? "selectinput" : role === "combobox" ? "combobox" : choice ? "checkbox" : "input";
    let label = directLabel(el);
    if (!label || (choice && label.length < 2) || (kind === "file" && GENERIC.test(label))) {
      label = blockLabel(el, [el]) || label;
    }
    if (kind === "file" && !/resume|cv|cover|transcript|portfolio|writing|sample/i.test(label)) {
      // "Upload a file (5MB max)" says nothing about which file: Workday's section heading does ("Resume/CV").
      const group = closest(el, "[role=group][aria-labelledby]");
      const head = group && textOf(byIdNear(group, group.getAttribute("aria-labelledby")));
      if (head) label = head;
    }
    if (!label) label = clean(el.getAttribute("placeholder")) || name || el.id;
    if (!label) continue;   // nothing a person could read either (e.g. Ashby's "autofill from resume" drop zone)
    if (PART.test(label)) {
      const head = partHeading(el);
      if (head) label = `${head} ${label}`;
    }
    const f = { id: tag(el), kind, type: kind === "input" ? type : kind, label, name, html_id: el.id || "",
                required: required(el, label), section: sectionOf(el), placeholder: clean(el.getAttribute("placeholder")) };
    if (el.maxLength > 0 && el.maxLength < 100000) f.max_chars = el.maxLength;
    if (role === "spinbutton") f.spin = true;   // Workday's date parts (Month / Year): typed key by key
    const hosts = hostsOf(el);
    if (hosts.length) f.hosts = hosts.map((h) => h.tagName.toLowerCase());   // the web components it is drawn in
    if (kind === "select") {
      f.multiple = el.multiple;
      // Options inside <optgroup> read as "Group: Option" (two "LinkedIn"s under "Job Board" and
      // "Social Media" stay distinguishable); option_index points back at the real option to select.
      f.options = []; f.option_index = [];
      Array.from(el.options).forEach((o, i) => {
        if (o.disabled || o.value === "" || !clean(o.text) || /^(select|choose|please select|--)/i.test(clean(o.text))) return;
        const group = o.parentElement && o.parentElement.tagName === "OPTGROUP" ? clean(o.parentElement.label) : "";
        f.options.push(group ? `${group}: ${clean(o.text)}` : clean(o.text));
        f.option_index.push(i);
      });
      const sel = el.selectedOptions && el.selectedOptions[0];
      f.value = sel && sel.value !== "" ? clean(sel.text) : "";
    } else if (kind === "checkbox") {
      f.value = isOn(el);
    } else if (kind === "combobox") {
      f.value = comboValue(el);
    } else if (kind === "selectinput") {
      f.value = pillsOf(el).join(", ");   // judged by the pills, never by the (always empty) search box
    } else if (kind === "file") {
      f.value = el.files && el.files.length ? el.files[0].name : uploadedName(el);
    } else {
      f.value = el.value || "";
    }
    fields.push(f);
  }
  // Toggle-button groups (Ashby yes/no): two or more aria-pressed buttons side by side.
  const buttonGroups = new Set();
  for (const b of all(root, "button[aria-pressed]")) {
    const box = b.parentElement;
    if (!box || buttonGroups.has(box) || !shown(box)) continue;
    const buttons = Array.from(box.querySelectorAll(":scope > button[aria-pressed]"));
    const btext = (x) => clean(x.innerText || x.textContent);   // textOf() skips <button> on purpose
    if (buttons.length < 2 || buttons.some((x) => btext(x).length === 0 || btext(x).length > 40)) continue;
    buttonGroups.add(box);
    const own = [...buttons, ...box.querySelectorAll("input")];
    const label = blockLabel(buttons[0], own);
    if (!label) continue;
    const pressed = buttons.find((x) => x.getAttribute("aria-pressed") === "true");
    fields.push({ id: "g-" + tag(box), kind: "buttons", type: "radio", label, name: "", html_id: "", group: true,
                  option_refs: buttons.map((x) => [btext(x), tag(x)]),
                  required: required(box, label) || !!(box.parentElement && box.parentElement.querySelector("label[class*='required' i]")),
                  section: sectionOf(box), value: pressed ? clean(pressed.innerText) : "" });
  }
  // A field's own instructions, shown under it ("If it doesn't apply to you, please enter 'None'", "in USD",
  // "max 300 words"): aria-describedby first (Greenhouse), else a description/help element in the field's
  // own block (Ashby, Workday). Error slots are skipped; they are empty until the site complains.
  const DESC = "[id$='-description'], [class*='description' i], [class*='helper' i], [class*='hint' i], " +
               "[data-automation-id*='description' i], [data-automation-id*='instruction' i]";
  const describe = (own) => {
    const el = own[0];
    const ids = (el.getAttribute("aria-describedby") || "").split(/\s+/).filter((i) => i && !/error/i.test(i));
    const via = clean(ids.map((i) => textOf(byIdNear(el, i))).join(" "));
    if (via) return via;
    let node = el;
    for (let depth = 0; depth < 5; depth++) {
      node = up(node);
      if (!node || node === document.body) break;
      if (all(node, CONTROL).some((c) => !own.includes(c))) break;   // another question's block
      const d = all(node, DESC).find((c) => !own.some((o) => holds(c, o)) && !closest(c, "label") && textOf(c));
      if (d) return textOf(d);
    }
    return "";
  };
  for (const f of fields) {
    const ownIds = f.option_refs ? f.option_refs.map((r) => r[1]) : [f.id.replace(/^g-/, "")];
    const own = ownIds.map(byTag).filter(Boolean);
    if (own[0] && own[0].tagName === "BUTTON" && own[0].parentElement) {
      own.push(...own[0].parentElement.querySelectorAll(":scope > input"));   // the 0x0 input beside "Select One"
    }
    const d = own.length ? describe(own).slice(0, 500) : "";
    if (d && d !== f.label && !f.label.includes(d)) f.description = d;
  }
  // Index repeating blocks: the n-th "School" inside the education section is index n.
  for (const f of fields) {
    if (f.option_refs) {
      f.options = f.option_refs.map((r) => r[0]);
      if (f.kind !== "buttons") {   // toggle buttons already carry their pressed value
        const opts = f.option_refs.map((r) => byTag(r[1])).filter(Boolean);
        f.value = f.option_refs.filter((r) => isOn(byTag(r[1]))).map((r) => r[0]);
        if (f.kind === "radio") f.value = f.value[0] || "";
        // Still on the page's own preselection (Workable's EEO survey opens on "I don't wish to answer"): a site
        // default, not anyone's answer.
        if (f.kind === "radio" && f.value) f.preset = opts.every((o) => o.checked === o.defaultChecked);
      }
    }
    // An entry block the adapter numbered itself (Workable edits one entry at a time, so the open editor is
    // always the only "School" on the page): its fields answer that stored entry.
    const ownEl = byTag(f.id.replace(/^g-/, ""));
    const entry = f.section && ownEl && closest(ownEl, "[data-jobfill-index]");
    if (entry) {
      f.index = parseInt(entry.getAttribute("data-jobfill-index"), 10) || 0;
      f.entry = true;
    } else if (f.section) {
      const key = f.section + "|" + f.label.toLowerCase();
      f.index = seen.get(key) || 0;
      seen.set(key, f.index + 1);
    }
  }
  for (const f of fields) {
    const el = byTag(f.id.replace(/^g-/, ""));
    const ids = el ? testIds(el) : [];
    if (ids.length) f.test_ids = ids;
  }
  return fields;
}
