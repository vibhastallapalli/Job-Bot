// ── Command palette ───────────────────────────────────────────────────────
(function () {
  var overlay  = document.getElementById('palette-overlay');
  var input    = document.getElementById('palette-input');
  var list     = document.getElementById('palette-results');
  var emptyEl  = document.getElementById('palette-empty');
  var trigger  = document.getElementById('palette-trigger');
  var hint     = document.getElementById('palette-shortcut-hint');
  if (!overlay || !input || !list) return;

  var activeIdx = -1;
  var results   = [];
  var debounceT = null;
  var reduced   = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Show ⌘K on Mac, Ctrl K everywhere else
  if (hint && /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent)) {
    hint.textContent = '⌘K';
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function open() {
    overlay.classList.add('open');
    overlay.setAttribute('aria-hidden', 'false');
    input.focus();
    input.select();
    fetch('/jobs/search?q=' + encodeURIComponent(input.value.trim()))
      .then(function (r) { return r.json(); })
      .then(renderResults)
      .catch(function () {});
  }

  function close() {
    overlay.classList.remove('open');
    overlay.setAttribute('aria-hidden', 'true');
    input.value = '';
    wipeResults();
  }

  function wipeResults() {
    list.innerHTML = '';
    activeIdx = -1;
    results   = [];
    if (emptyEl) emptyEl.hidden = true;
  }

  function setActive(idx) {
    var items = list.querySelectorAll('.palette__result');
    if (!items.length) return;
    idx = Math.max(0, Math.min(idx, items.length - 1));
    items.forEach(function (el, i) {
      el.classList.toggle('palette__result--active', i === idx);
    });
    if (items[idx]) items[idx].scrollIntoView({ block: 'nearest' });
    activeIdx = idx;
  }

  function renderResults(data) {
    wipeResults();
    results = data;
    if (!data.length) {
      if (emptyEl) emptyEl.hidden = false;
      return;
    }
    data.forEach(function (job, i) {
      var li = document.createElement('li');
      li.className  = 'palette__result';
      li.setAttribute('role', 'option');
      li.innerHTML =
        '<div class="palette__job-icon">' +
          '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24"' +
              ' fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<rect x="2" y="7" width="20" height="14" rx="2"/>' +
            '<path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/>' +
          '</svg>' +
        '</div>' +
        '<div class="palette__meta">' +
          '<div class="palette__title">' + esc(job.title) + '</div>' +
          (job.company ? '<div class="palette__company">' + esc(job.company) + '</div>' : '') +
        '</div>' +
        '<span class="palette__status palette__status--' + esc(job.status) + '">' + esc(job.status) + '</span>';
      li.addEventListener('click', function () { navigate(i); });
      li.addEventListener('mouseenter', function () {
        var items = list.querySelectorAll('.palette__result');
        items.forEach(function (el) { el.classList.remove('palette__result--active'); });
        li.classList.add('palette__result--active');
        activeIdx = i;
      });
      list.appendChild(li);
    });
  }

  function navigate(idx) {
    var job = results[idx >= 0 ? idx : 0];
    if (!job) return;
    close();
    var dest    = '/jobs/' + job.id;
    var content = document.querySelector('.content');
    if (!reduced && content) {
      content.classList.add('page-leaving');
      setTimeout(function () { window.location.href = dest; }, 180);
    } else {
      window.location.href = dest;
    }
  }

  input.addEventListener('input', function () {
    clearTimeout(debounceT);
    debounceT = setTimeout(function () {
      fetch('/jobs/search?q=' + encodeURIComponent(input.value.trim()))
        .then(function (r) { return r.json(); })
        .then(renderResults)
        .catch(function () {});
    }, 160);
  });

  overlay.addEventListener('mousedown', function (e) {
    if (e.target === overlay) close();
  });

  if (trigger) trigger.addEventListener('click', open);
  if (emptyEl) {
    // clicking Esc hint also closes
    var escHint = document.querySelector('.palette__esc-hint');
    if (escHint) escHint.addEventListener('click', close);
  }

  document.addEventListener('keydown', function (e) {
    var mod = e.ctrlKey || e.metaKey;

    if (mod && e.key === 'k') {
      e.preventDefault();
      overlay.classList.contains('open') ? close() : open();
      return;
    }

    if (!overlay.classList.contains('open')) return;

    switch (e.key) {
      case 'Escape':
        e.preventDefault();
        close();
        break;
      case 'ArrowDown':
        e.preventDefault();
        setActive(activeIdx + 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActive(activeIdx <= 0 ? 0 : activeIdx - 1);
        break;
      case 'Enter':
        e.preventDefault();
        navigate(activeIdx >= 0 ? activeIdx : 0);
        break;
    }
  });
})();

// ── Jobs keyboard navigation ─────────────────────────────────────────────
(function () {
  var tbody = document.querySelector('.real-tbody');
  if (!tbody) return;

  var cur  = -1;
  var hint = document.querySelector('.shortcut-bar__hint');

  // Recomputed on every action so hidden (filtered) rows are skipped
  function visibleRows() {
    return Array.from(tbody.querySelectorAll('tr')).filter(function (r) {
      return r.querySelector('.job-check') && r.style.display !== 'none';
    });
  }

  if (!visibleRows().length) return;

  function deactivate() {
    var active = tbody.querySelector('.row--active');
    if (active) active.classList.remove('row--active');
    cur = -1;
  }

  function activate(idx) {
    var rows = visibleRows();
    if (!rows.length) return;
    idx = Math.max(0, Math.min(idx, rows.length - 1));
    deactivate();
    cur = idx;
    rows[cur].classList.add('row--active');
    rows[cur].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    if (hint) hint.classList.remove('visible');
  }

  // Event delegation — works even as rows appear/disappear during filtering
  tbody.addEventListener('click', function (e) {
    if (e.target.closest('a, button, input')) return;
    var row = e.target.closest('tr');
    if (!row) return;
    var rows = visibleRows();
    var i = rows.indexOf(row);
    if (i >= 0) activate(i);
  });

  document.addEventListener('keydown', function (e) {
    if (/^(INPUT|TEXTAREA|SELECT)$/i.test((document.activeElement || {}).tagName || '')) return;
    var rows = visibleRows();

    switch (e.key) {
      case 'j':
        e.preventDefault();
        activate(cur < 0 ? 0 : cur + 1);
        break;

      case 'k':
        e.preventDefault();
        activate(cur < 0 ? 0 : cur - 1);
        break;

      case 'Enter':
        if (cur < 0 || !rows[cur]) { if (hint) hint.classList.add('visible'); return; }
        var link = rows[cur].querySelector('.job-link');
        if (link) window.location.href = link.href;
        break;

      case 'q':
        if (cur < 0 || !rows[cur]) { if (hint) hint.classList.add('visible'); return; }
        e.preventDefault();
        var cb = rows[cur].querySelector('.job-check');
        if (!cb) return;
        var f    = document.createElement('form');
        f.method = 'POST';
        f.action = '/jobs/queue';
        var inp  = document.createElement('input');
        inp.type  = 'hidden';
        inp.name  = 'job_ids';
        inp.value = cb.value;
        f.appendChild(inp);
        document.body.appendChild(f);
        f.submit();
        break;
    }
  });

  // Reset active row when the filter changes so navigation restarts cleanly
  var filterInput = document.getElementById('job-filter');
  if (filterInput) filterInput.addEventListener('input', deactivate);
})();

// ── Live job filter ───────────────────────────────────────────────────────
(function () {
  var input    = document.getElementById('job-filter');
  var countEl  = document.getElementById('job-filter-count');
  var emptyRow = document.getElementById('filter-empty-row');
  var tbody    = document.querySelector('.real-tbody');
  if (!input || !tbody) return;

  var allRows = Array.from(tbody.querySelectorAll('tr')).filter(function (r) {
    return r.querySelector('.job-check');
  });
  if (!allRows.length) return;

  function filter() {
    var q = input.value.trim().toLowerCase();
    var visible = 0;

    allRows.forEach(function (row) {
      var title   = (row.querySelector('.job-link') || {}).textContent || '';
      var company = (row.cells[2]                   || {}).textContent || '';
      var show    = !q || title.toLowerCase().includes(q) || company.toLowerCase().includes(q);
      row.style.display = show ? '' : 'none';
      if (show) visible++;
    });

    if (countEl)  countEl.textContent    = q ? visible + ' of ' + allRows.length : '';
    if (emptyRow) emptyRow.style.display = (q && visible === 0) ? '' : 'none';
  }

  input.addEventListener('input', filter);
})();

// ── Back to top ───────────────────────────────────────────────────────────
(function () {
  var btn = document.getElementById('back-to-top');
  if (!btn) return;

  window.addEventListener('scroll', function () {
    btn.classList.toggle('visible', window.scrollY > 300);
  }, { passive: true });

  btn.addEventListener('click', function () {
    var behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
    window.scrollTo({ top: 0, behavior: behavior });
  });
})();

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
    var suffix = el.textContent.trim().slice(-1) === '%' ? '%' : '';
    el.textContent = '0' + suffix;
    var startTs = null;

    requestAnimationFrame(function tick(ts) {
      if (!startTs) startTs = ts;
      var pct = Math.min((ts - startTs) / DURATION, 1);
      el.textContent = Math.round(easeOutCubic(pct) * target) + suffix;
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

// ── Toast notifications ───────────────────────────────────────────────────
(function () {
  function dismiss(toast) {
    if (toast.classList.contains('dismissing')) return;
    toast.classList.add('dismissing');
    toast.addEventListener('animationend', function () { toast.remove(); }, { once: true });
  }

  document.querySelectorAll('.toast').forEach(function (toast) {
    var timer = setTimeout(function () { dismiss(toast); }, 3000);
    var btn = toast.querySelector('.toast__close');
    if (btn) btn.addEventListener('click', function () { clearTimeout(timer); dismiss(toast); });
  });
})();

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

// ── Application rate progress ring ───────────────────────────────────────
(function () {
  var fill  = document.querySelector('.progress-ring__fill');
  var pctEl = document.querySelector('.ring-pct');
  if (!fill) return;

  var pct           = parseInt(fill.getAttribute('data-pct'), 10) || 0;
  var circumference = 2 * Math.PI * 54;
  var targetOffset  = circumference * (1 - pct / 100);
  var reduced       = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (reduced) {
    fill.style.strokeDashoffset = targetOffset;
    if (pctEl) pctEl.textContent = pct + '%';
    return;
  }

  var DURATION = 1000;
  var startTs  = null;

  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

  requestAnimationFrame(function tick(ts) {
    if (!startTs) startTs = ts;
    var progress = Math.min((ts - startTs) / DURATION, 1);
    var eased    = easeOutCubic(progress);

    fill.style.strokeDashoffset = circumference * (1 - eased * pct / 100);
    if (pctEl) pctEl.textContent = Math.round(eased * pct) + '%';

    if (progress < 1) requestAnimationFrame(tick);
  });
})();

// ── Relative timestamps ───────────────────────────────────────────────────
(function () {
  var els = document.querySelectorAll('.log-time[data-ts]');
  if (!els.length) return;

  function relTime(raw) {
    // SQLite timestamps use a space separator; replace to get a valid ISO string
    var ms   = Date.now() - new Date(raw.replace(' ', 'T')).getTime();
    var sec  = Math.round(ms / 1000);
    if (sec < 5)   return 'just now';
    if (sec < 60)  return sec + 's ago';
    var min  = Math.round(sec / 60);
    if (min < 60)  return min + 'm ago';
    var hr   = Math.round(min / 60);
    if (hr  < 24)  return hr  + 'h ago';
    var day  = Math.round(hr  / 24);
    if (day < 30)  return day + 'd ago';
    var mo   = Math.round(day / 30);
    if (mo  < 12)  return mo  + 'mo ago';
    return Math.round(mo / 12) + 'y ago';
  }

  function update() {
    els.forEach(function (el) {
      var raw = el.getAttribute('data-ts');
      if (!raw) return;
      if (!el.title) el.title = el.textContent.trim(); // set absolute time as tooltip once
      el.textContent = relTime(raw);
    });
  }

  update();
  setInterval(update, 30000);
})();
