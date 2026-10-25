// Acme CRM fixture behaviour. Plain DOM, React-like re-rendering (rows are replaced, not patched).
(() => {
  const qs = new URLSearchParams(location.search);
  const $ = (id) => document.getElementById(id);
  const fx = (window.__fixture = { saves: 0, saveAttempts: 0, noteSaves: 0, deletes: 0, exports: 0, renders: 0, consent: null });
  const customers = [
    { id: 1, name: 'Ada Lovelace', email: 'ada@example.com', status: 'Active' },
    { id: 2, name: 'Grace Hopper', email: 'grace@example.com', status: 'Active' },
    { id: 3, name: 'Alan Turing', email: 'alan@example.com', status: 'Paused' },
    { id: 4, name: 'Katherine Johnson', email: 'katherine@example.com', status: 'Active' },
  ];
  let editing = null;
  let deleting = null;

  function toast(text) {
    $('toast').textContent = text;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { $('toast').textContent = ''; }, 4000);
  }

  function render() {
    fx.renders++;
    const tbody = document.querySelector('#customers tbody');
    tbody.replaceChildren(...customers.map((c) => {
      const tr = document.createElement('tr');
      tr.dataset.id = c.id;
      for (const v of [c.name, c.email, c.status]) {
        const td = document.createElement('td');
        td.textContent = v;
        tr.append(td);
      }
      const td = document.createElement('td');
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.textContent = 'Edit';
      edit.addEventListener('click', () => openEdit(c.id));
      const del = document.createElement('button');
      del.type = 'button';
      del.textContent = 'Delete';
      del.setAttribute('aria-haspopup', 'dialog');
      del.addEventListener('click', () => openDelete(c.id));
      td.append(edit, ' ', del);
      tr.append(td);
      return tr;
    }));
  }

  function show(id, on) { $(id).hidden = !on; }

  function openEdit(id) {
    editing = customers.find((c) => c.id === id);
    $('fName').value = editing.name;
    $('fEmail').value = editing.email;
    $('fStatus').value = editing.status;
    $('emailError').textContent = '';
    $('fEmail').removeAttribute('aria-invalid');
    show('editBackdrop', true); show('editDialog', true);
    $('fName').focus();
  }

  function closeEdit() { show('editBackdrop', false); show('editDialog', false); editing = null; }

  $('editForm').addEventListener('submit', (e) => {
    e.preventDefault();
    fx.saveAttempts++;
    const email = $('fEmail').value.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      $('fEmail').setAttribute('aria-invalid', 'true');
      $('emailError').textContent = 'Enter a valid email address';
      return;
    }
    const btn = $('editSave');
    btn.disabled = true; btn.textContent = 'Saving…'; btn.setAttribute('aria-busy', 'true');
    setTimeout(() => {
      Object.assign(editing, { name: $('fName').value.trim(), email, status: $('fStatus').value });
      fx.saves++;
      btn.disabled = false; btn.textContent = 'Save'; btn.removeAttribute('aria-busy');
      closeEdit();
      render();
      toast('Customer saved');
    }, 800);
  });
  $('editCancel').addEventListener('click', closeEdit);

  function openDelete(id) {
    deleting = customers.find((c) => c.id === id);
    $('delTitle').textContent = `Delete ${deleting.name}?`;
    show('delBackdrop', true); show('deleteDialog', true);
  }
  $('delCancel').addEventListener('click', () => { show('delBackdrop', false); show('deleteDialog', false); });
  $('delConfirm').addEventListener('click', () => {
    customers.splice(customers.indexOf(deleting), 1);
    fx.deletes++;
    show('delBackdrop', false); show('deleteDialog', false);
    render();
    toast('Customer deleted');
  });

  // Quick note: non-modal panel with a second "Save".
  $('noteBtn').addEventListener('click', () => show('notePanel', true));
  $('noteClose').addEventListener('click', () => show('notePanel', false));
  $('noteSave').addEventListener('click', () => { fx.noteSaves++; toast('Note saved'); });

  // Actions menu (ARIA menu button).
  const menu = $('actionsMenu');
  $('actionsBtn').addEventListener('click', () => {
    const open = menu.hidden;
    menu.hidden = !open;
    $('actionsBtn').setAttribute('aria-expanded', String(open));
  });
  menu.addEventListener('click', (e) => {
    const item = e.target.closest('[role="menuitem"]');
    if (!item) return;
    menu.hidden = true;
    $('actionsBtn').setAttribute('aria-expanded', 'false');
    if (item.dataset.action === 'export') { $('exportResult').textContent = ''; $('exportDialog').showModal(); }
    if (item.dataset.action === 'new') toast('New customer form is not part of this fixture');
  });
  $('exportGo').addEventListener('click', () => {
    fx.exports++;
    const fmt = $('exportFormat').selectedOptions[0].text;
    $('exportResult').textContent = `Exported ${customers.length} customers as ${fmt}`;
  });
  $('exportClose').addEventListener('click', () => $('exportDialog').close());

  // Cookie wall (modal) appears shortly after load unless ?consent=0.
  if (qs.get('consent') !== '0') {
    setTimeout(() => show('consent', true), 400);
    $('consentAccept').addEventListener('click', () => { fx.consent = 'all'; show('consent', false); });
    $('consentReject').addEventListener('click', () => { fx.consent = 'necessary'; show('consent', false); });
  }

  // Tour backdrop that covers everything (not a dialog) for 2.5 s.
  if (qs.get('tour') === '1') {
    show('tour', true);
    setTimeout(() => show('tour', false), 2500);
  }

  // Async data load.
  setTimeout(() => {
    show('loading', false);
    $('loading').removeAttribute('aria-busy');
    show('customers', true);
    render();
    if (qs.get('live') === '1') setInterval(render, 700);
  }, Number(qs.get('delay') || 900));
})();
