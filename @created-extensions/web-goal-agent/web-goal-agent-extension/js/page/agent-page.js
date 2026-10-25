/**
 * Web Goal Agent — in-page library.
 *
 * Injected with chrome.scripting.executeScript into the ISOLATED world (the
 * content-script world): it shares the DOM with the page but not its JS, so a
 * hostile page cannot monkey-patch the functions used to observe and verify it.
 * The service worker calls `globalThis.__wga.call(op, args)`; a new document
 * means a fresh world, and the worker re-injects.
 *
 * Responsibilities, kept separate:
 *   observe   — page model: interactive elements, dialogs, alerts, blockers
 *   locate    — resolve semantic locators to ONE element, scoped, with reasons
 *   actionable— visible, enabled, stable, unobstructed; the point to click
 *   evaluate  — expectations (the evidence that an action worked)
 *   overlay   — visual feedback, rendered in the top layer, pointer-events: none
 * Input itself is dispatched by the service worker through CDP (trusted events).
 */
(() => {
  const VERSION = '0.1.0';
  if (globalThis.__wga && globalThis.__wga.version === VERSION) return;

  const HOST_TAG = 'wga-overlay';
  const MASK = '•••';
  const TESTID_ATTRS = ['data-testid', 'data-test-id', 'data-test', 'data-qa', 'data-cy'];
  const INTERACTIVE_ROLES = new Set(['button', 'link', 'textbox', 'searchbox', 'combobox', 'listbox', 'option',
    'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'slider', 'spinbutton',
    'treeitem', 'gridcell']);
  const NAME_FROM_CONTENT = new Set(['button', 'link', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option',
    'tab', 'treeitem', 'heading', 'cell', 'gridcell', 'columnheader', 'rowheader', 'row', 'checkbox', 'radio',
    'switch', 'tooltip', 'listitem', 'status', 'alert']);
  const CLICK_PURPOSES = new Set(['click', 'check', 'hover', 'press', 'type', 'select']);

  // ── Small utilities ───────────────────────────────────────────────────────

  const normalize = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
  const trunc = (s, n = 80) => { s = normalize(s); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // rAF never fires in background tabs; the timer keeps stability checks from hanging there.
  const raf = () => new Promise((r) => { requestAnimationFrame(() => r()); setTimeout(r, 60); });
  const styleOf = (el) => (el.ownerDocument.defaultView || window).getComputedStyle(el);

  function textMatches(actual, m) {
    if (m == null) return true;
    if (typeof m === 'string') m = { equals: m };
    const cs = !!m.case_sensitive;
    const a = cs ? normalize(actual) : normalize(actual).toLowerCase();
    if (m.regex != null) {
      try { return new RegExp(m.regex, cs ? '' : 'i').test(normalize(actual)); } catch { return false; }
    }
    if (m.contains != null) return a.includes(cs ? normalize(m.contains) : normalize(m.contains).toLowerCase());
    if (m.equals != null) return a === (cs ? normalize(m.equals) : normalize(m.equals).toLowerCase());
    return true;
  }
  const matchText = (m) => (m == null ? '' : typeof m === 'string' ? m : (m.equals ?? m.contains ?? m.regex ?? ''));

  function shadowOf(el) {
    if (el.shadowRoot) return el.shadowRoot;
    try { return chrome?.dom?.openOrClosedShadowRoot?.(el) || null; } catch { return null; }
  }

  function frameDocument(el) {
    if (el.localName !== 'iframe' && el.localName !== 'frame') return null;
    try { const d = el.contentDocument; return d && d.documentElement ? d : null; } catch { return null; }
  }

  function isOurs(el) {
    return el && el.localName === HOST_TAG;
  }

  /** The truly focused element: through open AND closed shadow roots and same-origin frames. */
  function deepActiveElement(doc = document) {
    let a = doc.activeElement;
    for (let i = 0; a && i < 20; i++) {
      const sr = shadowOf(a);
      if (sr && sr.activeElement) { a = sr.activeElement; continue; }
      const fd = frameDocument(a);
      if (fd && fd.activeElement && fd.activeElement !== fd.body) { a = fd.activeElement; continue; }
      break;
    }
    return a;
  }

  /** DOM-order walk over elements, descending into shadow roots and same-origin frames. */
  function walk(root, visit) {
    const start = root.nodeType === 9 ? root.documentElement : root;
    const stack = [];
    const pushChildren = (node) => {
      const kids = node.children;
      for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
    };
    if (start.nodeType === 11) pushChildren(start); else if (start) stack.push(start);
    while (stack.length) {
      const el = stack.pop();
      if (isOurs(el)) continue;
      if (visit(el) === false) return;
      const fd = frameDocument(el);
      if (fd) { stack.push(fd.documentElement); continue; }
      const sr = shadowOf(el);
      if (sr) pushChildren(sr);
      pushChildren(el);
    }
  }

  function* elementsIn(root) {
    const out = [];
    walk(root, (el) => { out.push(el); });
    yield* out;
  }

  /** Parent across shadow boundaries and frame boundaries. */
  function composedParent(node) {
    if (node.parentElement) return node.parentElement;
    const root = node.getRootNode && node.getRootNode();
    if (root && root.host) return root.host;
    const doc = node.ownerDocument || node;
    const view = doc && doc.defaultView;
    if (node === doc.documentElement && view && view.frameElement) return view.frameElement;
    return null;
  }

  function composedContains(ancestor, node) {
    for (let n = node; n; n = composedParent(n)) if (n === ancestor) return true;
    return false;
  }

  function closestComposed(el, pred) {
    for (let n = el; n; n = composedParent(n)) if (n.nodeType === 1 && pred(n)) return n;
    return null;
  }

  /** Offset of an element's document within the top document (same-origin frames). */
  function frameOffset(el) {
    let x = 0, y = 0;
    let view = el.ownerDocument.defaultView;
    while (view && view.frameElement) {
      const fe = view.frameElement;
      const r = fe.getBoundingClientRect();
      const s = styleOf(fe);
      x += r.left + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft);
      y += r.top + parseFloat(s.borderTopWidth) + parseFloat(s.paddingTop);
      view = fe.ownerDocument.defaultView;
    }
    return { x, y };
  }

  function topRect(el) {
    const r = el.getBoundingClientRect();
    const o = frameOffset(el);
    return { x: r.left + o.x, y: r.top + o.y, width: r.width, height: r.height };
  }

  /** elementFromPoint that pierces shadow roots and same-origin frames (top-level coords). */
  function deepElementFromPoint(x, y, doc = document) {
    let el = doc.elementFromPoint(x, y);
    while (el) {
      const sr = shadowOf(el);
      if (sr) {
        const inner = sr.elementFromPoint(x, y);
        if (inner && inner !== el) { el = inner; continue; }
      }
      const fd = frameDocument(el);
      if (fd) {
        const r = el.getBoundingClientRect();
        const s = styleOf(el);
        const ix = x - r.left - parseFloat(s.borderLeftWidth) - parseFloat(s.paddingLeft);
        const iy = y - r.top - parseFloat(s.borderTopWidth) - parseFloat(s.paddingTop);
        const inner = fd.elementFromPoint(ix, iy);
        if (inner) { x = ix; y = iy; el = inner; doc = fd; continue; }
      }
      break;
    }
    return el;
  }

  // ── Roles and accessible names (simplified accname) ──────────────────────

  function getRole(el) {
    const explicit = el.getAttribute && el.getAttribute('role');
    if (explicit && explicit.trim()) return explicit.trim().split(/\s+/)[0].toLowerCase();
    const tag = el.localName;
    switch (tag) {
      case 'button': return 'button';
      case 'summary': return 'button';
      case 'a': case 'area': return el.hasAttribute('href') ? 'link' : '';
      case 'input': {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (['button', 'submit', 'reset', 'image'].includes(t)) return 'button';
        if (t === 'checkbox') return el.getAttribute('switch') !== null ? 'switch' : 'checkbox';
        if (t === 'radio') return 'radio';
        if (t === 'range') return 'slider';
        if (t === 'number') return 'spinbutton';
        if (t === 'hidden' || t === 'file' || t === 'color') return '';
        if (t === 'search') return el.hasAttribute('list') ? 'combobox' : 'searchbox';
        return el.hasAttribute('list') ? 'combobox' : 'textbox'; // includes password (by design: findable by label)
      }
      case 'textarea': return 'textbox';
      case 'select': return (el.multiple || el.size > 1) ? 'listbox' : 'combobox';
      case 'option': return 'option';
      case 'img': return el.getAttribute('alt') === '' ? 'presentation' : 'img';
      case 'h1': case 'h2': case 'h3': case 'h4': case 'h5': case 'h6': return 'heading';
      case 'nav': return 'navigation';
      case 'main': return 'main';
      case 'aside': return 'complementary';
      case 'form': return 'form';
      case 'section': return (el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby')) ? 'region' : '';
      case 'article': return 'article';
      case 'dialog': return 'dialog';
      case 'ul': case 'ol': case 'menu': return 'list';
      case 'li': return 'listitem';
      case 'table': return 'table';
      case 'tr': return 'row';
      case 'td': return 'cell';
      case 'th': return el.getAttribute('scope') === 'row' ? 'rowheader' : 'columnheader';
      case 'thead': case 'tbody': case 'tfoot': return 'rowgroup';
      case 'fieldset': case 'details': return 'group';
      case 'progress': return 'progressbar';
      case 'output': return 'status';
      case 'header': return closestComposed(el.parentElement || el, (n) => ['article', 'section', 'main', 'aside', 'nav'].includes(n.localName)) ? '' : 'banner';
      case 'footer': return closestComposed(el.parentElement || el, (n) => ['article', 'section', 'main', 'aside', 'nav'].includes(n.localName)) ? '' : 'contentinfo';
      default: break;
    }
    if (el.isContentEditable && el.hasAttribute('contenteditable')) return 'textbox';
    return '';
  }

  function renderedText(node, skip, depth = 0) {
    if (depth > 30) return '';
    let out = '';
    for (const child of node.childNodes) {
      if (child.nodeType === 3) { out += child.data; continue; }
      if (child.nodeType !== 1 || child === skip || isOurs(child)) continue;
      const tag = child.localName;
      if (tag === 'script' || tag === 'style' || tag === 'template' || tag === 'noscript') continue;
      if (child.getAttribute('aria-hidden') === 'true') continue;
      const s = styleOf(child);
      if (s.display === 'none' || s.visibility === 'hidden') continue;
      const block = s.display !== 'inline' && s.display !== 'contents';
      let piece;
      if (tag === 'img') piece = child.getAttribute('alt') || '';
      else if (tag === 'input' || tag === 'textarea' || tag === 'select') piece = '';
      else if (child.getAttribute('aria-label')) piece = child.getAttribute('aria-label');
      else {
        const sr = shadowOf(child);
        piece = sr ? renderedText(sr, skip, depth + 1) + ' ' + renderedText(child, skip, depth + 1)
          : renderedText(child, skip, depth + 1);
      }
      out += block ? ` ${piece} ` : piece;
    }
    return out;
  }

  function byIdRef(el, id) {
    const root = el.getRootNode();
    return (root.getElementById && root.getElementById(id)) || el.ownerDocument.getElementById(id);
  }

  function labelText(el) {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const s = normalize(lb.split(/\s+/).map((id) => byIdRef(el, id)).filter(Boolean).map((n) => renderedText(n)).join(' '));
      if (s) return s;
    }
    const al = el.getAttribute('aria-label');
    if (al && normalize(al)) return normalize(al);
    if (el.labels && el.labels.length) {
      const s = normalize([...el.labels].map((l) => renderedText(l, el)).join(' '));
      if (s) return s;
    }
    return '';
  }

  function accName(el) {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const s = normalize(lb.split(/\s+/).map((id) => byIdRef(el, id)).filter(Boolean).map((n) => renderedText(n)).join(' '));
      if (s) return s;
    }
    const al = el.getAttribute('aria-label');
    if (al && normalize(al)) return normalize(al);
    const tag = el.localName;
    const role = getRole(el);
    if (tag === 'input') {
      const t = (el.type || '').toLowerCase();
      if (t === 'button' || t === 'submit' || t === 'reset') return normalize(el.value || (t === 'submit' ? 'Submit' : t === 'reset' ? 'Reset' : ''));
      if (t === 'image') return normalize(el.alt || el.value || '');
    }
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      const l = labelText(el);
      if (l) return l;
      return normalize(el.getAttribute('title') || el.getAttribute('placeholder') || '');
    }
    if (tag === 'img') return normalize(el.getAttribute('alt') || el.getAttribute('title') || '');
    if (role === 'dialog' || role === 'alertdialog') {
      const h = el.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]');
      if (h) return normalize(renderedText(h));
    }
    if (tag === 'fieldset') { const lg = el.querySelector(':scope > legend'); if (lg) return normalize(renderedText(lg)); }
    if (tag === 'table') { const cap = el.querySelector(':scope > caption'); if (cap) return normalize(renderedText(cap)); }
    if (NAME_FROM_CONTENT.has(role)) {
      const sr = shadowOf(el);
      const s = normalize((sr ? renderedText(sr) + ' ' : '') + renderedText(el));
      if (s) return s;
    }
    return normalize(el.getAttribute('title') || '');
  }

  // ── Element state ─────────────────────────────────────────────────────────

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) {
      if (styleOf(el).display === 'contents') return [...el.children].some(isVisible);
      return false;
    }
    if (el.checkVisibility) {
      if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    } else {
      const s = styleOf(el);
      if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) return false;
    }
    // Inside a same-origin frame: the frame itself must be visible.
    const fe = el.ownerDocument.defaultView?.frameElement;
    return fe ? isVisible(fe) : true;
  }

  function isInert(el) {
    if (closestComposed(el, (n) => n.hasAttribute && n.hasAttribute('inert'))) return true;
    const modal = el.ownerDocument.querySelector('dialog:modal');
    return !!(modal && !composedContains(modal, el) && !inTopLayer(el));
  }

  function inTopLayer(el) {
    return !!closestComposed(el, (n) => { try { return n.matches(':popover-open, :modal'); } catch { return false; } });
  }

  function isEnabled(el) {
    if ('disabled' in el && el.disabled) return false;
    if (el.getAttribute('aria-disabled') === 'true') return false;
    const fs = el.closest && el.closest('fieldset[disabled]');
    if (fs) {
      const legend = fs.querySelector(':scope > legend');
      if (!(legend && legend.contains(el))) return false;
    }
    return !isInert(el);
  }

  function isEditable(el) {
    const tag = el.localName;
    if (tag === 'textarea') return !el.readOnly && !el.disabled;
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      return !el.readOnly && !el.disabled && !['button', 'submit', 'reset', 'checkbox', 'radio', 'file', 'image', 'hidden', 'range', 'color'].includes(t);
    }
    return !!el.isContentEditable;
  }

  function valueOf(el) {
    const tag = el.localName;
    if (tag === 'input' || tag === 'textarea') return el.value;
    if (tag === 'select') return el.multiple ? [...el.selectedOptions].map((o) => o.value).join(',') : el.value;
    if (el.isContentEditable) return el.innerText;
    const v = el.getAttribute('aria-valuetext') || el.getAttribute('aria-valuenow');
    return v != null ? v : null;
  }

  function isSecretField(el) {
    return el.localName === 'input' && (el.type || '').toLowerCase() === 'password';
  }

  function displayValue(el) {
    const v = valueOf(el);
    if (v == null) return null;
    return isSecretField(el) ? (v ? MASK : '') : trunc(v, 120);
  }

  function isChecked(el) {
    if (el.localName === 'input' && (el.type === 'checkbox' || el.type === 'radio')) return el.checked;
    const a = el.getAttribute('aria-checked') ?? el.getAttribute('aria-pressed') ?? el.getAttribute('aria-selected');
    return a === 'true';
  }

  function isDialogEl(el) {
    const r = getRole(el);
    return r === 'dialog' || r === 'alertdialog';
  }

  function isModal(el) {
    try { if (el.localName === 'dialog' && el.matches(':modal')) return true; } catch { /* old engine */ }
    return el.getAttribute('aria-modal') === 'true';
  }

  const PAYMENT_RE = /\b(cc-|card|cvv|cvc|iban|expir|security.?code)/i;

  function context(el) {
    const dialog = closestComposed(el, (n) => n !== el && isDialogEl(n)) || (isDialogEl(el) ? el : null);
    const form = el.form || el.closest?.('form') || null;
    const fields = form ? [...form.querySelectorAll('input, select, textarea')] : [];
    const isPayment = (f) => PAYMENT_RE.test(`${f.getAttribute('autocomplete') || ''} ${f.name || ''} ${f.id || ''}`);
    const t = (el.type || '').toLowerCase();
    return {
      role: getRole(el), name: trunc(accName(el), 120), text: trunc(renderedText(el), 120), tag: el.localName,
      type: el.localName === 'input' ? t : undefined,
      dialog_name: dialog ? trunc(accName(dialog), 120) : undefined,
      dialog_role: dialog ? getRole(dialog) : undefined,
      submits: (el.localName === 'button' && (t === 'submit' || (!el.getAttribute('type') && !!form))) || (el.localName === 'input' && t === 'submit'),
      opens_popup: el.getAttribute('aria-haspopup') || undefined,
      payment_field: el.localName === 'input' ? isPayment(el) : false,
      form: form ? { has_password: fields.some(isSecretField), has_payment_fields: fields.some(isPayment) } : undefined,
    };
  }

  function containerName(el) {
    const c = closestComposed(el, (n) => n !== el && ['row', 'listitem', 'article', 'region', 'form', 'group'].includes(getRole(n)));
    return c ? `${getRole(c)}: ${trunc(accName(c) || renderedText(c), 60)}` : undefined;
  }

  // ── Handles (stable references between calls, re-validated on use) ───────

  const handles = new Map();
  const handleOf = new WeakMap();
  let handleSeq = 0;

  function handleFor(el) {
    let id = handleOf.get(el);
    if (id && handles.has(id)) return id;
    id = `h${++handleSeq}`;
    handles.set(id, new WeakRef(el));
    handleOf.set(el, id);
    if (handles.size > 800) handles.delete(handles.keys().next().value);
    return id;
  }

  function deref(id) {
    const ref = id && handles.get(id);
    const el = ref && ref.deref();
    return el && el.isConnected ? el : null;
  }

  function summarize(el) {
    const r = topRect(el);
    const dialog = closestComposed(el, (n) => n !== el && isDialogEl(n));
    return {
      handle: handleFor(el), role: getRole(el), name: trunc(accName(el)), tag: el.localName,
      type: el.localName === 'input' ? (el.type || 'text') : undefined,
      text: trunc(renderedText(el), 80) || undefined,
      testid: TESTID_ATTRS.map((a) => el.getAttribute(a)).find(Boolean) || undefined,
      id: el.id || undefined,
      value: displayValue(el) ?? undefined,
      state: {
        disabled: !isEnabled(el) || undefined,
        checked: (['checkbox', 'radio', 'switch', 'menuitemcheckbox', 'option', 'tab'].includes(getRole(el)) ? isChecked(el) : undefined),
        expanded: el.getAttribute('aria-expanded') ?? undefined,
      },
      dialog: dialog ? trunc(accName(dialog), 60) : undefined,
      container: containerName(el),
      in_frame: el.ownerDocument !== document || undefined,
      in_shadow: el.getRootNode() instanceof ShadowRoot || undefined,
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
    };
  }

  // ── Dialogs and scope ─────────────────────────────────────────────────────

  function visibleDialogs() {
    const out = [];
    walk(document, (el) => { if (isDialogEl(el) && isVisible(el)) out.push(el); });
    return out;
  }

  function activeDialog() {
    const modals = visibleDialogs().filter(isModal);
    if (!modals.length) return null;
    if (modals.length === 1) return modals[0];
    const onTop = modals.filter((d) => {
      const r = topRect(d);
      const hit = deepElementFromPoint(r.x + r.width / 2, r.y + Math.min(r.height / 2, 40));
      return hit && composedContains(d, hit);
    });
    const pool = onTop.length ? onTop : modals;
    return pool[pool.length - 1];
  }

  function describeDialog(d) {
    return { name: trunc(accName(d), 80), role: getRole(d), modal: isModal(d), handle: handleFor(d) };
  }

  /**
   * Resolve a locator's `within`. `auto` applies only to action targets: with a
   * modal open, a human only interacts with that modal, so the search starts there.
   */
  function resolveScope(within, auto) {
    if (within == null) {
      if (auto) {
        const d = activeDialog();
        if (d) return { ok: true, el: d, kind: 'dialog', auto: true, label: `dialog "${trunc(accName(d), 60)}"` };
      }
      return { ok: true, el: document.documentElement, kind: 'page', label: 'the page' };
    }
    if (within === 'page') return { ok: true, el: document.documentElement, kind: 'page', label: 'the page' };
    if (within === 'active-dialog') {
      const d = activeDialog();
      return d ? { ok: true, el: d, kind: 'dialog', label: `dialog "${trunc(accName(d), 60)}"` }
        : { ok: false, code: 'no_active_dialog', message: 'no modal dialog is open' };
    }
    const r = resolveAll(within, false);
    if (!r.ok) return { ...r, code: r.code === 'invalid_locator' ? r.code : 'scope_not_found' };
    if (r.elements.length === 0) {
      return { ok: false, code: 'scope_not_found', message: `container not found: ${describeLocator(within)}` };
    }
    if (r.elements.length > 1 && within.nth == null) {
      return { ok: false, code: 'ambiguous', scope: true, count: r.elements.length,
        message: `${r.elements.length} containers match ${describeLocator(within)}`,
        candidates: r.elements.slice(0, 6).map(summarize) };
    }
    const el = r.elements[within.nth ?? 0];
    if (!el) return { ok: false, code: 'scope_not_found', message: `no container #${within.nth}` };
    const kind = isDialogEl(el) ? 'dialog' : 'container';
    return { ok: true, el, kind, label: `${kind === 'dialog' ? 'dialog' : getRole(el) || el.localName} "${trunc(accName(el) || renderedText(el), 60)}"` };
  }

  function describeLocator(loc) {
    if (!loc) return 'the page';
    if (typeof loc === 'string') return loc === 'active-dialog' ? 'the active dialog' : 'the page';
    if (loc.any) return describeLocator(loc.any[0]);
    let base;
    if (loc.role && loc.name) base = `the "${matchText(loc.name)}" ${loc.role}`;
    else if (loc.label) base = `the "${matchText(loc.label)}" field`;
    else if (loc.placeholder) base = `the field "${matchText(loc.placeholder)}"`;
    else if (loc.text) base = `"${matchText(loc.text)}"`;
    else if (loc.testid) base = `[data-testid=${loc.testid}]`;
    else if (loc.role) base = `the ${loc.role}`;
    else if (loc.id) base = `#${loc.id}`;
    else base = loc.css || loc.xpath || 'the element';
    if (!loc.within || loc.within === 'page') return base;
    return `${base} inside ${describeLocator(loc.within)}`;
  }

  // ── Locator resolution ────────────────────────────────────────────────────

  function cssAll(scope, css) {
    const out = [];
    const roots = [scope];
    walk(scope, (el) => { const sr = shadowOf(el); if (sr) roots.push(sr); const fd = frameDocument(el); if (fd) roots.push(fd); });
    for (const root of roots) {
      try { out.push(...root.querySelectorAll(css)); } catch (e) { throw new Error(`invalid css: ${e.message}`); }
    }
    return [...new Set(out)];
  }

  function xpathAll(scope, xpath) {
    const doc = scope.ownerDocument || scope;
    let res;
    try { res = doc.evaluate(xpath, scope, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null); }
    catch (e) { throw new Error(`invalid xpath: ${e.message}`); }
    const out = [];
    for (let i = 0; i < res.snapshotLength; i++) { const n = res.snapshotItem(i); if (n.nodeType === 1) out.push(n); }
    return out;
  }

  const CLICKABLE_ANCESTOR = (n) => ['button', 'link', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'tab', 'option', 'checkbox', 'radio', 'switch', 'treeitem'].includes(getRole(n));

  function byText(scope, m) {
    const needle = normalize(matchText(m)).toLowerCase();
    const hits = [];
    walk(scope, (el) => {
      if (el === scope) return;
      const tc = (el.textContent || '').toLowerCase();
      if (m.regex == null && needle && !normalize(tc).includes(needle)) return;
      if (textMatches(renderedText(el), m)) hits.push(el);
    });
    // Keep the innermost matches, then lift each to its clickable ancestor if it has one.
    const innermost = hits.filter((el) => !hits.some((o) => o !== el && composedContains(el, o)));
    const lifted = innermost.map((el) => {
      if (CLICKABLE_ANCESTOR(el)) return el;
      const up = closestComposed(el, (n) => n !== el && CLICKABLE_ANCESTOR(n));
      return up && composedContains(scope, up) ? up : el;
    });
    return [...new Set(lifted)];
  }

  function matchesFilters(el, loc) {
    if (loc.id != null && el.id !== loc.id) return false;
    if (loc.testid != null && !TESTID_ATTRS.some((a) => el.getAttribute(a) === loc.testid)) return false;
    if (loc.role != null && getRole(el) !== loc.role.toLowerCase()) return false;
    if (loc.name != null && !textMatches(accName(el), loc.name)) return false;
    if (loc.label != null) {
      const isControl = ['input', 'textarea', 'select'].includes(el.localName) || el.isContentEditable || INTERACTIVE_ROLES.has(getRole(el));
      if (!isControl || !textMatches(labelText(el) || '', loc.label)) return false;
    }
    if (loc.placeholder != null && !textMatches(el.getAttribute('placeholder') || el.getAttribute('aria-placeholder') || '', loc.placeholder)) return false;
    return true;
  }

  /**
   * All visible matches of a locator, in DOM order. `auto` enables the
   * active-dialog default scope (action targets only).
   */
  function resolveAll(loc, auto) {
    if (!loc || typeof loc !== 'object') return { ok: false, code: 'invalid_locator', message: 'locator must be an object' };
    if (loc.any) {
      let firstHidden = 0;
      for (let i = 0; i < loc.any.length; i++) {
        const r = resolveAll(loc.any[i], auto);
        if (!r.ok && r.code !== 'scope_not_found' && r.code !== 'no_active_dialog') return r;
        if (r.ok && r.elements.length) return { ...r, alternative: i };
        firstHidden += r.hidden || 0;
      }
      return { ok: true, elements: [], hidden: firstHidden, scope: { kind: 'page', label: 'the page' } };
    }
    const scope = resolveScope(loc.within, auto);
    if (!scope.ok) return scope;
    let pool;
    try {
      if (loc.css) pool = cssAll(scope.el, loc.css);
      else if (loc.xpath) pool = xpathAll(scope.el, loc.xpath);
      else if (loc.text != null) pool = byText(scope.el, typeof loc.text === 'string' ? { equals: loc.text } : loc.text);
      else pool = [...elementsIn(scope.el)].filter((el) => el !== scope.el);
    } catch (e) {
      return { ok: false, code: 'invalid_locator', message: e.message };
    }
    const matched = pool.filter((el) => !isOurs(el) && composedContains(scope.el, el) && matchesFilters(el, loc));
    // Walk order is DOM order across shadow roots and frames. A visually-hidden
    // checkbox/radio still counts when its label is visible (custom-styled inputs).
    const elements = matched.filter((el) => isVisible(el) || labelProxy(el));
    return { ok: true, elements, hidden: matched.length - elements.length, scope: { kind: scope.kind, label: scope.label, auto: !!scope.auto, el: scope.el } };
  }

  function labelProxy(el) {
    if (el.localName !== 'input' || !['checkbox', 'radio'].includes((el.type || '').toLowerCase())) return null;
    return [...(el.labels || [])].find(isVisible) || null;
  }

  // ── Actionability ─────────────────────────────────────────────────────────

  function fullyInViewport(r) {
    return r.y >= 0 && r.x >= 0 && r.y + r.height <= window.innerHeight && r.x + r.width <= window.innerWidth;
  }

  function hitOk(target, hit) {
    if (!hit) return false;
    if (hit === target || composedContains(target, hit)) return true;
    if (target.labels && [...target.labels].some((l) => l === hit || composedContains(l, hit))) return true;
    return false;
  }

  async function actionable(el, purpose) {
    if (!el || !el.isConnected) return { ok: false, code: 'stale', message: 'the element was removed from the page' };
    let clickEl = el;
    if (!isVisible(el)) {
      const proxy = labelProxy(el);
      if (!proxy) return { ok: false, code: 'hidden', message: 'the element exists but is not visible' };
      clickEl = proxy;
    }
    if (CLICK_PURPOSES.has(purpose) && purpose !== 'hover' && !isEnabled(el)) {
      return { ok: false, code: 'disabled', message: 'the element is disabled' };
    }
    if (purpose === 'type' && !isEditable(el)) return { ok: false, code: 'not_editable', message: 'the element does not accept text' };
    if (!fullyInViewport(topRect(clickEl))) clickEl.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    const r1 = topRect(clickEl);
    await raf(); await raf();
    if (!clickEl.isConnected) return { ok: false, code: 'stale', message: 'the element was removed while settling' };
    const r = topRect(clickEl);
    if (Math.abs(r.x - r1.x) > 1 || Math.abs(r.y - r1.y) > 1 || Math.abs(r.width - r1.width) > 1) {
      return { ok: false, code: 'unstable', message: 'the element is still moving (animation/layout shift)' };
    }
    const points = [[0.5, 0.5], [0.25, 0.5], [0.75, 0.5], [0.5, 0.25], [0.5, 0.75]];
    let lastHit = null;
    for (const [fx, fy] of points) {
      const x = r.x + r.width * fx, y = r.y + r.height * fy;
      if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) continue;
      const hit = deepElementFromPoint(x, y);
      if (hitOk(clickEl, hit) || hitOk(el, hit)) {
        return { ok: true, point: { x: Math.round(x), y: Math.round(y) }, rect: r, via_label: clickEl !== el };
      }
      lastHit = hit;
    }
    const blocker = lastHit ? closestComposed(lastHit, (n) => isDialogEl(n) || ['banner', 'alert', 'status', 'region', 'complementary'].includes(getRole(n))) || lastHit : null;
    return {
      ok: false, code: 'obstructed',
      message: `covered by ${blocker ? describeEl(blocker) : 'another element'}`,
      obstructed_by: blocker ? describeEl(blocker) : 'another element',
    };
  }

  function describeEl(el) {
    const role = getRole(el);
    const name = trunc(accName(el) || renderedText(el), 50);
    return `${role || el.localName}${name ? ` "${name}"` : ''}`;
  }

  // ── Expectations ──────────────────────────────────────────────────────────

  function visibleText(scope) {
    const el = scope || document.body;
    let text = el.innerText || '';
    walk(el, (n) => {
      const sr = shadowOf(n);
      if (sr) for (const c of sr.children) if (c.innerText) text += '\n' + c.innerText;
      const fd = frameDocument(n);
      if (fd && fd.body && isVisible(n)) text += '\n' + fd.body.innerText;
    });
    return text;
  }

  function scopeForText(within) {
    if (within == null || within === 'page') return { ok: true, el: document.body };
    const s = resolveScope(within, false);
    return s.ok ? { ok: true, el: s.el } : s;
  }

  /** Controls (value/checked/focus/attribute) resolve like action targets: active dialog first. */
  function firstMatch(loc) {
    const r = resolveAll(loc, true);
    if (!r.ok) return { error: r };
    return { el: r.elements[loc.nth ?? 0] || null, count: r.elements.length, hidden: r.hidden };
  }

  function compareString(actual, args) {
    if (actual == null) return false;
    if (args.equals != null) return actual === args.equals || normalize(actual) === normalize(args.equals);
    if (args.contains != null) return normalize(actual).toLowerCase().includes(normalize(args.contains).toLowerCase());
    if (args.regex != null) { try { return new RegExp(args.regex).test(actual); } catch { return false; } }
    return true;
  }

  function evaluate(exp, ctx) {
    const { kind, args = {} } = exp;
    const res = (ok, actual, message) => ({ kind, ok, actual, message: ok ? undefined : message });
    try {
      switch (kind) {
        case 'url': {
          const href = location.href;
          if (args.changed) return res(href !== ctx.baseline_url, href, 'the URL did not change');
          return res(compareString(href, args), href, `URL does not match ${matchText(args) || JSON.stringify(args)}`);
        }
        case 'title': return res(textMatches(document.title, args), document.title, 'title does not match');
        case 'visible': case 'hidden': {
          const r = resolveAll(args, false);
          if (!r.ok) return res(kind === 'hidden' && r.code === 'scope_not_found', null, r.message);
          const n = r.elements.length;
          return kind === 'visible' ? res(n > 0, n, `${describeLocator(args)} is not visible`)
            : res(n === 0, n, `${describeLocator(args)} is still visible`);
        }
        case 'enabled': case 'disabled': {
          const r = resolveAll(args, false);
          if (!r.ok || !r.elements.length) return res(false, 0, `${describeLocator(args)} not found`);
          const want = kind === 'enabled';
          return res(r.elements.every((el) => isEnabled(el) === want), r.elements.map(isEnabled), `${describeLocator(args)} is not ${kind}`);
        }
        case 'focused': {
          const m = firstMatch(args);
          const active = deepActiveElement();
          return res(!!(m.el && active && (active === m.el || composedContains(m.el, active))), active ? describeEl(active) : null, `${describeLocator(args)} is not focused`);
        }
        case 'dialog_open': case 'dialog_closed': {
          const ds = visibleDialogs().filter((d) => (!args.role || getRole(d) === args.role) && textMatches(accName(d), args.name));
          const names = visibleDialogs().map((d) => trunc(accName(d), 40));
          const label = args.name ? `dialog "${matchText(args.name)}"` : 'a dialog';
          return kind === 'dialog_open' ? res(ds.length > 0, names, `${label} is not open (open: ${names.join(', ') || 'none'})`)
            : res(ds.length === 0, names, `${label} is still open`);
        }
        case 'text_present': case 'text_absent': {
          const s = scopeForText(args.within);
          if (!s.ok) return res(kind === 'text_absent', null, s.message);
          const m = { ...args.text };
          if (m.equals != null) { m.contains = m.equals; delete m.equals; } // "is shown on the page"
          const found = textMatches(visibleText(s.el), m);
          return kind === 'text_present' ? res(found, undefined, `text "${matchText(args.text)}" not found`)
            : res(!found, undefined, `text "${matchText(args.text)}" is still shown`);
        }
        case 'value': {
          const m = firstMatch(args.target);
          if (m.error || !m.el) return res(false, null, `${describeLocator(args.target)} not found`);
          let actual = valueOf(m.el);
          let ok = compareString(actual, args);
          if (!ok && m.el.localName === 'select' && m.el.selectedOptions[0]) ok = compareString(m.el.selectedOptions[0].text, args);
          const shown = isSecretField(m.el) ? (actual ? MASK : '') : trunc(actual, 120);
          return res(ok, shown, `field value does not match`);
        }
        case 'checked': {
          const m = firstMatch(args.target);
          if (m.error || !m.el) return res(false, null, `${describeLocator(args.target)} not found`);
          const c = isChecked(m.el);
          return res(c === (args.value !== false), c, `checked is ${c}`);
        }
        case 'attribute': {
          const m = firstMatch(args.target);
          if (m.error || !m.el) return res(false, null, `${describeLocator(args.target)} not found`);
          const v = m.el.getAttribute(args.name);
          if (args.present != null) return res((v != null) === args.present, v, `attribute ${args.name} presence is ${v != null}`);
          return res(compareString(v, args), v, `attribute ${args.name} is ${JSON.stringify(v)}`);
        }
        case 'count': {
          const r = resolveAll(args.target, false);
          const n = r.ok ? r.elements.length : 0;
          const ok = (args.eq == null || n === args.eq) && (args.gte == null || n >= args.gte) && (args.lte == null || n <= args.lte)
            && (args.gt == null || n > args.gt) && (args.lt == null || n < args.lt);
          return res(ok, n, `count is ${n}`);
        }
        default: return res(false, null, `unknown expectation ${kind}`);
      }
    } catch (e) {
      return res(false, null, `evaluation error: ${e.message}`);
    }
  }

  function alerts() {
    const out = [];
    walk(document, (el) => {
      const role = getRole(el);
      if ((role === 'alert' || el.getAttribute('aria-live') === 'assertive') && isVisible(el)) {
        const t = trunc(renderedText(el), 200);
        if (t) out.push(t);
      } else if (el.getAttribute('aria-invalid') === 'true' && isVisible(el)) {
        const ids = `${el.getAttribute('aria-errormessage') || ''} ${el.getAttribute('aria-describedby') || ''}`.trim();
        const msg = ids ? ids.split(/\s+/).map((id) => byIdRef(el, id)).filter((n) => n && isVisible(n)).map((n) => renderedText(n)).join(' ') : '';
        out.push(trunc(`${accName(el) || 'field'}: ${msg || 'invalid'}`, 200));
      }
    });
    return [...new Set(out)].slice(0, 10);
  }

  function checkAll(expect, ctx) {
    const results = (expect || []).map((e) => evaluate(e, ctx));
    return { ok: results.every((r) => r.ok), results };
  }

  /** Resolves on the next DOM mutation (ignoring our overlay) or after `ms`. */
  function changeOrTimeout(ms) {
    return new Promise((resolve) => {
      let done = false;
      const finish = () => { if (!done) { done = true; mo.disconnect(); clearTimeout(t); resolve(); } };
      const mo = new MutationObserver((records) => { if (records.some((r) => !isOurs(r.target))) finish(); });
      mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
      const t = setTimeout(finish, ms);
    });
  }

  async function waitFor({ expect, timeout_ms = 2000, baseline_url, label }) {
    const deadline = Date.now() + timeout_ms;
    const ctx = { baseline_url };
    if (label) overlay({ op: 'phase', state: 'wait', text: label });
    let res = checkAll(expect, ctx);
    while (!res.ok && Date.now() < deadline) {
      await changeOrTimeout(Math.min(250, deadline - Date.now()));
      res = checkAll(expect, ctx);
    }
    return { ...res, url: location.href, title: document.title, alerts: alerts() };
  }

  const BUSY_SELECTOR = '[aria-busy="true"], [role="progressbar"], .spinner, .loading, .loader, [class*="skeleton"], [class*="shimmer"]';

  function busyIndicators() {
    const out = [];
    walk(document, (el) => {
      if (el === document.body || el === document.documentElement) return;
      if (el.matches && el.matches(BUSY_SELECTOR) && isVisible(el)) out.push(describeEl(el));
    });
    return out.slice(0, 5);
  }

  async function settle({ timeout_ms = 4000, quiet_ms = 300 }) {
    const start = Date.now();
    const deadline = start + timeout_ms;
    let lastMutation = Date.now();
    const mo = new MutationObserver((records) => { if (records.some((r) => !isOurs(r.target))) lastMutation = Date.now(); });
    mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
    try {
      while (Date.now() < deadline) {
        const busy = busyIndicators();
        const ready = document.readyState === 'complete';
        if (ready && !busy.length && Date.now() - lastMutation >= quiet_ms) {
          return { settled: true, waited_ms: Date.now() - start };
        }
        await sleep(100);
      }
      return { settled: false, waited_ms: Date.now() - start, pending: { busy: busyIndicators(), ready_state: document.readyState, dom_quiet_ms: Date.now() - lastMutation } };
    } finally { mo.disconnect(); }
  }

  // ── Operations called by the service worker ───────────────────────────────

  async function locate({ locator, purpose = 'click', timeout_ms = 1500, label }) {
    const deadline = Date.now() + timeout_ms;
    let last = { ok: false, code: 'not_found', message: 'not found' };
    if (label) overlay({ op: 'phase', state: 'inspect', text: label });
    while (true) {
      last = await locateOnce(locator, purpose);
      if (last.ok || last.code === 'ambiguous' || last.code === 'invalid_locator' || Date.now() >= deadline) break;
      await changeOrTimeout(Math.min(250, Math.max(0, deadline - Date.now())));
    }
    return { ...last, url: location.href, alerts: alerts() };
  }

  async function locateOnce(locator, purpose) {
    const r = resolveAll(locator, true);
    if (!r.ok) return r;
    if (r.scope && r.scope.kind !== 'page') overlay({ op: 'scope', el: r.scope.el, text: `Inspecting ${r.scope.label}` });
    const n = r.elements.length;
    if (n === 0) {
      return r.hidden ? { ok: false, code: 'hidden', message: `${describeLocator(locator)} exists but is hidden`, scope: r.scope.label }
        : { ok: false, code: 'not_found', message: `no element matches ${describeLocator(locator)} in ${r.scope.label}`, scope: r.scope.label };
    }
    if (n > 1 && locator.nth == null) {
      const candidates = r.elements.slice(0, 6).map(summarize);
      overlay({ op: 'candidates', els: r.elements.slice(0, 6), text: `${n} matches — ambiguous` });
      return { ok: false, code: 'ambiguous', count: n, scope: r.scope.label, message: `${n} elements match ${describeLocator(locator)} in ${r.scope.label}`, candidates };
    }
    const el = r.elements[locator.nth ?? 0];
    if (!el) return { ok: false, code: 'not_found', message: `fewer than ${locator.nth + 1} matches` };
    const a = await actionable(el, purpose);
    if (!a.ok) {
      overlay({ op: 'target', el, state: 'wait', text: `${describeLocator(locator)}: ${a.message}` });
      return { ...a, handle: handleFor(el), scope: r.scope.label };
    }
    overlay({ op: 'target', el, state: 'inspect', text: `Found ${describeLocator(locator)}` });
    return { ok: true, handle: handleFor(el), point: a.point, via_label: a.via_label, scope: r.scope.label,
      element: summarize(el), target: context(el) };
  }

  /** Immediately before input: re-validate the handle (or re-resolve a stale one). */
  async function prepare({ handle, locator, purpose }) {
    let el = deref(handle);
    let refreshed = false;
    if (!el && locator) {
      const r = resolveAll(locator, true);
      if (r.ok && r.elements.length === 1) { el = r.elements[0]; refreshed = true; }
    }
    if (!el) return { ok: false, code: 'stale', message: 'the element was replaced on the page' };
    const a = await actionable(el, purpose);
    if (!a.ok) return a;
    return { ok: true, handle: handleFor(el), point: a.point, refreshed, editable: isEditable(el),
      secret: isSecretField(el), tag: el.localName, content_editable: !!el.isContentEditable,
      checked: isChecked(el), url: location.href };
  }

  function focusInfo({ handle }) {
    const el = deref(handle);
    if (!el) return { ok: false, code: 'stale' };
    const isFocused = () => { const a = deepActiveElement(); return !!a && (a === el || composedContains(el, a)); };
    if (!isFocused()) el.focus({ preventScroll: true });
    const focused = isFocused();
    return { ok: focused, code: focused ? undefined : 'focus_failed' };
  }

  function readValue({ handle }) {
    const el = deref(handle);
    if (!el) return { ok: false, code: 'stale' };
    const v = valueOf(el) ?? '';
    return { ok: true, length: v.length, empty: v.length === 0 };
  }

  function valueMatches({ handle, value, mode = 'equals' }) {
    const el = deref(handle);
    if (!el) return { ok: false, code: 'stale' };
    const v = valueOf(el) ?? '';
    return { ok: mode === 'contains' ? v.includes(value) : (v === value || normalize(v) === normalize(value)) };
  }

  /** React-compatible fallback when trusted insertText didn't land: native setter + events. */
  function setValueNative({ handle, value }) {
    const el = deref(handle);
    if (!el) return { ok: false, code: 'stale' };
    const proto = el.localName === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (!setter || el.isContentEditable) return { ok: false, code: 'not_supported' };
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true };
  }

  function selectAllIn({ handle }) {
    const el = deref(handle);
    if (!el) return { ok: false, code: 'stale' };
    if (typeof el.select === 'function') { el.select(); return { ok: true }; }
    const sel = el.ownerDocument.getSelection();
    const range = el.ownerDocument.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges(); sel.addRange(range);
    return { ok: true };
  }

  function selectOption({ handle, value }) {
    const el = deref(handle);
    if (!el) return { ok: false, code: 'stale' };
    if (el.localName !== 'select') return { ok: false, code: 'not_supported', terminal: true, message: 'not a native <select>; use click steps for custom dropdowns' };
    const want = normalize(value).toLowerCase();
    const opt = [...el.options].find((o) => o.value === value) || [...el.options].find((o) => normalize(o.text).toLowerCase() === want);
    if (!opt) return { ok: false, code: 'option_not_found', terminal: true, message: `no option "${value}" (have: ${[...el.options].map((o) => o.text).slice(0, 8).join(', ')})` };
    if (opt.disabled) return { ok: false, code: 'disabled', message: `option "${value}" is disabled` };
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
    setter.call(el, opt.value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true };
  }

  function focusEl({ handle }) {
    const el = deref(handle);
    if (!el) return { ok: false, code: 'stale' };
    el.focus({ preventScroll: false });
    return { ok: true };
  }

  async function scrollSearch({ locator, max_scrolls = 15, label }) {
    if (label) overlay({ op: 'phase', state: 'wait', text: label });
    const containers = [];
    const scope = resolveScope(locator.within, true);
    const root = scope.ok ? scope.el : document.documentElement;
    walk(root, (el) => {
      if (el.scrollHeight > el.clientHeight + 4 && isVisible(el)) {
        const oy = styleOf(el).overflowY;
        if (oy === 'auto' || oy === 'scroll') containers.push(el);
      }
    });
    containers.sort((a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth);
    containers.push(document.scrollingElement || document.documentElement);
    let scrolls = 0;
    for (const c of containers) {
      overlay({ op: 'scope', el: c === document.scrollingElement ? null : c, text: 'Scrolling to load more content' });
      while (scrolls < max_scrolls) {
        const found = resolveAll(locator, true);
        if (found.ok && found.elements.length) return locateOnce(locator, 'click');
        const before = c.scrollTop;
        c.scrollBy({ top: Math.max(100, c.clientHeight * 0.8), behavior: 'instant' });
        scrolls++;
        await changeOrTimeout(500);
        if (c.scrollTop === before) { await changeOrTimeout(400); if (c.scrollTop === before && !resolveAll(locator, true).elements?.length) break; }
      }
    }
    const final = resolveAll(locator, true);
    if (final.ok && final.elements.length) return locateOnce(locator, 'click');
    return { ok: false, code: 'not_found', message: `${describeLocator(locator)} not found after ${scrolls} scrolls` };
  }

  function extractFields({ fields }) {
    const values = {};
    for (const [name, loc] of Object.entries(fields || {})) {
      const m = firstMatch(loc);
      if (!m.el) { values[name] = null; continue; }
      const v = valueOf(m.el);
      values[name] = isSecretField(m.el) ? MASK : (v != null && m.el.localName !== 'select' ? v : trunc(renderedText(m.el), 500));
    }
    return { ok: true, values };
  }

  function observe({ max_elements = 120, text_chars = 3000 } = {}) {
    const active = activeDialog();
    const scopeEl = active || document.documentElement;
    const elements = [];
    walk(scopeEl, (el) => {
      if (elements.length >= max_elements) return false;
      const role = getRole(el);
      const interactive = INTERACTIVE_ROLES.has(role) || el.hasAttribute('onclick') || (el.isContentEditable && el.hasAttribute('contenteditable'));
      if (interactive && isVisible(el)) elements.push(summarize(el));
    });
    const frames = [];
    walk(document, (el) => {
      if (el.localName === 'iframe' && isVisible(el)) {
        let origin = '';
        try { origin = new URL(el.src, location.href).origin; } catch { /* about:blank */ }
        frames.push({ origin, same_origin: !!frameDocument(el) });
      }
    });
    const captcha = frames.some((f) => /recaptcha|hcaptcha|challenges\.cloudflare|turnstile|arkoselabs/.test(f.origin))
      || !!document.querySelector('.g-recaptcha, .h-captcha, [data-sitekey], #challenge-form');
    const password = [];
    walk(document, (el) => { if (isSecretField(el) && isVisible(el)) password.push(el); });
    const headings = [];
    walk(scopeEl, (el) => { if (/^h[1-3]$/.test(el.localName) && isVisible(el) && headings.length < 8) headings.push(trunc(renderedText(el), 80)); });
    return {
      url: location.href, title: document.title, ready_state: document.readyState,
      active_dialog: active ? describeDialog(active) : null,
      dialogs: visibleDialogs().map(describeDialog),
      elements, truncated: elements.length >= max_elements,
      alerts: alerts(), busy: busyIndicators(), headings,
      blockers: { captcha, login_form: password.length > 0 },
      frames: frames.slice(0, 10),
      text: trunc(visibleText(active || document.body), text_chars),
    };
  }

  // ── Overlay ───────────────────────────────────────────────────────────────

  const COLORS = { inspect: '#2563eb', act: '#7c3aed', wait: '#d97706', success: '#16a34a', retry: '#ea580c',
    fail: '#dc2626', human: '#c026d3', idle: '#6b7280' };
  const journal = [];
  let host = null, shadow = null, tracked = [], trackLoop = false, hideTimer = null;

  function ensureHost() {
    if (host && host.isConnected) return;
    host = document.createElement(HOST_TAG);
    const css = {
      position: 'fixed', inset: '0', margin: '0', padding: '0', border: '0', background: 'transparent',
      width: '100vw', height: '100vh', 'max-width': 'none', 'max-height': 'none', overflow: 'visible',
      'pointer-events': 'none', 'z-index': '2147483647', display: 'block', color: 'inherit',
    };
    for (const [k, v] of Object.entries(css)) host.style.setProperty(k, v, 'important');
    host.setAttribute('aria-hidden', 'true');
    if ('popover' in HTMLElement.prototype) host.setAttribute('popover', 'manual');
    shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = `<style>
      :host { all: initial; }
      * { box-sizing: border-box; }
      .box { position: fixed; border: 2px solid var(--c); border-radius: 5px; pointer-events: none;
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--c) 28%, transparent);
        transition: border-color 120ms ease-out, box-shadow 120ms ease-out; } /* never animate position: boxes must sit exactly on the element */
      .box.scope { border-style: dashed; box-shadow: none; background: color-mix(in srgb, var(--c) 5%, transparent); }
      .box.candidate { border-style: dotted; }
      .label { position: fixed; font: 600 12px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif; color: #fff;
        background: var(--c); padding: 3px 8px; border-radius: 4px; max-width: min(420px, 90vw); white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis; box-shadow: 0 1px 4px rgba(0,0,0,.35); pointer-events: none; }
      .hud { position: fixed; bottom: 16px; right: 16px; width: 320px; max-width: calc(100vw - 32px);
        background: rgba(17,24,39,.95); color: #f9fafb; border-radius: 10px; padding: 10px 12px 11px;
        font: 12px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; border-left: 4px solid var(--c);
        box-shadow: 0 8px 28px rgba(0,0,0,.4); pointer-events: none; }
      .hud.left { right: auto; left: 16px; }
      .hud .top { display: flex; gap: 8px; align-items: center; font-weight: 600; font-size: 11px;
        letter-spacing: .02em; color: #d1d5db; }
      .hud .badge { background: var(--c); color: #fff; border-radius: 999px; padding: 1px 8px; font-size: 10px;
        text-transform: uppercase; letter-spacing: .06em; }
      .hud .goal { margin-top: 4px; font-weight: 600; font-size: 13px; color: #fff; overflow: hidden;
        text-overflow: ellipsis; white-space: nowrap; }
      .hud .msg { margin-top: 3px; color: #e5e7eb; overflow-wrap: anywhere; }
      .hud .bar { margin-top: 8px; height: 4px; background: rgba(255,255,255,.15); border-radius: 2px; overflow: hidden; }
      .hud .bar > i { display: block; height: 100%; background: var(--c); width: 0; transition: width 200ms; }
      @media (prefers-reduced-motion: reduce) { .box, .hud .bar > i { transition: none; } }
    </style><div id="boxes"></div><div class="hud" id="hud" hidden>
      <div class="top"><span>Web Goal Agent</span><span class="badge" id="badge"></span><span id="count"></span></div>
      <div class="goal" id="goal"></div><div class="msg" id="msg"></div><div class="bar"><i id="bar"></i></div></div>`;
    document.documentElement.appendChild(host);
  }

  /** Re-enter the top layer so highlights draw above modal <dialog>s and popovers. */
  function raise() {
    if (!host || !host.showPopover) return;
    try { if (host.matches(':popover-open')) host.hidePopover(); host.showPopover(); } catch { /* not in a document */ }
  }

  function drawBox(rect, state, text, cls = '') {
    const c = COLORS[state] || COLORS.inspect;
    const box = document.createElement('div');
    box.className = `box ${cls}`;
    box.style.setProperty('--c', c);
    place(box, rect);
    shadow.getElementById('boxes').appendChild(box);
    let label = null;
    if (text) {
      label = document.createElement('div');
      label.className = 'label';
      label.style.setProperty('--c', c);
      label.textContent = text;
      shadow.getElementById('boxes').appendChild(label);
      placeLabel(label, rect);
    }
    return { box, label };
  }

  function place(box, r) {
    box.style.left = `${r.x - 3}px`; box.style.top = `${r.y - 3}px`;
    box.style.width = `${r.width + 6}px`; box.style.height = `${r.height + 6}px`;
  }

  function placeLabel(label, r) {
    const above = r.y > 26;
    label.style.left = `${Math.max(4, Math.min(r.x - 3, window.innerWidth - 200))}px`;
    label.style.top = above ? `${r.y - 26}px` : `${Math.min(r.y + r.height + 6, window.innerHeight - 24)}px`;
  }

  function track() {
    if (trackLoop) return;
    trackLoop = true;
    const step = () => {
      if (!tracked.length || !host || !host.isConnected) { trackLoop = false; return; }
      for (const t of tracked) {
        if (!t.el || !t.el.isConnected) continue;
        const r = topRect(t.el);
        place(t.box, r);
        if (t.label) placeLabel(t.label, r);
      }
      avoidHud();
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function avoidHud() {
    const hud = shadow && shadow.getElementById('hud');
    if (!hud || hud.hidden) return;
    const t = tracked.find((x) => x.kind === 'target' && x.el && x.el.isConnected);
    if (!t) return;
    const r = topRect(t.el);
    const nearRight = r.x + r.width > window.innerWidth - 360 && r.y + r.height > window.innerHeight - 160;
    hud.classList.toggle('left', nearRight);
  }

  function clearKind(kind) {
    tracked = tracked.filter((t) => {
      if (kind && t.kind !== kind) return true;
      t.box.remove(); if (t.label) t.label.remove();
      return false;
    });
  }

  function hud({ title, phase, state = 'inspect', text, step, total }) {
    const el = shadow.getElementById('hud');
    el.hidden = false;
    el.style.setProperty('--c', COLORS[state] || COLORS.inspect);
    if (title != null) shadow.getElementById('goal').textContent = title;
    shadow.getElementById('badge').textContent = phase || state;
    if (text != null) shadow.getElementById('msg').textContent = text;
    // Page-level phase updates carry no step info: keep the count/progress set by run events.
    if (total) {
      shadow.getElementById('count').textContent = `step ${Math.min(step + 1, total)}/${total}`;
      shadow.getElementById('bar').style.width = `${Math.round((Math.max(0, step) / total) * 100)}%`;
    }
  }

  function overlay(a) {
    try {
      ensureHost();
      raise();
      clearTimeout(hideTimer);
      journal.push({ t: Date.now(), op: a.op, state: a.state, text: a.text });
      if (journal.length > 300) journal.shift();
      switch (a.op) {
        case 'hud': hud(a); break;
        case 'phase': hud({ state: a.state, text: a.text, phase: a.phase }); break;
        case 'scope': {
          clearKind('scope');
          if (a.el) { const d = drawBox(topRect(a.el), 'inspect', a.text, 'scope'); tracked.push({ kind: 'scope', el: a.el, ...d }); }
          break;
        }
        case 'target': {
          clearKind('target'); clearKind('candidate');
          const el = a.el || deref(a.handle);
          if (el) { const d = drawBox(topRect(el), a.state || 'inspect', a.text); tracked.push({ kind: 'target', el, ...d }); }
          break;
        }
        case 'candidates': {
          clearKind('target'); clearKind('candidate');
          for (const el of a.els || []) { const d = drawBox(topRect(el), 'retry', null, 'candidate'); tracked.push({ kind: 'candidate', el, ...d }); }
          break;
        }
        case 'state': {
          // Recolour the current target (act / success / fail) and update its label.
          const t = tracked.find((x) => x.kind === 'target');
          const el = a.handle ? deref(a.handle) : t?.el;
          if (el && (!t || t.el !== el)) { clearKind('target'); const d = drawBox(topRect(el), a.state, a.text); tracked.push({ kind: 'target', el, ...d }); }
          else if (t) {
            const c = COLORS[a.state] || COLORS.inspect;
            t.box.style.setProperty('--c', c);
            if (t.label) { t.label.style.setProperty('--c', c); if (a.text) t.label.textContent = a.text; }
          }
          if (a.text) hud({ state: a.state, text: a.text });
          break;
        }
        case 'human': {
          const el = a.handle ? deref(a.handle) : null;
          if (el) { clearKind('target'); const d = drawBox(topRect(el), 'human', 'Waiting for your approval'); tracked.push({ kind: 'target', el, ...d }); }
          hud({ state: 'human', phase: 'needs you', text: a.text });
          break;
        }
        case 'clear': clearKind(a.kind); break;
        case 'done': {
          clearKind();
          hud({ state: a.state, phase: a.phase || a.state, text: a.text, step: a.total, total: a.total, title: a.title });
          if (a.state === 'success') shadow.getElementById('bar').style.width = '100%';
          hideTimer = setTimeout(() => removeOverlay(), a.linger_ms ?? 6000);
          break;
        }
        case 'remove': removeOverlay(); break;
        default: break;
      }
      if (tracked.length) track();
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  function removeOverlay() {
    clearKind();
    if (host) { try { if (host.hidePopover && host.matches(':popover-open')) host.hidePopover(); } catch { /* ignore */ } host.remove(); }
    host = null; shadow = null;
  }

  // ── Dispatcher ────────────────────────────────────────────────────────────

  const OPS = {
    ping: () => ({ ok: true, version: VERSION, url: location.href, ready_state: document.readyState }),
    observe, locate, prepare, scroll_search: scrollSearch, settle, wait_for: waitFor,
    check: ({ expect, baseline_url }) => ({ ...checkAll(expect, { baseline_url }), url: location.href, alerts: alerts() }),
    extract_fields: extractFields, focus_info: focusInfo, read_value: readValue, value_matches: valueMatches,
    set_value_native: setValueNative, select_all_in: selectAllIn, select_option: selectOption, focus: focusEl,
    overlay: (a) => {
      const args = { ...a };
      if (args.handle && !args.el) args.el = deref(args.handle) || undefined;
      return overlay(args);
    },
    overlay_journal: () => ({ ok: true, journal: journal.slice(-100), present: !!(host && host.isConnected) }),
  };

  globalThis.__wga = {
    version: VERSION,
    async call(op, args) {
      const fn = OPS[op];
      if (!fn) return { ok: false, code: 'unknown_op', message: `unknown page op ${op}` };
      try { return await fn(args || {}); } catch (e) { return { ok: false, code: 'page_error', message: String(e && e.message || e) }; }
    },
  };
})();
