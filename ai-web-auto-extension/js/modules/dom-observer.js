/**
 * DOM Observer Module — structured DOM diffing and mutation analysis.
 *
 * Ported and universalized from the DOM Activity Recorder extension.
 * Provides:
 *   - Element-by-element snapshot diffing (add/remove/attr/text)
 *   - Human-readable reason inference for every change
 *   - Mutation statistics for action verification
 *   - Targeted or full-page observation
 *
 * Designed to run inside executeScript (MAIN world) for CSP safety.
 * No extension-specific dependencies — pure DOM API.
 */

// ── Selector builders ─────────────────────────────────────────────────────

function getSelector(el) {
  if (!el || el.nodeType !== 1) return '';
  const parts = [];
  let cur = el;
  while (cur && cur !== document.body && cur !== document.documentElement) {
    const tag = cur.tagName.toLowerCase();
    if (cur.id) { parts.unshift(`${tag}#${cur.id}`); break; }
    const cls = Array.from(cur.classList || [])
      .filter(c => !/^(awa-|sar-)/.test(c))
      .slice(0, 2).join('.');
    let sel = cls ? `${tag}.${cls}` : tag;
    const parent = cur.parentElement;
    if (parent) {
      const siblings = parent.querySelectorAll(`:scope > ${tag}`);
      if (siblings.length > 1) {
        const idx = Array.from(siblings).indexOf(cur);
        sel += `:nth-child(${idx + 1})`;
      }
    }
    parts.unshift(sel);
    cur = cur.parentElement;
  }
  return parts.join(' > ');
}

function getXPath(el, root) {
  if (!el || el.nodeType !== 1) return '';
  const parts = [];
  let cur = el;
  while (cur && cur !== root && cur !== document.documentElement) {
    const tag = cur.tagName.toLowerCase();
    const parent = cur.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
      parts.unshift(`${tag}[${siblings.indexOf(cur) + 1}]`);
    } else {
      parts.unshift(tag);
    }
    cur = cur.parentElement;
  }
  return '/' + parts.join('/');
}

function truncate(str, max = 200) {
  if (!str) return '';
  str = str.replace(/\s+/g, ' ').trim();
  return str.length > max ? str.substring(0, max) + '...' : str;
}

// ── Reason inference engine ───────────────────────────────────────────────

