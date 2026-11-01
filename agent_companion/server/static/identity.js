// Stable, local-only visual identities for session names. The shapes are
// generated here rather than shipped as character art.
'use strict';
(function () {
  const HEX = /^#[0-9a-f]{6}$/i;
  let overrides = Object.create(null);

  function escapeAttr(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g,
      char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  }

  function hash(value) {
    let out = 2166136261;
    for (const char of String(value == null ? '' : value)) {
      out ^= char.charCodeAt(0);
      out = Math.imul(out, 16777619);
    }
    return out >>> 0;
  }

  function hexHue(value) {
    if (!HEX.test(value)) return null;
    const red = parseInt(value.slice(1, 3), 16) / 255;
    const green = parseInt(value.slice(3, 5), 16) / 255;
    const blue = parseInt(value.slice(5, 7), 16) / 255;
    const high = Math.max(red, green, blue), low = Math.min(red, green, blue);
    const delta = high - low;
    if (!delta) return 0;
    let result;
    if (high === red) result = ((green - blue) / delta) % 6;
    else if (high === green) result = (blue - red) / delta + 2;
    else result = (red - green) / delta + 4;
    return Math.round((result * 60 + 360) % 360);
  }

  function setOverrides(value) {
    overrides = Object.create(null);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return;
    for (const [name, color] of Object.entries(value)) {
      if (typeof name === 'string' && typeof color === 'string' && HEX.test(color)) {
        overrides[name] = color;
      }
    }
  }

  function hue(name) {
    const pinned = hexHue(overrides[String(name == null ? '' : name)] || '');
    return pinned == null ? hash(name) % 360 : pinned;
  }

  function sprite(name) {
    const label = String(name == null ? '' : name);
    const seed = hash(label);
    const cells = new Map();
    const put = (x, y, className = '') => cells.set(`${x},${y}`, {x, y, className});
    const pair = (x, y, className = '') => {
      put(x, y, className);
      put(11 - x, y, className);
    };

    // The body always has a head, torso and two pixel eyes. Every optional
    // part comes from a hash bit, so a display name always gets the same one.
    for (let y = 2; y <= 8; y++) {
      let reach = y === 2 || y === 8 ? 2 : 3;
      if (y === 3 && (seed & 8)) reach = 2;
      if (y === 7 && (seed & 16)) reach = 2;
      for (let x = 6 - reach; x < 6; x++) pair(x, y);
    }
    if (seed & 1) pair(2, 2);                 // ears
    if (seed & 2) { pair(5, 0); pair(4, 1); } // antennae
    if (seed & 4) { pair(3, 9); pair(4, 9); } // feet
    // A mirrored pattern inside the body and an optional mouth, from a second
    // hash, so a crew of agents differs by shape and not only by colour.
    const mark = hash(`${label}#`);
    let bit = 0;
    for (let y = 3; y <= 7; y++) {
      for (let x = 3; x <= 5; x++) {
        if (y === 4 && x === 4) { bit++; continue; }      // the eye's own cell
        if ((mark >>> (bit++ % 30)) & 1) pair(x, y, ' identity-mark');
      }
    }
    if (mark & (1 << 30)) { put(5, 6, ' identity-mouth'); put(6, 6, ' identity-mouth'); }
    put(4, 4, ' identity-eye');
    put(7, 4, ' identity-eye');

    const rects = [...cells.values()].sort((left, right) => left.y - right.y || left.x - right.x)
      .map(cell => `<rect class="identity-pixel${cell.className}" x="${cell.x}" y="${cell.y}" width="1" height="1" fill="currentColor"/>`)
      .join('');
    return `<svg class="identity-sprite" viewBox="0 0 12 12" role="img" aria-label="Session sprite for ${escapeAttr(label)}" shape-rendering="crispEdges" style="--identity-hue:${hue(label)}">${rects}</svg>`;
  }

  const css = document.createElement('style');
  css.textContent = `
    .identity-sprite { color:hsl(var(--identity-hue) 62% 48%); display:block; flex:0 0 auto; height:1.7rem; width:1.7rem; }
    .identity-sprite .identity-mark { fill:hsl(var(--identity-hue) 70% 32%); }
    .identity-sprite .identity-mouth { fill:var(--raised); }
    .identity-sprite .identity-eye { fill:var(--raised); transform-box:fill-box; transform-origin:center; animation:cc-identity-blink 4.8s ease-in-out infinite; }
    .identity-label { align-items:center; display:inline-flex; gap:6px; min-width:0; }
    @keyframes cc-identity-blink { 0%, 93%, 100% { transform:scaleY(1); } 95%, 97% { transform:scaleY(.15); } }
    @media (prefers-reduced-motion:reduce) { .identity-sprite .identity-eye { animation:none; } }
  `;
  document.head.appendChild(css);

  setOverrides(window.CC_IDENTITY_OVERRIDES);
  window.CCIdentity = {hue, setOverrides, sprite};
})();
