ENUMERATE_JS = r"""
(args) => {
  const MAX = (args && args.max) || 220;
  const MAX_TEXT = (args && args.max_text) || 6000;
  const vw = window.innerWidth, vh = window.innerHeight;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();

  const isVisible = el => {
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const xpathOf = el => {
    const parts = [];
    while (el && el.nodeType === 1 && el.tagName.toLowerCase() !== 'html') {
      let i = 1, sib = el.previousElementSibling;
      while (sib) { if (sib.tagName === el.tagName) i++; sib = sib.previousElementSibling; }
      parts.unshift(el.tagName.toLowerCase() + '[' + i + ']');
      el = el.parentElement;
    }
    return '/html/' + parts.join('/');
  };

  const overlayEls = [];
  for (const el of document.body.querySelectorAll('*')) {
    const st = getComputedStyle(el);
    if ((st.position === 'fixed' || st.position === 'absolute') && isVisible(el)) {
      const r = el.getBoundingClientRect();
      if (r.width * r.height >= 0.4 * vw * vh) overlayEls.push(el);
    }
  }
  const overlays = overlayEls.map(el => ({ text: norm(el.innerText).slice(0, 300), xpath: xpathOf(el) }));
  const inOverlay = el => overlayEls.some(o => o.contains(el));

  const implicitRole = el => {
    const explicit = el.getAttribute('role'); if (explicit) return explicit;
    const t = el.tagName.toLowerCase();
    const ty = (el.getAttribute('type') || 'text').toLowerCase();
    if (t === 'a') return el.hasAttribute('href') ? 'link' : '';
    if (t === 'button') return 'button';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      if (['submit', 'button', 'reset', 'image'].includes(ty)) return 'button';
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      if (ty === 'password') return '';
      return 'textbox';
    }
    if (t === 'td' || t === 'th') return 'cell';
    if (/^h[1-6]$/.test(t)) return 'heading';
    if (t === 'li') return 'listitem';
    return '';
  };

  const labelFor = el => {
    if (el.id) {
      try { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return norm(l.innerText); } catch (e) {}
    }
    const wrap = el.closest('label'); if (wrap) return norm(wrap.innerText);
    return '';
  };

  const accName = el => {
    const t = el.tagName.toLowerCase();
    const ty = (el.getAttribute('type') || 'text').toLowerCase();
    const aria = el.getAttribute('aria-label'); if (aria) return norm(aria);
    const lbl = labelFor(el); if (lbl) return lbl;
    if (t === 'input' && ['submit', 'button', 'reset'].includes(ty)) return norm(el.value || (ty === 'submit' ? 'Submit' : 'Reset'));
    if (t === 'input' && ty === 'image') return norm(el.alt);
    if (t === 'img') return norm(el.alt);
    const title = el.getAttribute('title'); if (title) return norm(title);
    if (t === 'input' || t === 'textarea') return norm(el.getAttribute('placeholder') || '');
    if (t === 'select') return '';
    return norm(el.innerText).slice(0, 80);
  };

  const INTERACTIVE = 'a[href],button,input:not([type=hidden]),select,textarea,[role=button],[role=link],[role=textbox],[role=combobox],[role=checkbox],[role=menuitem],[role=tab],[onclick],summary,[tabindex]:not([tabindex="-1"])';
  const READABLE = 'td,th,li,h1,h2,h3,h4,h5,h6,p,legend,dt,dd';

  const nearText = el => {
    const cell = el.closest('td,th'); if (!cell) return '';
    let prev = cell.previousElementSibling;
    while (prev) {
      const tx = norm(prev.innerText);
      if (tx && !prev.querySelector(INTERACTIVE)) return tx.replace(/[:：]\s*$/, '');
      prev = prev.previousElementSibling;
    }
    return '';
  };

  const allTables = Array.from(document.querySelectorAll('table'));
  const tableCell = el => {
    if (!/^(td|th)$/i.test(el.tagName)) return null;
    const row = el.parentElement; const table = el.closest('table');
    if (!row || !table) return null;
    const rows = Array.from(table.rows);
    const rowIdx = rows.indexOf(row); if (rowIdx < 0) return null;
    const colIdx = Array.from(row.cells).indexOf(el);
    const isHeaderCell = c => {
      if (c.tagName.toLowerCase() === 'th') return true;
      const probe = c.querySelector('b,strong') || c;
      const fw = getComputedStyle(probe).fontWeight;
      return (fw === 'bold' || parseInt(fw, 10) >= 600) && !/\d{3,}|[$]/.test(norm(c.innerText));
    };
    let header = null;
    for (let i = 0; i < rowIdx; i++) {
      const r = rows[i];
      if (r.cells.length === row.cells.length && Array.from(r.cells).every(isHeaderCell)) {
        const h = norm(r.cells[colIdx].innerText);
        if (h) header = h;
        break;
      }
    }
    const anchor = colIdx > 0 ? norm(row.cells[0].innerText).replace(/[:：]\s*$/, '') : null;
    if (!anchor && !header) return null;
    return { header, row_anchor: anchor, col_index: colIdx, row_cells: row.cells.length, table_index: allTables.indexOf(table) };
  };

  const seen = new Set(); const items = [];
  const push = (el, kind) => {
    if (seen.has(el) || !isVisible(el)) return;
    if (kind === 'readable') {
      if (el.querySelector('table')) return;
      if (el.querySelector(INTERACTIVE)) return;
      const tx = norm(el.innerText);
      if (!tx || tx.length > 200) return;
    }
    seen.add(el);
    const r = el.getBoundingClientRect();
    const t = el.tagName.toLowerCase();
    const ty = (el.getAttribute('type') || (t === 'input' ? 'text' : '')).toLowerCase();
    items.push({ el, data: {
      kind, tag: t, role: implicitRole(el), name: accName(el),
      text: kind === 'interactive' ? norm(el.innerText || (t === 'input' ? el.value : '') || '').slice(0, 120) : norm(el.innerText).slice(0, 200),
      attrs: {
        id: el.id || '', name: el.getAttribute('name') || '', type: ty,
        href: el.getAttribute('href') || '', placeholder: el.getAttribute('placeholder') || '',
        value: ((t === 'input' && ty !== 'password') || t === 'textarea' || t === 'select') ? String(el.value || '').slice(0, 80) : '',
        title: el.getAttribute('title') || '', cls: (el.getAttribute('class') || '').slice(0, 60)
      },
      options: t === 'select' ? Array.from(el.options).map(o => norm(o.text)).slice(0, 40) : null,
      bbox: { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height, in_viewport: r.bottom > 0 && r.top < vh },
      label: labelFor(el),
      near_text: (t === 'input' || t === 'select' || t === 'textarea') ? nearText(el) : '',
      table_cell: tableCell(el),
      xpath: xpathOf(el),
      in_overlay: inOverlay(el),
      disabled: !!el.disabled
    }});
  };
  document.querySelectorAll(INTERACTIVE).forEach(el => push(el, 'interactive'));
  document.querySelectorAll(READABLE).forEach(el => push(el, 'readable'));
  items.sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);

  return {
    url: location.href, title: document.title,
    text: norm(document.body.innerText).slice(0, MAX_TEXT),
    overlays, viewport: { w: vw, h: vh },
    elements: items.slice(0, MAX).map(i => i.data)
  };
}
"""

