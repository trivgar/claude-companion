// A browser-held Gemini Live call. Audio frames and transcriptions remain in
// memory only; the server records timing and decisions, never words or audio.
'use strict';
(function () {
  const CC = window.CC;
  const POLL_MS = 8000;
  const call = {state: 'idle', id: '', agent: '', operator: '', audio: '', model: '', wsUrl: '', ws: null,
    ready: false, handle: '', mic: null, input: null, output: null, node: null, playAt: 0, sources: [],
    poll: 0, timer: 0, started: 0, pending: [], ring: '', brief: 0, heard: Promise.resolve(), note: ''};

  const bytes = data => {
    const raw = atob(data), out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out.buffer;
  };

  async function post(path, body) {
    const response = await fetch('/api/call/' + path, {method: 'POST', headers: {
      'Content-Type': 'application/json', 'x-claude-companion-key': CC.key()}, body: JSON.stringify(body || {})});
    const data = await response.json().catch(() => ({}));
    return Object.assign(data, {ok: response.ok && data.ok !== false, status: response.status});
  }

  function draw() {
    const el = document.getElementById('call');
    if (!el) return;
    const on = call.state !== 'idle';
    const mins = call.started ? Math.floor((Date.now() - call.started) / 60000) : 0;
    const status = call.state === 'dialing' ? 'Calling' : call.state === 'live'
      ? `On with ${call.operator || 'the operator'}${call.agent ? ', about ' + call.agent : ''}, ${mins} min` : call.note;
    el.innerHTML = `<button type="button" class="call-btn" aria-pressed="${on}">${on ? 'Hang up' : 'Call'}</button>` +
      (status ? `<p class="call-bar" role="status">${CC.esc(status)}</p>` : '');
  }

  function b64(buffer) {
    const view = new Uint8Array(buffer); let text = '';
    for (let i = 0; i < view.length; i += 0x8000) text += String.fromCharCode.apply(null, view.subarray(i, i + 0x8000));
    return btoa(text);
  }

  function sendAudio(buffer) {
    if (call.ws && call.ws.readyState === WebSocket.OPEN && call.ready) {
      call.ws.send(JSON.stringify({realtimeInput: {audio: {mimeType: 'audio/pcm;rate=16000', data: b64(buffer)}}}));
    }
  }

  function sendText(text) {
    if (call.ws && call.ws.readyState === WebSocket.OPEN && call.ready) {
      call.ws.send(JSON.stringify({realtimeInput: {text}}));
    } else call.pending.push(text);
  }

  function play(data) {
    if (!call.output) return;
    const input = new Int16Array(bytes(data)), buffer = call.output.createBuffer(1, input.length, 24000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < input.length; i++) channel[i] = input[i] / 0x8000;
    const source = call.output.createBufferSource();
    source.buffer = buffer; source.connect(call.output.destination);
    const at = Math.max(call.output.currentTime + 0.03, call.playAt);
    source.start(at); call.playAt = at + buffer.duration; call.sources.push(source);
    source.onended = () => { call.sources = call.sources.filter(item => item !== source); };
  }

  async function tools(calls) {
    await call.heard;
    const responses = [];
    for (const item of calls) {
      let result;
      try {
        const response = await post('tool/' + encodeURIComponent(item.name), {call_id: call.id, args: item.args || {}});
        if (response.closed) { hangup(response.detail); return; }
        result = response.ok ? response.result : {error: response.detail || 'The dashboard refused.'};
      } catch (error) { result = {error: 'The dashboard could not be reached.'}; }
      responses.push({id: item.id, name: item.name, response: {result}});
    }
    if (call.ws && call.ws.readyState === WebSocket.OPEN) call.ws.send(JSON.stringify({toolResponse: {functionResponses: responses}}));
  }

  function message(data) {
    if (data.setupComplete) {
      call.ready = true; call.state = 'live'; call.started = call.started || Date.now(); draw();
      const pending = call.pending; call.pending = [];
      if (call.brief) pending.unshift('[Ring] The caller answered. Give the stuck-ask news first.');
      pending.forEach(sendText); return;
    }
    if (data.sessionResumptionUpdate && data.sessionResumptionUpdate.resumable && data.sessionResumptionUpdate.newHandle) {
      call.handle = data.sessionResumptionUpdate.newHandle;
    }
    if (data.goAway) { reconnect(); return; }
    if (data.toolCall) tools(data.toolCall.functionCalls || []);
    const content = data.serverContent;
    if (!content) return;
    if (content.interrupted) {
      call.sources.forEach(source => { try { source.stop(); } catch (error) {} });
      call.sources = []; call.playAt = 0;
    }
    ((content.modelTurn || {}).parts || []).forEach(part => {
      if (part.inlineData && part.inlineData.data && call.audio === 'gemini') play(part.inlineData.data);
    });
    if (content.inputTranscription && content.inputTranscription.text && call.id) {
      const text = content.inputTranscription.text, id = call.id;
      call.heard = call.heard.then(() => post('heard', {call_id: id, text})).catch(() => {});
    }
  }

  function connect(token) {
    const ws = new WebSocket(call.wsUrl + '?access_token=' + encodeURIComponent(token));
    call.ws = ws; call.ready = false;
    ws.onopen = () => ws.send(JSON.stringify({setup: {model: call.model,
      sessionResumption: call.handle ? {handle: call.handle} : {}}}));
    ws.onmessage = async event => {
      const raw = typeof event.data === 'string' ? event.data : await event.data.text();
      if (call.ws !== ws) return;
      try { message(JSON.parse(raw)); } catch (error) {}
    };
    ws.onclose = () => { if (call.ws === ws && call.state !== 'idle') reconnect(); };
  }

  async function reconnect() {
    if (call.state === 'idle' || !call.id) return;
    const old = call.ws; call.ws = null; call.ready = false;
    try { if (old) old.close(); } catch (error) {}
    try {
      const token = await post('token', {call_id: call.id, agent: call.agent});
      if (!token.ok) return hangup(token.detail || 'The call dropped.');
      arm(token); connect(token.token);
    } catch (error) { hangup('The call dropped.'); }
  }

  function arm(token) {
    clearTimeout(call.timer);
    call.timer = setTimeout(() => hangup('That was the last of today\'s call minutes.'),
      Math.max(0, token.hangup_at * 1000 - Date.now()));
  }

  async function poll() {
    if (call.state !== 'live') return;
    try {
      const response = await post('updates', {call_id: call.id});
      if (response.closed) return hangup(response.detail);
      (response.updates || []).forEach(update => sendText(`[Update] ${update.from} wrote: ${update.text}`));
    } catch (error) {}
  }

  async function start(agent, ring) {
    if (call.state !== 'idle') return;
    const Audio = window.AudioContext || window.webkitAudioContext;
    Object.assign(call, {state: 'dialing', agent: agent || '', ring: ring || '', note: '', pending: [], handle: '', brief: 0});
    try {
      if (!Audio || !window.AudioWorkletNode) throw new Error('This browser cannot carry a call.');
      call.input = new Audio(); call.output = new Audio(); draw();
      call.mic = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true,
        noiseSuppression: true, autoGainControl: true}});
      const token = await post('token', Object.assign({agent: call.agent}, call.ring ? {ring: call.ring} : {}));
      if (!token.ok) throw new Error(token.detail || 'No call.');
      Object.assign(call, {id: token.call_id, agent: token.agent || '', operator: token.operator || '', audio: token.audio,
        model: token.model, wsUrl: token.ws_url, brief: token.brief || 0});
      await call.input.resume(); await call.output.resume();
      await call.input.audioWorklet.addModule('/static/call-worklet.js');
      call.node = new AudioWorkletNode(call.input, 'call-mic', {numberOfInputs: 1, numberOfOutputs: 0});
      call.node.port.onmessage = event => sendAudio(event.data);
      call.input.createMediaStreamSource(call.mic).connect(call.node);
      arm(token); connect(token.token); call.poll = setInterval(poll, POLL_MS);
    } catch (error) { hangup(String((error && error.message) || error)); }
  }

  function hangup(note) {
    const id = call.id; clearInterval(call.poll); clearTimeout(call.timer);
    try { if (call.ws) call.ws.close(); } catch (error) {}
    call.sources.forEach(source => { try { source.stop(); } catch (error) {} });
    if (call.mic) call.mic.getTracks().forEach(track => track.stop());
    [call.input, call.output].forEach(context => { try { if (context) context.close(); } catch (error) {} });
    Object.assign(call, {state: 'idle', id: '', agent: '', ws: null, ready: false, mic: null, input: null, output: null,
      node: null, started: 0, pending: [], ring: '', brief: 0, note: note || ''});
    draw(); if (id) post('end', {call_id: id}).catch(() => {});
  }

  document.addEventListener('click', event => {
    if (!event.target.closest('#call .call-btn')) return;
    if (call.state === 'idle') start(''); else hangup('');
  });
  document.addEventListener('companion:call', event => start(String((event.detail || {}).agent || '')));
  window.callHangup = hangup;

  async function answeredRing() {
    const url = new URL(location.href), ring = url.searchParams.get('r'), key = url.searchParams.get('rk');
    if (url.searchParams.get('act') !== 'answer' || !ring || !key) return;
    ['act', 'r', 'rk'].forEach(name => url.searchParams.delete(name));
    history.replaceState(null, '', url.pathname + url.search + url.hash);
    try {
      const response = await post('answer', {ring, n: key});
      if (response.ok) start(String(response.agent || ''), ring); else { call.note = response.detail || 'That ring could not be answered.'; draw(); }
    } catch (error) { call.note = 'That ring could not be answered.'; draw(); }
  }

  window.addEventListener('pagehide', () => { if (call.id) post('end', {call_id: call.id}).catch(() => {}); });
  draw(); answeredRing();
})();
