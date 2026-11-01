// Push: turn notifications on for this device.
'use strict';
(function () {
  const esc = CC.esc;
  const b64 = s => { const p = '='.repeat((4 - s.length % 4) % 4), r = (s + p).replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(r), c => c.charCodeAt(0)); };
  let el, note = '';

  async function enable() {
    try {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) throw new Error('This browser cannot do web push.');
      const reg = await navigator.serviceWorker.register('/static/sw.js');
      await navigator.serviceWorker.ready;
      if (await Notification.requestPermission() !== 'granted') throw new Error('Notifications were not allowed.');
      const {key} = await CC.get('/api/push/key');
      const sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64(key)});
      await CC.post('/api/push/subscribe', sub.toJSON());
      note = 'Notifications are on for this device.';
    } catch (e) { note = e.message; }
    await part.refresh();
  }
  async function test() {
    try { const r = await CC.post('/api/push/test', {}); note = r.sent ? 'Test sent.' : 'No devices to send to.'; }
    catch (e) { note = e.message; }
    await part.refresh();
  }

  const part = {
    mount(s) {
      el = s;
      el.addEventListener('click', e => {
        if (e.target.closest('#push-on')) enable();
        if (e.target.closest('#push-test')) test();
      });
    },
    async refresh() {
      const k = await CC.get('/api/push/key');
      el.innerHTML = `<div class="card"><h3>Notifications</h3>
        <p class="dim">Get a notification when a session needs you. ${esc(k.devices)} device${k.devices === 1 ? '' : 's'} subscribed.</p>
        <p><button type="button" class="act primary" id="push-on">Enable notifications</button>
        <button type="button" class="act" id="push-test">Send a test</button></p>
        <p class="faint" role="status">${esc(note)}</p></div>`;
    },
  };
  CC.register('push', part);
})();