function inferReason(change) {
  if (change.type === 'add') {
    const tag = change.tag;
    if (tag === 'script') return 'Script injected — dynamic code loading';
    if (tag === 'style' || tag === 'link') return 'Stylesheet added — visual update';
    if (/^(img|svg|canvas|video|picture)$/.test(tag)) return 'Media element inserted — new visual content';
    if (tag === 'iframe') return 'Iframe embedded — external content loaded';
    if (/^(input|textarea|select)$/.test(tag)) return 'Form element added — new interaction point';
    if (/^(li|tr|td|option)$/.test(tag)) return 'List/table item added — data row appended';
    if (/^(a|button)$/.test(tag)) return 'Interactive element added — new action available';
    if (/^(dialog|[a-z]+-modal|[a-z]+-overlay)$/i.test(tag)) return 'Modal/dialog opened — overlay content appeared';
    return 'Element inserted — dynamic content update';
  }

  if (change.type === 'rem') {
    const tag = change.tag;
    if (tag === 'script') return 'Script removed — cleanup or replacement';
    if (/^(li|tr|td|option)$/.test(tag)) return 'List/table item removed — data row deleted';
    if (/^(dialog|[a-z]+-modal|[a-z]+-overlay)$/i.test(tag)) return 'Modal/dialog closed — overlay dismissed';
    return 'Element removed — content cleanup or replacement';
  }

  if (change.type === 'attr') {
    const attr = change.attribute;

    if (attr === 'class') {
      const oldCls = (change.oldValue || '').split(/\s+/);
      const newCls = (change.newValue || '').split(/\s+/);
      const added = newCls.filter(c => c && !oldCls.includes(c));
      const removed = oldCls.filter(c => c && !newCls.includes(c));

      const patterns = [
        { re: /hidden|hide|collapse|invisible|d-none|display-none|sr-only/, addMsg: 'Element hidden via class', remMsg: 'Element shown via class' },
        { re: /active|selected|current|focus|open|expanded|checked/, addMsg: 'State class added — element activated/selected', remMsg: 'State class removed — element deactivated' },
        { re: /loading|spinner|pending|processing|skeleton|shimmer/, addMsg: 'Loading state applied — async operation in progress', remMsg: 'Loading state cleared — operation completed' },
        { re: /error|invalid|danger|warning|alert/, addMsg: 'Error/warning state applied', remMsg: 'Error/warning state cleared' },
        { re: /success|valid|complete|done|finished/, addMsg: 'Success state applied', remMsg: 'Success state cleared' },
        { re: /disabled|readonly/, addMsg: 'Element disabled — interaction blocked', remMsg: 'Element enabled — interaction allowed' },
      ];

      for (const p of patterns) {
        if (added.some(c => p.re.test(c))) return p.addMsg;
        if (removed.some(c => p.re.test(c))) return p.remMsg;
      }

      if (added.length || removed.length) {
        return `CSS classes changed (${added.length ? '+' + added.slice(0, 3).join(', +') : ''}${removed.length ? ' -' + removed.slice(0, 3).join(', -') : ''})`;
      }
      return 'CSS classes modified — styling change';
    }

    if (attr === 'style') return 'Inline style changed — direct visual modification';
    if (attr === 'disabled') return change.newValue === '(removed)' ? 'Element enabled' : 'Element disabled';
    if (attr === 'href' || attr === 'src') return 'Resource URL changed — content source updated';
    if (attr === 'value') return 'Input value changed — form data updated';
    if (/^aria-/.test(attr)) return `ARIA attribute "${attr}" changed — accessibility state updated`;
    if (/^data-/.test(attr)) return `Data attribute "${attr}" changed — application state updated`;
    if (attr === 'title' || attr === 'alt' || attr === 'placeholder') return 'Descriptive attribute changed — label/hint updated';
    return `Attribute "${attr}" modified`;
  }

  if (change.type === 'text') {
    const oldLen = (change.oldValue || '').length;
    const newLen = (change.newValue || '').length;
    if (oldLen === 0 && newLen > 0) return 'Text content populated — empty element now has content';
    if (oldLen > 0 && newLen === 0) return 'Text content cleared — element emptied';
    if (Math.abs(newLen - oldLen) < 5) return 'Text content tweaked — minor edit';
    return 'Text content replaced — dynamic text update';
  }

  return 'DOM mutation detected';
}

// ── Snapshot diff engine ──────────────────────────────────────────────────

function buildElementMap(container) {
  const map = {};
  const els = container.querySelectorAll('*');
  for (const el of els) {
    const sel = getSelector(el);
    if (sel) map[sel] = el;
  }
  return map;
}

