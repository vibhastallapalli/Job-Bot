// ── Page transition ───────────────────────────────────────────────────────
(function () {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var content = document.querySelector('.content');
  if (!content) return;

  document.addEventListener('click', function (e) {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;

    var link = e.target.closest('a[href]');
    if (!link) return;

    // Skip new-tab targets, external origins, and same-page anchors
    if (link.target === '_blank') return;
    try { if (new URL(link.href).origin !== window.location.origin) return; } catch (_) { return; }
    if (link.pathname === window.location.pathname && link.hash) return;

    e.preventDefault();
    var dest = link.href;
    content.classList.add('page-leaving');
    setTimeout(function () { window.location.href = dest; }, 180);
  });
})();

// ── Subtitle typing animation ─────────────────────────────────────────────
(function () {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var SPEED = 34; // ms per character

  document.querySelectorAll('.page-subtitle').forEach(function (el) {
    var text = el.textContent.trim();
    if (!text) return;

    // Clear element, add text node + blinking cursor
    el.textContent = '';
    var textNode = document.createTextNode('');
    var cursor   = document.createElement('span');
    cursor.className   = 'type-cursor';
    cursor.textContent = '|';
    el.appendChild(textNode);
    el.appendChild(cursor);

    var i = 0;
    function tick() {
      if (i < text.length) {
        textNode.textContent = text.slice(0, ++i);
        setTimeout(tick, SPEED);
      } else {
        // Stop blinking and fade cursor out after a short pause
        setTimeout(function () {
          cursor.style.animation = 'none';
          cursor.style.opacity   = '0';
        }, 600);
      }
    }
    tick();
  });
})();

// ── Stat card count-up ───────────────────────────────────────────────────
(function () {
  var els = document.querySelectorAll('[data-count]');
  if (!els.length) return;

  var DURATION = 800;

  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

  els.forEach(function (el) {
    var target = parseInt(el.getAttribute('data-count'), 10);
    if (!target) return;          // leave 0 as-is, nothing to animate
    el.textContent = '0';
    var startTs = null;

    requestAnimationFrame(function tick(ts) {
      if (!startTs) startTs = ts;
      var pct = Math.min((ts - startTs) / DURATION, 1);
      el.textContent = Math.round(easeOutCubic(pct) * target);
      if (pct < 1) requestAnimationFrame(tick);
    });
  });
})();

// ── Theme toggle ──────────────────────────────────────────────────────────
(function () {
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.addEventListener('click', function () {
    var light = document.documentElement.classList.toggle('light');
    localStorage.setItem('theme', light ? 'light' : 'dark');
  });
})();

// Auto-dismiss flash messages after 5 seconds
document.querySelectorAll('.flash').forEach(function (el) {
  setTimeout(function () {
    el.style.transition = 'opacity 0.5s';
    el.style.opacity = '0';
    setTimeout(function () { el.remove(); }, 500);
  }, 5000);
});

// ── Scrape live-log panel ─────────────────────────────────────────────────
// Runs only on pages that have the #scrape-live-panel element (jobs page).
(function () {
  var panel      = document.getElementById('scrape-live-panel');
  var spinner    = document.getElementById('scrape-spinner');
  var statusText = document.getElementById('scrape-status-text');
  var logEntries = document.getElementById('scrape-log-entries');
  if (!panel) return;

  var timer         = null;
  var wasInProgress = false;

  function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function poll() {
    fetch('/jobs/scrape/status')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        // Show panel whenever there are logs or a scrape is running
        if (data.in_progress || data.logs.length > 0) {
          panel.style.display = 'block';
        }

        // Spinner + status text
        if (spinner) spinner.style.display = data.in_progress ? 'inline-block' : 'none';
        if (statusText) {
          statusText.textContent = data.in_progress ? 'Scrape running…' : 'Last scrape';
        }

        // Log rows
        if (logEntries) {
          logEntries.innerHTML = data.logs.map(function (l) {
            var time = l.time ? l.time.slice(11, 19) : '';
            return '<div class="live-log-row live-log-row--' + l.level.toLowerCase() + '">' +
              '<span class="live-log-time">' + escHtml(time) + '</span>' +
              '<span class="live-log-msg">' + escHtml(l.message) + '</span>' +
              '</div>';
          }).join('');
        }

        // When scrape just finished, reload so new job rows appear
        if (wasInProgress && !data.in_progress) {
          clearInterval(timer);
          timer = null;
          setTimeout(function () { window.location.reload(); }, 800);
        }

        wasInProgress = data.in_progress;
      })
      .catch(function () { /* network hiccup - keep polling */ });
  }

  poll();
  timer = setInterval(poll, 2000);
})();

// ── Queue selection ───────────────────────────────────────────────────────
(function () {
  var form     = document.getElementById('queue-form');
  var btn      = document.getElementById('queue-btn');
  var countSpan = document.getElementById('queue-count');
  var checkAll = document.getElementById('check-all');
  if (!form) return;

  function getChecked() {
    return Array.from(document.querySelectorAll('.job-check:checked'));
  }

  function updateBtn() {
    var n = getChecked().length;
    if (countSpan) countSpan.textContent = n;
    if (btn) btn.disabled = n === 0;
  }

  document.querySelectorAll('.job-check').forEach(function (cb) {
    cb.addEventListener('change', function () {
      if (checkAll && !this.checked) checkAll.checked = false;
      updateBtn();
    });
  });

  if (checkAll) {
    checkAll.addEventListener('change', function () {
      document.querySelectorAll('.job-check').forEach(function (cb) {
        cb.checked = checkAll.checked;
      });
      updateBtn();
    });
  }

  form.addEventListener('submit', function (e) {
    var checked = getChecked();
    if (checked.length === 0) { e.preventDefault(); return; }
    form.querySelectorAll('input[name="job_ids"]').forEach(function (el) { el.remove(); });
    checked.forEach(function (cb) {
      var inp = document.createElement('input');
      inp.type = 'hidden';
      inp.name = 'job_ids';
      inp.value = cb.value;
      form.appendChild(inp);
    });
  });
})();

// ── Queue status bar ──────────────────────────────────────────────────────
(function () {
  var bar = document.getElementById('queue-status-bar');
  if (!bar) return;

  function poll() {
    fetch('/jobs/queue/stats')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var q = document.getElementById('qs-queued');
        var a = document.getElementById('qs-applied');
        var r = document.getElementById('qs-remaining');
        if (q) q.textContent = data.queued;
        if (a) a.textContent = data.applied_today;
        if (r) r.textContent = data.remaining_today;
      })
      .catch(function () {});
  }

  poll();
  setInterval(poll, 10000);
})();
