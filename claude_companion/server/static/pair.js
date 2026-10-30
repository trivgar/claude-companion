// Trades the one-time code in the address for an owner key, once. The key is
// kept in this browser's localStorage and nowhere else.
'use strict';
(async function () {
  const status = document.getElementById('status');
  const url = new URL(location.href);
  const code = url.searchParams.get('code') || url.searchParams.get('k') || '';
  history.replaceState(null, '', url.pathname);          // the code leaves the address bar
  const fail = msg => {
    status.textContent = msg;
    document.getElementById('help').hidden = false;
  };
  if (!code) return fail('This link has no pairing code.');
  try {
    const r = await fetch('/api/pair', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code})});
    const out = await r.json().catch(() => ({}));
    if (!r.ok || !out.key) return fail(out.detail || `Pairing failed (${r.status}).`);
    localStorage.setItem('cc-owner-key', out.key);
    status.textContent = 'Paired.';
    location.replace('/');
  } catch (e) {
    fail('Could not pair: ' + e.message);
    document.getElementById('go').hidden = true;
  }
})();
