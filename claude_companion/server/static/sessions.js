// Sessions: one card per live session, and the spend strip in the header.
'use strict';
(function () {
  const esc = CC.esc;
  const css = document.createElement('style');
  css.textContent = `
    .scard { border-left:4px solid hsl(var(--h,0) var(--sc-pct,40%) 50%); overflow-wrap:anywhere; }
    .scard.waiting { border-color:var(--alert); }
    .scard .top { display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; }
    .scard .top h3 { margin:0; }
    .scard .meta { display:flex; flex-wrap:wrap; gap:4px 12px; color:var(--dim); font-size:.8rem; margin:2px 0 6px; }
    .scard .reply { margin:6px 0 0; max-height:4.4em; overflow:hidden; color:var(--fg); }
    .scard .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
    .scard .action-result { margin:8px 0 0; color:var(--dim); }
    .wbadge { color:var(--alert); border:1px solid var(--alert); border-radius:6px; padding:0 6px; font-size:.75rem; font-weight:700; }
    .clearbadge { color:var(--busy); border:1px solid var(--busy); border-radius:6px; padding:0 6px; font-size:.75rem; font-weight:700; }
    .cold { color:var(--alert); }
    .dot { width:8px; height:8px; border-radius:50%; background:var(--faint); display:inline-block; }
    .dot.busy { background:var(--busy); }
    .bar { height:4px; background:var(--sunk); border-radius:2px; margin-top:6px; }
    .bar i { display:block; height:100%; background:var(--dim); border-radius:2px; }
    .bar.hot i { background:var(--alert); }`;
  document.head.appendChild(css);

  const hue = s => { let h = 0; for (const ch of String(s || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h % 360; };
  const ago = s => s == null ? '' : s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
  const money = n => '$' + Number(n || 0).toFixed(2);
  const short = m => String(m || '').replace(/^claude-/, '').replace(/-(\d)-(\d)$/, ' $1.$2');
  const ARM_MS = 4000;
  const RESULT_MS = 6000;
  const arms = new Map();
  const results = new Map();
  let rowsOnPage = [];
  let sessionsEl = null;

  const cardKey = r => `${String(r.pid || '')}:${String(r.session_id || '')}:${String(r.speaker || r.name || '')}`;
  const actionKey = (r, action) => `${action}:${cardKey(r)}`;
  const isArmed = (r, action) => (arms.get(actionKey(r, action)) || 0) > Date.now();

  function actionResult(r) {
    const value = results.get(cardKey(r));
    if (!value || value.until <= Date.now()) {
      results.delete(cardKey(r));
      return '';
    }
    return `<p class="action-result" role="status">${esc(value.text)}</p>`;
  }

  function actionButton(r, action, label) {
    const armed = isArmed(r, action);
    return `<button type="button" class="act" data-card-action="${esc(action)}" data-name="${esc(r.speaker || r.name || '')}" data-pid="${esc(r.pid)}" data-session-id="${esc(r.session_id)}">${esc(armed ? `Tap again to ${action}` : label)}</button>`;
  }

  function actionRow(r) {
    const idle = r.status === 'idle' && !r.waiting;
    const actions = [
      r.status === 'busy' ? actionButton(r, 'stop', 'Stop') : '',
      idle ? actionButton(r, 'resume', 'Resume') : '',
      idle ? actionButton(r, 'compact', 'Compact') : '',
      idle ? actionButton(r, 'clear', 'Clear') : '',
      actionButton(r, 'end', 'End'),
    ].filter(Boolean).join('');
    return actions ? `<div class="actions">${actions}</div>${actionResult(r)}` : '';
  }

  function cacheChip(cache) {
    if (!cache || cache.rewarm_usd == null) return '';
    const rewarm = Number(cache.rewarm_usd);
    if (!Number.isFinite(rewarm) || rewarm < 1) return '';
    let text = '';
    if (cache.cold) text = `cold: next turn ~${money(rewarm)}`;
    else if (Number(cache.expires_in_s) > 0 && Number(cache.expires_in_s) <= 600) {
      text = `cold in ${Math.ceil(Number(cache.expires_in_s) / 60)} min: ~${money(rewarm)}`;
    }
    return text ? `<span class="cold">${esc(text)}</span>` : '';
  }

  function card(r) {
    const name = r.speaker || r.name || r.project || 'session';
    const pct = Math.max(0, Math.min(100, Number(r.context_pct) || 0));
    return `<article class="card scard${r.waiting ? ' waiting' : ''}" data-pid="${esc(r.pid)}" data-card-key="${esc(cardKey(r))}" style="--h:${hue(name)}">
      <div class="top"><span class="dot ${esc(r.status)}"></span><h3>${esc(name)}</h3>
        ${r.waiting ? '<span class="wbadge">waiting</span>' : ''}${r.needs_clear ? '<span class="clearbadge">needs /clear</span>' : ''}${r.child ? '<span class="faint">spawned</span>' : ''}</div>
      <div class="meta"><span>${esc(r.project)}</span>${r.branch ? `<span>${esc(r.branch)}</span>` : ''}
        <span>${esc(short(r.model))}</span><span>${esc(money(r.cost_usd))}</span><span>${esc(Math.round(pct))}% context</span>
        <span>${esc(ago(r.last_activity))}</span>${cacheChip(r.cache)}</div>
      ${r.title ? `<div class="dim">${esc(r.title)}</div>` : ''}
      ${r.last_reply ? `<p class="reply">${esc(r.last_reply)}</p>` : ''}
      <div class="bar${pct >= 80 ? ' hot' : ''}"><i style="width:${pct}%"></i></div>
      ${actionRow(r)}
    </article>`;
  }

  function renderCards() {
    if (!sessionsEl) return;
    sessionsEl.innerHTML = rowsOnPage.length ? rowsOnPage.map(card).join('') : '<p class="empty">No live sessions.</p>';
  }

  function rememberResult(r, text) {
    const key = cardKey(r);
    results.set(key, {text, until: Date.now() + RESULT_MS});
    setTimeout(() => {
      const value = results.get(key);
      if (value && value.until <= Date.now()) {
        results.delete(key);
        renderCards();
      }
    }, RESULT_MS + 50);
  }

  async function act(event) {
    const button = event.target.closest('[data-card-action]');
    if (!button || !sessionsEl || !sessionsEl.contains(button)) return;
    const cardEl = button.closest('.scard');
    const row = rowsOnPage.find(item => cardKey(item) === cardEl.dataset.cardKey);
    if (!row) return;
    const action = button.dataset.cardAction;
    if (!isArmed(row, action)) {
      const key = actionKey(row, action);
      arms.set(key, Date.now() + ARM_MS);
      setTimeout(() => {
        if (!isArmed(row, action)) {
          arms.delete(key);
          renderCards();
        }
      }, ARM_MS + 50);
      renderCards();
      return;
    }
    arms.delete(actionKey(row, action));
    const name = String(row.speaker || row.name || '');
    const verbs = {stop: 'stopped', resume: 'resumed', compact: 'compacted', clear: 'cleared', end: 'ended'};
    try {
      let result;
      if (action === 'resume' || action === 'end') {
        result = await CC.post(`/api/cave/${action}/${encodeURIComponent(name)}`,
          {pid: row.pid, session_id: row.session_id});
      } else if (action === 'stop') {
        result = await CC.post(`/api/cave/stop/${encodeURIComponent(name)}`, {});
      } else {
        result = await CC.post('/api/cave/send', {to: name, text: `/${action}`});
      }
      if (!result.ok) throw new Error(result.detail || 'refused');
      rememberResult(row, `${verbs[action][0].toUpperCase()}${verbs[action].slice(1)}.`);
    } catch (error) {
      rememberResult(row, `Not ${verbs[action]}: ${error.message}`);
    }
    await refresh();
  }

  function strip(u) {
    const f = u.five_hour || {}, s = u.seven_day || {}, l = u.limits || {};
    const bits = [`5h ${money(f.cost_usd)}`, `7d ${money(s.cost_usd)}`];
    if (l.available) {
      const w = (k, label) => l[k] && l[k].utilization != null ? `${label} ${Math.round(l[k].utilization)}% used` : '';
      bits.push(...[w('five_hour', '5h limit'), w('seven_day', '7d limit')].filter(Boolean));
    } else if (l.reason) bits.push(`limits: ${l.reason}`);
    document.getElementById('strip').textContent = bits.join(' | ');
  }

  async function refresh() {
    const [rows, usage] = await Promise.all([CC.get('/api/sessions'), CC.get('/api/usage').catch(() => null)]);
    if (usage) strip(usage);
    CC.badge('sessions', rows.filter(r => r.waiting).length);
    rowsOnPage = rows;
    renderCards();
  }

  CC.register('sessions', {
    mount(el) {
      sessionsEl = el;
      el.innerHTML = '<p class="empty">Loading sessions...</p>';
      el.addEventListener('click', act);
    },
    refresh,
  });
})();
