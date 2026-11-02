// Collect every fillable control on an application form, with the question text a human sees.
// Runs inside the page (Playwright frame.evaluate). Each control is tagged with a stable
// data-jobfill-id so the Python side can address it again in later passes.
(rootSelector) => {
  const root = (rootSelector && document.querySelector(rootSelector)) || document.body;
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const STRIP = "input,select,textarea,option,button,script,style,svg,noscript";
  // Visible text only: status messages hidden with display:none ("Analyzing resume...") stay out.
  const textOf = (node) => {
    if (!node) return "";
    let out = "";
    const walk = (n) => {
      if (n.nodeType === 3) { out += n.nodeValue; return; }
      if (n.nodeType !== 1 || n.matches(STRIP)) return;
      const st = getComputedStyle(n);
      if (st.display === "none" || st.visibility === "hidden") return;
      for (const c of n.childNodes) walk(c);
      if (/^(DIV|P|LI|BR|H\d|LABEL|LEGEND)$/.test(n.tagName)) out += " ";
    };
    walk(node);
    return clean(out);
  };
  const byId = (id) => (id ? document.getElementById(id) : null);
  const shown = (el) => {
    if (!el) return false;
    const st = getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden") return false;
    return el.getClientRects().length > 0;
  };
  const ariaLabel = (el) => {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    return clean(ids.map((i) => textOf(byId(i))).join(" ")) || clean(el.getAttribute("aria-label"));
  };
  const forLabel = (el) => {
    if (!el.id) return null;
    try { return document.querySelector(`label[for="${CSS.escape(el.id)}"]`); } catch (e) { return null; }
  };
  // Label of one control: aria-labelledby, <label for>, wrapping <label>, aria-label.
  const directLabel = (el) => {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    const viaIds = clean(ids.map((i) => textOf(byId(i))).join(" "));
    if (viaIds) return viaIds;
    const l = forLabel(el);
    if (l && textOf(l)) return textOf(l);
    const wrap = el.closest("label");
    if (wrap) {
      // A wrapping label may hold the whole question block; its own label element is the question.
      const inner = Array.from(wrap.querySelectorAll(".application-label, [class*='label' i]"))
        .find((c) => !c.contains(el) && !c.querySelector(CONTROL) && textOf(c));
      if (inner) return textOf(inner);
      if (textOf(wrap)) return textOf(wrap);
    }
    return clean(el.getAttribute("aria-label"));
  };
  const GENERIC = /^(attach|upload|choose( a)? files?|browse|select( a)? files?|drop files? here|add( a)? files?|attach files?|upload( a)? files?)$/i;
  const LABELISH = "label, legend, .application-label, [class*='label' i], [class*='question' i], [class*='title' i], h3, h4, h5, p, span, div";
  const CONTROL = "input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea";
  // Question text from the surrounding block, used for radio/checkbox groups and unlabeled
  // inputs. Climbs only while the block contains nothing but this field's own controls, so a
  // neighbouring question's label is never borrowed.
  const blockLabel = (el, own) => {
    let node = el;
    for (let depth = 0; depth < 7; depth++) {
      node = node.parentElement;
      if (!node || node === document.body) break;
      const others = Array.from(node.querySelectorAll(CONTROL)).filter((c) => !own.includes(c));
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
      const cands = Array.from(node.querySelectorAll(LABELISH)).filter((c) =>
        !own.some((o) => c.contains(o)) && !c.querySelector(CONTROL) &&
        (c.compareDocumentPosition(own[0]) & Node.DOCUMENT_POSITION_FOLLOWING) && textOf(c) &&
        !own.some((o) => { const l = forLabel(o) || o.closest("label"); return l && (l === c || l.contains(c)); }));
      // Prefer real label elements, then the nearest preceding text block.
      const strong = cands.filter((c) => c.matches("label, legend, .application-label, [class*='label' i], [class*='question' i]"));
      const pick = (strong.length ? strong : cands);
      if (pick.length) {
        // Outermost of the nearest candidates holds the full question text.
        let best = pick[pick.length - 1];
        while (best.parentElement && pick.includes(best.parentElement)) best = best.parentElement;
        return textOf(best);
      }
    }
    return "";
  };
  const sectionOf = (el) => {
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
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
      node = node.parentElement;
      if (!node || node === document.body) return null;
      const controls = Array.from(node.querySelectorAll(CONTROL));
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
      node = node.parentElement;
      if (!node || node === document.body) break;
      const ctrls = Array.from(node.querySelectorAll(CONTROL));
      if (ctrls.length > 3 || ctrls.some((c) => !PART.test(directLabel(c) || c.getAttribute("placeholder") || ""))) break;
      const heads = Array.from(node.querySelectorAll(LABELISH)).filter((c) =>
        !c.querySelector(CONTROL) && textOf(c) && !PART.test(textOf(c)) && !ctrls.some((o) => forLabel(o) === c) &&
        (c.compareDocumentPosition(ctrls[0]) & Node.DOCUMENT_POSITION_FOLLOWING));
      if (heads.length) {
        let best = heads[heads.length - 1];
        while (best.parentElement && heads.includes(best.parentElement)) best = best.parentElement;
        return textOf(best);
      }
    }
    return "";
  };
  const required = (el, label) =>
    el.required || el.getAttribute("aria-required") === "true" || /[*✱]|\(required\)/i.test(label);
  // Choices already picked in a Workday prompt, read from its pills.
  const pillsOf = (el) => {
    const box = el.closest("[data-automation-id^='formField']") || el.closest("[data-automation-id='multiSelectContainer']")
      || el.parentElement;
    return Array.from(box.querySelectorAll("[data-automation-id='selectedItem']"))
      .map((p) => clean((p.getAttribute("title") || p.innerText || "").replace(/[×✕]/g, ""))).filter(Boolean);
  };
  // A file already taken by the site: uploaders like Workday's clear the input once the upload is done and
  // list the file's name beside it instead.
  const uploadedName = (el) => {
    const box = el.closest("[data-automation-id^='formField'], [data-automation-id$='FileUpload' i]") || el.parentElement;
    // innerText, not textOf(): the name may sit on a button (open / remove the file).
    const m = box && clean(box.innerText).match(/(?:^|\s)([^\s\\/:*?"<>|]+\.(?:pdf|docx?|rtf|txt|odt|pages))\b/i);
    return m ? m[1] : "";
  };
  // React-Select shows the chosen option in a sibling element, not in the input itself.
  const comboValue = (el) => {
    let box = el.parentElement;
    for (let i = 0; i < 4 && box; i++, box = box.parentElement) {
      const v = box.querySelectorAll("[class*='single-value' i], [class*='singleValue'], [class*='multi-value__label' i]");
      if (v.length) return Array.from(v).map((n) => clean(n.textContent)).filter(Boolean).join(", ");
      if (box.querySelectorAll("[role=combobox]").length > 1) break;
    }
    return el.value || "";
  };

  let next = 0;
  document.querySelectorAll("[data-jobfill-id]").forEach((e) => {
    next = Math.max(next, parseInt((e.dataset.jobfillId || "").replace("jf", ""), 10) || 0);
  });
  const tag = (el) => { if (!el.dataset.jobfillId) el.dataset.jobfillId = "jf" + ++next; return el.dataset.jobfillId; };

  const fields = [];
  const groups = new Map();
  const seen = new Map();
  for (const el of root.querySelectorAll("input, select, textarea, button[aria-haspopup=listbox]")) {
    const t = el.tagName.toLowerCase();
    // A "Select One" button that opens a list (Workday's single-choice dropdowns: How Did You Hear About
    // Us?, Country, State, Phone Device Type). Its answer is the option clicked in that list, which the
    // button then shows as its text; the 0x0 input next to it is only for the site's own validation.
    if (t === "button") {
      if (!shown(el) || el.disabled) continue;
      if (!forLabel(el) && !el.closest("[data-automation-id^='formField'], fieldset, form")) continue;   // e.g. a language picker
      const text = clean(el.innerText || el.textContent);
      const aria = clean(el.getAttribute("aria-label"));
      let label = directLabel(el);
      // No <label for> (Workday's questionnaires): the question is the fieldset's <legend>. The 0x0 input
      // beside the button belongs to it, so it must not stop the climb as "another question's control".
      const own = [el, ...(el.parentElement ? el.parentElement.querySelectorAll(":scope > input") : [])];
      if (!label || label === aria) label = blockLabel(el, own) || clean(text ? aria.split(text)[0] : aria);
      if (!label) continue;
      fields.push({ id: tag(el), kind: "dropdown", type: "dropdown", label, name: el.getAttribute("name") || "",
                    html_id: el.id || "", section: sectionOf(el),
                    required: required(el, label) || /\brequired\b/i.test(aria),
                    value: /^(select( one)?|choose( one)?|please select|-+|)$/i.test(text) ? "" : text });
      continue;
    }
    let type = t === "input" ? (el.getAttribute("type") || "text").toLowerCase() : t;
    if (["hidden", "submit", "button", "reset", "image"].includes(type) || el.disabled) continue;
    // React-Select keeps an aria-hidden, tabindex=-1 input next to each dropdown for validation.
    if (el.getAttribute("aria-hidden") === "true" && type !== "file") continue;
    const name = el.getAttribute("name") || "";
    // Bot traps: filling Workday's hidden "beecatcher" (or any honeypot) flags the applicant as a bot.
    if (/captcha|honeypot|recaptcha|hcaptcha|beecatcher/i.test(`${name} ${el.id} ${el.className} ${el.getAttribute("data-automation-id") || ""}`)) continue;
    if (el.closest("[class*='captcha' i]")) continue;
    // "Autofill from resume" panes (Ashby, Greenhouse) are shortcuts, not questions.
    if (el.closest("[class*='autofill' i]")) continue;
    const choice = type === "radio" || type === "checkbox";
    // The hidden input behind a toggle-button pair (Ashby yes/no) is read as the button group below.
    if (choice && el.parentElement && el.parentElement.querySelector(":scope > button[aria-pressed]")) continue;
    // Likewise the 0x0 input beside a "Select One" button: the button is the question.
    if (el.parentElement && el.parentElement.querySelector(":scope > button[aria-haspopup=listbox]")) continue;
    if (type !== "file") {
      const lab = forLabel(el) || el.closest("label");
      if (!(shown(el) || (choice && lab && shown(lab)))) continue;
    }

    if (choice) {
      // Group by shared name; else (Ashby: one name per checkbox) by the block holding them.
      let members = name ? Array.from(root.querySelectorAll(`input[name="${CSS.escape(name)}"]`)) : [];
      let key = name;
      if (members.length < 2 && type === "checkbox") {
        const box = choiceBlock(el, type);
        if (box) {
          members = Array.from(box.querySelectorAll(`input[type=${type}]`));
          key = "block:" + tag(box);
        }
      }
      if (key && (type === "radio" || members.length > 1)) {
        if (groups.has(key)) { groups.get(key).option_refs.push([directLabel(el) || el.value, tag(el)]); continue; }
        const label = blockLabel(el, members) || ariaLabel(el.closest("[role]") || el) || name;
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
      || (t === "input" && !!el.closest("[data-automation-id='multiSelectContainer'], [data-automation-id='multiselectInputContainer']"));
    const kind = type === "file" ? "file" : t === "select" ? "select" : t === "textarea" ? "textarea"
      : selectInput ? "selectinput" : role === "combobox" ? "combobox" : choice ? "checkbox" : "input";
    let label = directLabel(el);
    if (!label || (choice && label.length < 2) || (kind === "file" && GENERIC.test(label))) {
      label = blockLabel(el, [el]) || label;
    }
    if (kind === "file" && !/resume|cv|cover|transcript|portfolio|writing|sample/i.test(label)) {
      // "Upload a file (5MB max)" says nothing about which file: Workday's section heading does ("Resume/CV").
      const group = el.closest("[role=group][aria-labelledby]");
      const head = group && textOf(byId(group.getAttribute("aria-labelledby")));
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
      f.value = el.checked;
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
  for (const b of root.querySelectorAll("button[aria-pressed]")) {
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
    const via = clean(ids.map((i) => textOf(byId(i))).join(" "));
    if (via) return via;
    let node = el;
    for (let depth = 0; depth < 5; depth++) {
      node = node.parentElement;
      if (!node || node === document.body) break;
      if (Array.from(node.querySelectorAll(CONTROL)).some((c) => !own.includes(c))) break;   // another question's block
      const d = Array.from(node.querySelectorAll(DESC)).find((c) => !own.some((o) => c.contains(o)) && !c.closest("label")
                                                                    && textOf(c));
      if (d) return textOf(d);
    }
    return "";
  };
  for (const f of fields) {
    const ownIds = f.option_refs ? f.option_refs.map((r) => r[1]) : [f.id.replace(/^g-/, "")];
    const own = ownIds.map((i) => document.querySelector(`[data-jobfill-id="${i}"]`)).filter(Boolean);
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
        f.value = f.option_refs.filter((r) => { const e = document.querySelector(`[data-jobfill-id="${r[1]}"]`); return e && e.checked; }).map((r) => r[0]);
        if (f.kind === "radio") f.value = f.value[0] || "";
      }
    }
    if (f.section) {
      const key = f.section + "|" + f.label.toLowerCase();
      f.index = seen.get(key) || 0;
      seen.set(key, f.index + 1);
    }
  }
  return fields;
}
