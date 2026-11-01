// Files: browse the configured project trees, open a bounded text file, and
// save an edit with the version that was opened.  The server remains the
// authority for every path and for deciding whether a commit is allowed.
'use strict';
(function () {
  const CC = window.CC, esc = CC.esc;
  const css = document.createElement('style');
  css.textContent = `
    .files .top { display:flex; gap:8px; align-items:end; flex-wrap:wrap; margin-bottom:10px; }
    .files label { display:grid; gap:3px; color:var(--dim); font-size:.82rem; }
    .files select { min-height:40px; max-width:100%; color:var(--fg); background:var(--raised); border:1px solid var(--line); border-radius:8px; font:inherit; padding:0 8px; }
    .files .crumbs { display:flex; flex-wrap:wrap; gap:4px; align-items:center; overflow-wrap:anywhere; margin:0 0 10px; }
    .files .crumbs button, .files .tree button { color:var(--fg); background:none; border:0; padding:5px 2px; font:inherit; text-align:left; }
    .files .crumbs button { text-decoration:underline; } .files .slash { color:var(--faint); }
    .files .tree { list-style:none; padding:0; margin:0; } .files .tree li + li { border-top:1px solid var(--line); }
    .files .tree button { display:block; width:100%; min-height:40px; overflow-wrap:anywhere; }
    .files .dir::before { content:'› '; color:var(--dim); } .files .up { color:var(--dim) !important; }
    .files textarea { min-height:50vh; resize:vertical; font:.85rem/1.4 ui-monospace, Menlo, monospace; white-space:pre; overflow:auto; }
    .files .bar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:10px 0; }
    .files .note { color:var(--dim); font-size:.86rem; margin:0; overflow-wrap:anywhere; }
    .files .status { min-height:1.4em; color:var(--dim); margin:8px 0 0; overflow-wrap:anywhere; }
    .files .status.bad { color:var(--alert); }
  `;
  document.head.appendChild(css);

  let box, projects = [], selected = '', folder = '', opened = null, draft = '', message = '', busy = false;

  const join = (a, b) => a ? `${a}/${b}` : b;
  const parent = p => p ? p.split('/').slice(0, -1).join('/') : '';

  function header() {
    const crumbs = folder ? folder.split('/') : [];
    return `<div class="top"><label>Project<select data-project>${projects.map(p =>
      `<option value="${esc(p.name)}"${p.name === selected ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label></div>
      <nav class="crumbs" aria-label="Path"><button type="button" data-folder="">${esc(selected)}</button>${crumbs.map((part, i) =>
        `<span class="slash">/</span><button type="button" data-folder="${esc(crumbs.slice(0, i + 1).join('/'))}">${esc(part)}</button>`).join('')}</nav>`;
  }

  function status(text, bad = false) {
    message = text;
    const target = box && box.querySelector('.status');
    if (target) { target.textContent = text; target.classList.toggle('bad', bad); }
  }

  async function drawTree() {
    if (!selected) {
      box.innerHTML = '<p class="empty">No projects are configured. Add a [projects] table to config.toml.</p>';
      return;
    }
    box.innerHTML = header() + '<p class="empty">Loading files...</p>';
    let data;
    try { data = await CC.get(`/api/files/tree?project=${encodeURIComponent(selected)}&path=${encodeURIComponent(folder)}`); }
    catch (error) { box.innerHTML = header() + `<p class="empty">${esc(error.message)}</p>`; return; }
    const up = folder ? `<li><button type="button" class="up" data-folder="${esc(parent(folder))}">..</button></li>` : '';
    const dirs = data.dirs.map(name => `<li><button type="button" class="dir" data-folder="${esc(join(folder, name))}">${esc(name)}</button></li>`).join('');
    const files = data.files.map(name => `<li><button type="button" data-file="${esc(join(folder, name))}">${esc(name)}</button></li>`).join('');
    box.innerHTML = header() + `<div class="card"><ul class="tree">${up}${dirs}${files || (dirs ? '' : '<li class="empty">No readable text files here.</li>')}</ul></div>`;
  }

  function drawFile() {
    if (!opened) return drawTree();
    const name = opened.path.split('/').pop();
    box.innerHTML = header() + `<div class="card"><p class="note">Editing <b>${esc(name)}</b>. A save commits only if this file was clean when it was opened. Nothing is pushed.</p>
      <textarea aria-label="File contents" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off">${esc(draft)}</textarea>
      <div class="bar"><button type="button" class="act" data-back>Back</button><button type="button" class="act primary" data-save${busy ? ' disabled' : ''}>Save</button></div>
      <p class="status${message ? '' : ' faint'}" role="status">${esc(message)}</p></div>`;
  }

  async function open(path) {
    message = '';
    try {
      opened = await CC.get(`/api/files/file?project=${encodeURIComponent(selected)}&path=${encodeURIComponent(path)}`);
      folder = parent(path);
      draft = opened.text;
      drawFile();
      box.querySelector('textarea').focus();
    } catch (error) { status(error.message, true); }
  }

  async function save() {
    if (!opened || busy) return;
    const area = box.querySelector('textarea');
    draft = area ? area.value : draft;
    busy = true;
    status('Saving...');
    try {
      const result = await CC.post('/api/files/save', {project: selected, path: opened.path, text: draft,
        mtime_ns: opened.mtime_ns, sha256: opened.sha256});
      if (result.unchanged) status('No changes to save.');
      else if (result.committed) status('Saved and committed. Nothing was pushed.');
      else status(result.detail || 'Saved, not committed.');
      if (!result.unchanged) {
        // Refresh the version guard after either a commit or a deliberate
        // no-commit save, so the next save is still protected.
        opened = await CC.get(`/api/files/file?project=${encodeURIComponent(selected)}&path=${encodeURIComponent(opened.path)}`);
        draft = opened.text;
      }
    } catch (error) {
      status(error.message || 'Not saved.', true);
    }
    busy = false;
    drawFile();
  }

  function wire() {
    box.addEventListener('change', event => {
      const select = event.target.closest('[data-project]');
      if (!select) return;
      selected = select.value;
      folder = '';
      opened = null;
      draft = '';
      message = '';
      drawTree();
    });
    box.addEventListener('click', event => {
      const folderButton = event.target.closest('[data-folder]');
      if (folderButton) {
        folder = folderButton.dataset.folder || '';
        opened = null;
        draft = '';
        message = '';
        drawTree();
        return;
      }
      const fileButton = event.target.closest('[data-file]');
      if (fileButton) { open(fileButton.dataset.file); return; }
      if (event.target.closest('[data-back]')) {
        opened = null;
        draft = '';
        message = '';
        drawTree();
        return;
      }
      if (event.target.closest('[data-save]')) save();
    });
  }

  CC.register('files', {
    mount(el) { box = document.createElement('div'); box.className = 'files'; el.appendChild(box); wire(); },
    async refresh() {
      if (busy || opened) return;
      const data = await CC.get('/api/files');
      projects = Array.isArray(data.projects) ? data.projects : [];
      if (!projects.some(item => item.name === selected)) selected = projects.length ? projects[0].name : '';
      await drawTree();
    },
  });
})();