TABLE_CELL_JS = r"""
(spec) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().replace(/[:：]\s*$/, '').toLowerCase();
  const header = spec.header ? norm(spec.header) : null;
  const anchor = spec.row_anchor ? norm(spec.row_anchor) : null;
  const matches = [];
  for (const table of document.querySelectorAll('table')) {
    const rows = Array.from(table.rows);
    let colIdx = -1;
    if (header) {
      for (const r of rows) {
        const idx = Array.from(r.cells).findIndex(c => norm(c.innerText) === header && !c.querySelector('table'));
        if (idx >= 0) { colIdx = idx; break; }
      }
      if (colIdx < 0) continue;
    } else {
      colIdx = spec.col_index;
    }
    for (const r of rows) {
      if (!r.cells.length || (anchor && norm(r.cells[0].innerText) !== anchor)) continue;
      if (!anchor && header && norm(r.cells[colIdx] ? r.cells[colIdx].innerText : '') === header) continue;
      const cell = r.cells[colIdx];
      if (cell && !cell.querySelector('table')) matches.push(cell);
    }
  }
  if (matches.length !== 1) return null;
  let el = matches[0]; const parts = [];
  while (el && el.nodeType === 1 && el.tagName.toLowerCase() !== 'html') {
    let i = 1, sib = el.previousElementSibling;
    while (sib) { if (sib.tagName === el.tagName) i++; sib = sib.previousElementSibling; }
    parts.unshift(el.tagName.toLowerCase() + '[' + i + ']');
    el = el.parentElement;
  }
  return '/html/' + parts.join('/');
}
"""

HUMAN_RECORDER_JS = r"""
() => {
  if (window.__cuaHumanInstalled) return;
  window.__cuaHumanInstalled = true;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const describe = el => {
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    const itype = el.getAttribute ? (el.getAttribute('type') || '').toLowerCase() : '';
    const isField = tag === 'input' || tag === 'textarea' || tag === 'select';
    const label = isField ? (['submit', 'button', 'reset'].includes(itype) ? el.value : '') : el.innerText;
    return { tag, input_type: itype, name: el.getAttribute ? (el.getAttribute('name') || '') : '', text: norm(label || '').slice(0, 60) };
  };
  const send = (evt) => { try { window.__cuaHuman(evt); } catch (e) {} };
  document.addEventListener('click', e => { const el = e.target.closest('a,button,input,select,label,td') || e.target; send({ type: 'click', ...describe(el) }); }, true);
  document.addEventListener('change', e => {
    const el = e.target; const d = describe(el);
    d.value = (d.input_type === 'password') ? '[REDACTED]' : String(el.value || '').slice(0, 60);
    send({ type: 'change', ...d });
  }, true);
  document.addEventListener('submit', e => { send({ type: 'submit', ...describe(e.target) }); }, true);
}
"""
