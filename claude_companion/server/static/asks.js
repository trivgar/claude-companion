// Asks: what a session is waiting on you for. A permission request gets Allow
// and Deny; a question gets its options. Answered here, the session's hook
// hands the answer back; an ask whose session stopped waiting disappears.
'use strict';
(function () {
  const esc = CC.esc;
  let el = null, sig = null, busy = false;

  function card(a) {
    const who = a.speaker || a.project || 'A session';
    if (a.questions && a.questions.length) {
      const qs = a.questions.map((q, i) => `
        <fieldset class="q" data-q="${i}" data-multi="${q.multiSelect ? 1 : 0}" data-text="${esc(q.question)}">
          <legend>${esc(q.header ? q.header + ': ' : '')}${esc(q.question)}</legend>
          ${q.options.map(o => `<button type="button" class="act opt" aria-pressed="false" data-label="${esc(o.label)}">
            ${esc(o.label)}${o.description ? `<span class="faint"> ${esc(o.description)}</span>` : ''}</button>`).join(' ')}
        </fieldset>`).join('');
      return `<div class="card" data-ask="${esc(a.id)}">
        <h3><span class="needs">${esc(who)}</span> asks</h3>${qs}
        <p><button type="button" class="act primary send" disabled>Send answers</button></p>
        <p class="faint err" role="status"></p></div>`;
    }
    const short = !!a.shortened;
    const more = short ? `<p class="faint">Shortened. <button type="button" class="act showall" aria-expanded="false">Show all</button></p>
      <pre class="full" hidden tabindex="0" style="max-height:40vh;overflow:auto;white-space:pre-wrap;word-break:break-word;margin:0 0 8px">${esc(a.input_full || '')}</pre>
      ${a.input_truncated ? '<p class="faint">Even the full view is cut at 20 KB.</p>' : ''}` : '';
    return `<div class="card" data-ask="${esc(a.id)}" data-short="${short ? 1 : 0}">
      <h3><span class="needs">${esc(who)}</span> needs your OK</h3>
      <p><b>${esc(a.tool)}</b> <code>${esc(a.what)}</code></p>${more}
      <p><button type="button" class="act primary decide" data-d="allow"${short ? ' disabled' : ''}>Allow</button>
         <button type="button" class="act decide" data-d="deny">Deny</button></p>
      <p class="faint err" role="status"></p></div>`;
  }

  function ready(c) {
    const qs = [...c.querySelectorAll('fieldset.q')];
    c.querySelector('.send').disabled = !qs.every(q => q.querySelector('.opt[aria-pressed="true"]'));
  }

  async function act(c, fn) {
    const err = c.querySelector('.err');
    err.textContent = '';
    busy = true;
    try { await fn(); sig = null; busy = false; await refresh(); }
    catch (e) { err.textContent = 'Not sent: ' + e.message; }
    finally { busy = false; }
  }

  function mount(root) {
    el = root;
    el.addEventListener('click', ev => {
      const c = ev.target.closest('[data-ask]');
      if (!c) return;
      const id = c.dataset.ask;
      const sa = ev.target.closest('.showall');
      if (sa) {
        const pre = c.querySelector('pre.full');
        pre.hidden = !pre.hidden;
        sa.textContent = pre.hidden ? 'Show all' : 'Hide';
        sa.setAttribute('aria-expanded', String(!pre.hidden));
        if (!pre.hidden) { c.dataset.viewed = '1'; c.querySelector('.decide[data-d=allow]').disabled = false; }
        return;
      }
      const d = ev.target.closest('.decide');
      if (d) return act(c, () => CC.post(`/api/asks/${id}/decide`,
        {decision: d.dataset.d, reviewed: c.dataset.viewed === '1'}));
      const o = ev.target.closest('.opt');
      if (o) {
        const q = o.closest('fieldset.q');
        if (q.dataset.multi !== '1') q.querySelectorAll('.opt').forEach(b => b.setAttribute('aria-pressed', 'false'));
        o.setAttribute('aria-pressed', String(o.getAttribute('aria-pressed') !== 'true'));
        return ready(c);
      }
      if (ev.target.closest('.send')) {
        const answers = {};
        c.querySelectorAll('fieldset.q').forEach(q => {
          answers[q.dataset.text] = [...q.querySelectorAll('.opt[aria-pressed="true"]')].map(b => b.dataset.label).join(', ');
        });
        act(c, () => CC.post(`/api/asks/${id}/answer`, {answers}));
      }
    });
  }

  async function refresh() {
    if (!el || busy) return;
    const {asks} = await CC.get('/api/asks');
    CC.badge('asks', asks.length);
    const next = asks.map(a => a.id).join(',');
    if (next === sig) return;                 // the same asks: keep the picks being made
    sig = next;
    el.innerHTML = asks.length ? asks.map(card).join('')
      : '<p class="empty">Nothing is waiting on you.</p>';
  }

  CC.register('asks', {mount, refresh});
})();
