// The chat feature keeps its historic feature id while its full client lives
// in cave.js, matching the direct-line API address family.
'use strict';
(function () {
  const script = document.createElement('script');
  script.src = '/static/cave.js';
  document.body.appendChild(script);
})();
