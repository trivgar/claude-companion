// Direct chat with a running session. The page talks only to /api/cave so the
// server has one full contract for session windows, sends and attachments.
'use strict';
(function () {
  const esc = CC.esc;
  let el = null, who = '', agents = [], files = [];
  try { who = localStorage.getItem('cc-chat-to') || ''; } catch (e) {}

  const css = document.createElement('style');
  css.textContent = `
    #chat-log { display:flex; flex-direction:column; gap:8px; margin:0 0 12px; }
    .msg { max-width:92%; padding:8px 12px; border-radius:8px; border:1px solid var(--line);
      background:var(--raised); white-space:pre-wrap; overflow-wrap:anywhere; }
    .msg.me { align-self:flex-end; background:var(--sunk); border-color:var(--line-strong); }
    .msg.work, .msg.asked, .msg.spoke { color:var(--dim); font-size:.85rem; }
    .msg.bad { border-color:var(--alert); } #chat-bar { display:flex; gap:8px; align-items:center;
      margin:0 0 10px; flex-wrap:wrap; } #chat-bar select { min-height:44px; font:inherit; color:var(--fg);
      background:var(--sunk); border:1px solid var(--line); border-radius:8px; padding:0 10px; }
    #chat-send { display:flex; gap:8px; flex-wrap:wrap; } #chat-send textarea { flex:1; min-height:44px;
      resize:vertical; } #chat-files { flex-basis:100%; color:var(--dim); font-size:.85rem; }`;
  document.head.appendChild(css);

  const time = at => new Date(at * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  const fileNames = m => m.files && m.files.length ? `\nDelivered: ${m.files.map(f => esc(f.name || 'file')).join(', ')}` : '';
  function line(m) {
    if (m.role === 'work') return `<div class="msg work">${esc((m.tools || []).join(', '))}${m.last ? ' · ' + esc(m.last) : ''}</div>`;
    if (m.role === 'asked') {
      const pick = m.answer ? `Answered: ${esc(m.answer)}` : (m.closed ? 'Closed without an answer' : 'Waiting on you in Asks');
      return `<div class="msg asked">Asked: ${esc(m.text)}${m.options && m.options.length ? ' (' + esc(m.options.join(' / ')) + ')' : ''}<span class="by">${pick}</span></div>`;
    }
    const by = m.role === 'me' ? (m.to ? 'to ' + esc(m.to) : 'you') : esc(m.from || who);
    const bad = m.role === 'me' && m.ok === false ? ' bad' : '';
    const note = bad ? `<span class="by">Not sent: ${esc(m.detail || 'refused')}</span>` : '';
    return `<div class="msg ${esc(m.role)}${bad}"><span class="by">${by} · ${esc(time(m.at))}</span>${esc(m.text)}${fileNames(m)}${note}</div>`;
  }

  function shell() {
    el.innerHTML = `
      <div id="chat-bar"><label class="dim" for="chat-to">Talk to</label><select id="chat-to"></select>
      <button type="button" class="act" id="chat-stop">Stop it</button>
      <label class="dim"><input type="checkbox" id="chat-win"> its whole window</label></div>
      <div id="chat-controls"></div><div id="chat-asks"></div>
      <div id="chat-log" aria-live="polite"></div>
      <form id="chat-send"><input type="file" id="chat-file" multiple hidden
        accept="image/*,application/pdf,text/plain,text/csv,text/markdown,application/json">
        <textarea id="chat-text" rows="2" maxlength="4000" placeholder="Say something" aria-label="Message"></textarea>
        <button type="submit" class="act primary">Send</button><button type="button" class="act" id="chat-attach">Attach</button>
        <span id="chat-files"></span></form><p class="faint" id="chat-err" role="status"></p>`;
    el.querySelector('#chat-to').addEventListener('change', e => {
      who = e.target.value;
      try { localStorage.setItem('cc-chat-to', who); } catch (x) {}
      refresh();
    });
    el.querySelector('#chat-win').addEventListener('change', refresh);
    el.querySelector('#chat-stop').addEventListener('click', async () => {
      const err = el.querySelector('#chat-err');
      if (!who) { err.textContent = 'Pick a session first.'; return; }
      try { const r = await CC.post(`/api/cave/stop/${encodeURIComponent(who)}`, {}); err.textContent = r.ok ? 'Stopped.' : 'Not stopped: ' + r.detail; }
      catch (e) { err.textContent = 'Not stopped: ' + e.message; }
    });
    el.querySelector('#chat-attach').addEventListener('click', () => el.querySelector('#chat-file').click());
    el.querySelector('#chat-file').addEventListener('change', e => {
      files = [...files, ...e.target.files].slice(0, 6);
      e.target.value = '';
      el.querySelector('#chat-files').textContent = files.map(f => f.name).join(', ');
    });
    el.addEventListener('click', actOnChat);
    el.querySelector('#chat-send').addEventListener('submit', send);
  }

  function askCards(asks) {
    return (asks || []).map(a => {
      if (a.questions && a.questions.length) return `<div class="card" data-cave-ask="${esc(a.id)}"><h3>Question</h3>${a.questions.map(q =>
        `<label>${esc(q.question)}<select data-question="${esc(q.question)}"><option value="">Choose</option>${(q.options || []).map(o =>
          `<option value="${esc(o.label)}">${esc(o.label)}</option>`).join('')}</select></label>`).join('')}<p><button class="act primary" data-answer>Send answers</button></p></div>`;
      return `<div class="card" data-cave-ask="${esc(a.id)}"><h3>Needs your OK</h3><p><b>${esc(a.tool || '')}</b> <code>${esc(a.what || '')}</code></p><p>
        <button class="act primary" data-decide="allow">Allow</button><button class="act" data-decide="deny">Deny</button></p></div>`;
    }).join('');
  }

  function controls(data) {
    const suggested = data.suggestion ? `<button type="button" class="act" data-suggestion="${esc(data.suggestion)}">Use suggestion</button>` : '';
    const models = (data.models || []).map(model => `<button type="button" class="act" data-command="/model ${esc(model)}">${esc(model)}</button>`).join('');
    const efforts = (data.efforts || []).map(level => `<button type="button" class="act" data-command="/effort ${esc(level)}">${esc(level)}</button>`).join('');
    return suggested || models || efforts ? `<p class="faint">${suggested}${models}${efforts}</p>` : '';
  }

  async function actOnChat(event) {
    const suggestion = event.target.closest('[data-suggestion]');
    if (suggestion) { el.querySelector('#chat-text').value = suggestion.dataset.suggestion; el.querySelector('#chat-text').focus(); return; }
    const command = event.target.closest('[data-command]');
    if (command) { el.querySelector('#chat-text').value = command.dataset.command; await send(new Event('submit')); return; }
    const card = event.target.closest('[data-cave-ask]');
    if (!card) return;
    const id = card.dataset.caveAsk;
    const decision = event.target.closest('[data-decide]');
    try {
      if (decision) await CC.post(`/api/cave/ask/${encodeURIComponent(id)}`, {decision: decision.dataset.decide});
      const answer = event.target.closest('[data-answer]');
      if (answer) {
        const answers = {};
        for (const select of card.querySelectorAll('select[data-question]')) {
          if (!select.value) throw new Error('Choose an answer for every question.');
          answers[select.dataset.question] = select.value;
        }
        await CC.post(`/api/cave/ask/${encodeURIComponent(id)}/answer`, {answers});
      }
      await refresh();
    } catch (error) { el.querySelector('#chat-err').textContent = 'Not sent: ' + error.message; }
  }

  const base64 = file => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
    reader.onerror = () => reject(new Error('could not read the file'));
    reader.readAsDataURL(file);
  });
  async function send(event) {
    event.preventDefault();
    const box = el.querySelector('#chat-text'), err = el.querySelector('#chat-err');
    let text = box.value.trim();
    if (!text && !files.length) return;
    err.textContent = '';
    try {
      const paths = [];
      for (const file of files) {
        const upload = await CC.post('/api/cave/upload', {type: file.type, data: await base64(file)});
        paths.push(`[${file.type.startsWith('image/') ? 'photo' : 'file'}: ${upload.path}]`);
      }
      if (paths.length) text = [text || 'Please review these attachments.', ...paths].join(' ');
      const r = await CC.post('/api/cave/send', {to: who, text});
      if (r.ok) { box.value = ''; files = []; el.querySelector('#chat-files').textContent = ''; }
      else err.textContent = 'Not sent: ' + r.detail;
    } catch (e) { err.textContent = 'Not sent: ' + e.message; }
    refresh();
  }

  async function refresh() {
    if (!el) return;
    if (!el.querySelector('#chat-log')) shell();
    const data = await CC.get('/api/cave');
    agents = data.agents || [];
    const names = agents.map(a => a.name);
    if (!names.includes(who)) who = names.length === 1 ? names[0] : '';
    const select = el.querySelector('#chat-to');
    if (document.activeElement !== select) select.innerHTML = `<option value="">${names.length ? 'pick a session' : 'no session reachable'}</option>` +
      agents.map(a => `<option value="${esc(a.name)}"${a.name === who ? ' selected' : ''}>${esc(a.name)}${a.waiting ? ' (waiting)' : ''}${a.project ? ' · ' + esc(a.project) : ''}</option>`).join('');
    const log = el.querySelector('#chat-log');
    let messages = data.messages || [];
    if (who && el.querySelector('#chat-win').checked) {
      try {
        const windowData = await CC.get(`/api/cave/chat/${encodeURIComponent(who)}`);
        messages = windowData.messages || [];
        el.querySelector('#chat-asks').innerHTML = askCards(windowData.asks);
        el.querySelector('#chat-controls').innerHTML = controls(windowData);
        if (windowData.suggestion && !el.querySelector('#chat-text').value) el.querySelector('#chat-text').placeholder = windowData.suggestion;
        await CC.post(`/api/cave/seen/${encodeURIComponent(who)}`, {});
      } catch (e) { messages = []; el.querySelector('#chat-asks').innerHTML = ''; el.querySelector('#chat-controls').innerHTML = ''; }
    }
    const atBottom = innerHeight + scrollY >= document.body.scrollHeight - 80;
    log.innerHTML = messages.length ? messages.map(line).join('') : '<p class="empty">Nothing said yet.</p>';
    if (atBottom) scrollTo(0, document.body.scrollHeight);
  }

  CC.register('chat', {mount: root => { el = root; shell(); }, refresh});
})();