function diffSnapshots(prevHTML, currHTML) {
  const prev = document.createElement('div');
  prev.innerHTML = prevHTML;
  const curr = document.createElement('div');
  curr.innerHTML = currHTML;

  const prevMap = buildElementMap(prev);
  const currMap = buildElementMap(curr);
  const changes = [];

  // Added elements
  for (const sel of Object.keys(currMap)) {
    if (!prevMap[sel]) {
      const el = currMap[sel];
      const entry = {
        type: 'add',
        tag: el.tagName.toLowerCase(),
        id: el.id || '',
        classes: Array.from(el.classList || []).join(' '),
        selector: sel,
        xpath: getXPath(el, curr),
        html: truncate(el.outerHTML, 150),
      };
      entry.reason = inferReason(entry);
      changes.push(entry);
    }
  }

  // Removed elements
  for (const sel of Object.keys(prevMap)) {
    if (!currMap[sel]) {
      const el = prevMap[sel];
      const entry = {
        type: 'rem',
        tag: el.tagName.toLowerCase(),
        id: el.id || '',
        classes: Array.from(el.classList || []).join(' '),
        selector: sel,
        xpath: getXPath(el, prev),
        html: truncate(el.outerHTML, 150),
      };
      entry.reason = inferReason(entry);
      changes.push(entry);
    }
  }

  // Attribute + text changes on shared elements
  for (const sel of Object.keys(currMap)) {
    if (!prevMap[sel]) continue;
    const pEl = prevMap[sel];
    const cEl = currMap[sel];

    // Attributes
    const allAttrs = new Set([
      ...Array.from(pEl.attributes).map(a => a.name),
      ...Array.from(cEl.attributes).map(a => a.name),
    ]);

    for (const attr of allAttrs) {
      let oldVal = pEl.getAttribute(attr) || '';
      let newVal = cEl.getAttribute(attr) || '';
      if (attr === 'class') {
        oldVal = oldVal.split(/\s+/).filter(c => !/^(awa-|sar-)/.test(c)).join(' ').trim();
        newVal = newVal.split(/\s+/).filter(c => !/^(awa-|sar-)/.test(c)).join(' ').trim();
      }
      if (oldVal !== newVal) {
        const entry = {
          type: 'attr',
          tag: cEl.tagName.toLowerCase(),
          selector: sel,
          xpath: getXPath(cEl, curr),
          attribute: attr,
          oldValue: truncate(oldVal || '(none)', 80),
          newValue: truncate(newVal || '(removed)', 80),
        };
        entry.reason = inferReason(entry);
        changes.push(entry);
      }
    }

    // Direct text nodes
    const pText = Array.from(pEl.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
    const cText = Array.from(cEl.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
    if (pText.trim() !== cText.trim() && (pText.trim() || cText.trim())) {
      const entry = {
        type: 'text',
        tag: cEl.tagName.toLowerCase(),
        selector: sel,
        xpath: getXPath(cEl, curr),
        oldValue: truncate(pText.trim(), 80),
        newValue: truncate(cText.trim(), 80),
      };
      entry.reason = inferReason(entry);
      changes.push(entry);
    }
  }

  return changes;
}

// ── Stats helper ──────────────────────────────────────────────────────────

function computeStats(changes) {
  const stats = { total: changes.length, add: 0, rem: 0, attr: 0, text: 0 };
  for (const c of changes) {
    if (stats[c.type] !== undefined) stats[c.type]++;
  }
  return stats;
}

// ── Summarize for AI ──────────────────────────────────────────────────────

function summarizeForAI(changes, maxEntries = 30) {
  if (changes.length === 0) return 'No DOM changes detected.';

  const stats = computeStats(changes);
  const lines = [`DOM changes: ${stats.total} total (${stats.add} added, ${stats.rem} removed, ${stats.attr} attr, ${stats.text} text)`];

  // Group by reason for concise summary
  const reasonCounts = {};
  for (const c of changes) {
    const key = c.reason || 'Unknown';
    reasonCounts[key] = (reasonCounts[key] || 0) + 1;
  }

  lines.push('Change summary:');
  const sorted = Object.entries(reasonCounts).sort((a, b) => b[1] - a[1]);
  for (const [reason, count] of sorted.slice(0, 10)) {
    lines.push(`  - ${reason} (×${count})`);
  }

  // Top specific changes
  if (changes.length <= maxEntries) {
    lines.push('Details:');
    for (const c of changes) {
      if (c.type === 'add') lines.push(`  + <${c.tag}> ${c.selector} — ${c.reason}`);
      else if (c.type === 'rem') lines.push(`  - <${c.tag}> ${c.selector} — ${c.reason}`);
      else if (c.type === 'attr') lines.push(`  ~ [${c.attribute}] ${c.selector}: "${c.oldValue}" → "${c.newValue}" — ${c.reason}`);
      else if (c.type === 'text') lines.push(`  ~ text ${c.selector}: "${c.oldValue}" → "${c.newValue}" — ${c.reason}`);
    }
  } else {
    lines.push(`Showing top ${maxEntries} of ${changes.length}:`);
    for (const c of changes.slice(0, maxEntries)) {
      if (c.type === 'add') lines.push(`  + <${c.tag}> ${c.selector}`);
      else if (c.type === 'rem') lines.push(`  - <${c.tag}> ${c.selector}`);
      else if (c.type === 'attr') lines.push(`  ~ [${c.attribute}] ${c.selector}`);
      else if (c.type === 'text') lines.push(`  ~ text ${c.selector}`);
    }
  }

  return lines.join('\n');
}

// ── Exports ───────────────────────────────────────────────────────────────

export {
  getSelector,
  getXPath,
  inferReason,
  diffSnapshots,
  computeStats,
  summarizeForAI,
  truncate,
};
