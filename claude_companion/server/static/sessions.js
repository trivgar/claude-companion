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
    return `<article class="card scard${r.waiting ? ' waiting' : ''}" data-pid="${esc(r.pid)}" style="--h:${hue(name)}">
      <div class="top"><span class="dot ${esc(r.status)}"></span><h3>${esc(name)}</h3>
        ${r.waiting ? '<span class="wbadge">waiting</span>' : ''}${r.needs_clear ? '<span class="clearbadge">needs /clear</span>' : ''}${r.child ? '<span class="faint">spawned</span>' : ''}</div>
      <div class="meta"><span>${esc(r.project)}</span>${r.branch ? `<span>${esc(r.branch)}</span>` : ''}
        <span>${esc(short(r.model))}</span><span>${esc(money(r.cost_usd))}</span><span>${esc(Math.round(pct))}% context</span>
        <span>${esc(ago(r.last_activity))}</span>${cacheChip(r.cache)}</div>
      ${r.title ? `<div class="dim">${esc(r.title)}</div>` : ''}
      ${r.last_reply ? `<p class="reply">${esc(r.last_reply)}</p>` : ''}
      <div class="bar${pct >= 80 ? ' hot' : ''}"><i style="width:${pct}%"></i></div>
    </article>`;
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

  CC.register('sessions', {
    mount(el) { el.innerHTML = '<p class="empty">Loading sessions...</p>'; },
    async refresh() {
      const el = document.querySelector('#views > section[data-id="sessions"]');
      const [rows, usage] = await Promise.all([CC.get('/api/sessions'), CC.get('/api/usage').catch(() => null)]);
      if (usage) strip(usage);
      CC.badge('sessions', rows.filter(r => r.waiting).length);
      el.innerHTML = rows.length ? rows.map(card).join('') : '<p class="empty">No live sessions.</p>';
    },
  });
})();
