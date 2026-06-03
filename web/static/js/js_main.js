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

// ── Jobs sticky summary bar ───────────────────────────────────────────────
(function () {
  var bar      = document.getElementById('jobs-sticky-bar');
  var sentinel = document.getElementById('jobs-scroll-sentinel');
  if (!bar || !sentinel) return;

  var navH = parseInt(
    getComputedStyle(document.documentElement).getPropertyValue('--nav-h'), 10
  ) || 54;

  var obs = new IntersectionObserver(function (entries) {
    var visible = !entries[0].isIntersecting;
    bar.classList.toggle('visible', visible);
    bar.setAttribute('aria-hidden', visible ? 'false' : 'true');
  }, {
    rootMargin: '-' + navH + 'px 0px 0px 0px',
    threshold: 0,
  });

  obs.observe(sentinel);
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

// ── Company favicon / initials avatars ───────────────────────────────────
(function () {
  var logos = document.querySelectorAll('.co-logo[data-url]');
  if (!logos.length) return;

  var PALETTE = ['purple', 'blue', 'green', 'amber', 'red'];

  logos.forEach(function (logo) {
    var company = logo.getAttribute('data-company') || '';
    var url     = logo.getAttribute('data-url')     || '';
    var img     = logo.querySelector('.co-logo__img');
    if (!img) return;

    // Pick colour from palette by hashing first char of company name
    logo.setAttribute('data-color', PALETTE[(company.charCodeAt(0) || 0) % PALETTE.length]);

    // Extract bare hostname from the job posting URL
    var domain = '';
    try { domain = new URL(url).hostname.replace(/^www\./, ''); } catch (_) {}
    if (!domain) return;

    img.src = 'https://www.google.com/s2/favicons?domain='
              + encodeURIComponent(domain) + '&sz=32';

    img.addEventListener('load', function () {
      logo.classList.add('co-logo--loaded');
    }, { once: true });

    // On error the initial stays visible — no action needed
  });
})();

// ── Job description skills tag cloud ─────────────────────────────────────
(function () {
  var cloud  = document.getElementById('tag-cloud');
  var rawEl  = document.getElementById('job-desc-raw');
  if (!cloud || !rawEl) return;

  var STOP = new Set([
    // Common English function words
    'able','about','above','across','after','again','ago','all','also','although',
    'among','and','any','are','around','been','before','being','below','between',
    'both','but','can','come','could','did','does','during','each','either',
    'even','ever','every','for','from','get','give','given','goes','gone',
    'got','had','has','have','help','here','how','into','its','just','large',
    'less','like','look','made','make','may','more','most','much','must',
    'need','nor','not','off','often','once','only','other','our','out','over',
    'own','per','put','same','she','should','since','small','some','such',
    'take','than','that','the','their','them','then','there','these','they',
    'this','those','through','too','two','until','upon','use','used','very',
    'was','well','were','what','when','where','while','who','will','with',
    'within','without','would','yet','you','your',
    // Job-description boilerplate
    'ability','applicant','apply','candidate','collaborate','come','company',
    'compensation','drive','duties','excellent','experience','flexible',
    'grow','hire','hiring','hybrid','include','includes','including',
    'join','looking','opportunity','passion','position','preferred',
    'qualifications','remote','require','required','requirements','role',
    'salary','seeking','team','work','working','years','you\'re',
  ]);

  // Strip any residual HTML tags by parsing through a temporary element
  var tmp = document.createElement('div');
  tmp.innerHTML = rawEl.textContent;
  var text = tmp.textContent || '';

  // Tokenise: lowercase alphabetic runs of 3+ characters
  var tokens = text.toLowerCase().match(/[a-z]{3,}/g) || [];

  // Count frequencies, skipping stopwords
  var freq = {};
  tokens.forEach(function (w) {
    if (!STOP.has(w)) freq[w] = (freq[w] || 0) + 1;
  });

  // Sort by frequency desc, take top 40
  var ranked = Object.keys(freq)
    .map(function (w) { return { word: w, count: freq[w] }; })
    .sort(function (a, b) { return b.count - a.count || a.word.localeCompare(b.word); })
    .slice(0, 40);

  if (!ranked.length) return;

  var maxFreq = ranked[0].count;

  function sizeLevel(count) {
    var r = count / maxFreq;
    if (r >= 0.8) return 5;
    if (r >= 0.6) return 4;
    if (r >= 0.35) return 3;
    if (r >= 0.15) return 2;
    return 1;
  }

  // Display alphabetically so same-size words are visually mixed
  ranked.sort(function (a, b) { return a.word.localeCompare(b.word); });

  var frag = document.createDocumentFragment();
  ranked.forEach(function (item) {
    var span = document.createElement('span');
    span.className = 'tag-cloud__pill';
    span.setAttribute('data-size', sizeLevel(item.count));
    span.setAttribute('title', item.count + (item.count === 1 ? ' mention' : ' mentions'));
    span.textContent = item.word;
    frag.appendChild(span);
  });
  cloud.appendChild(frag);
})();

// ── Dashboard motivational quote ──────────────────────────────────────────
(function () {
  var textEl   = document.getElementById('quote-text');
  var authorEl = document.getElementById('quote-author');
  if (!textEl || !authorEl) return;

  var quotes = [
    { text: "The only way to do great work is to love what you do.", author: "Steve Jobs" },
    { text: "Success is not final, failure is not fatal: it is the courage to continue that counts.", author: "Winston Churchill" },
    { text: "Don't watch the clock; do what it does. Keep going.", author: "Sam Levenson" },
    { text: "Hard work beats talent when talent doesn't work hard.", author: "Tim Notke" },
    { text: "Opportunities don't happen. You create them.", author: "Chris Grosser" },
    { text: "The secret of getting ahead is getting started.", author: "Mark Twain" },
    { text: "It does not matter how slowly you go as long as you do not stop.", author: "Confucius" },
    { text: "You miss 100% of the shots you don't take.", author: "Wayne Gretzky" },
    { text: "The best time to plant a tree was 20 years ago. The second best time is now.", author: "Chinese Proverb" },
    { text: "Whether you think you can or you think you can't, you're right.", author: "Henry Ford" },
    { text: "The only place where success comes before work is in the dictionary.", author: "Vidal Sassoon" },
    { text: "Great things are not done by impulse, but by a series of small things brought together.", author: "Vincent Van Gogh" },
    { text: "I never dreamed about success. I worked for it.", author: "Estée Lauder" },
    { text: "Chase the vision, not the money; the money will end up following you.", author: "Tony Hsieh" },
    { text: "Don't be afraid to give up the good to go for the great.", author: "John D. Rockefeller" },
    { text: "The difference between ordinary and extraordinary is that little extra.", author: "Jimmy Johnson" },
    { text: "Perseverance is not a long race; it is many short races one after another.", author: "Walter Elliot" },
    { text: "The way to get started is to quit talking and begin doing.", author: "Walt Disney" },
    { text: "You don't have to be great to start, but you have to start to be great.", author: "Zig Ziglar" },
    { text: "Your work is going to fill a large part of your life. The only way to be truly satisfied is to do what you believe is great work.", author: "Steve Jobs" },
  ];

  var q = quotes[Math.floor(Math.random() * quotes.length)];
  textEl.textContent   = q.text;
  authorEl.textContent = '— ' + q.author;
})();

// ── Jobs view tab switcher ─────────────────────────────────────────────────
(function () {
  var tabs        = document.querySelectorAll('.view-tab');
  var listSection = document.getElementById('list-view-section');
  var boardSection = document.getElementById('board-view-section');
  if (!tabs.length || !listSection || !boardSection) return;

  function switchTo(view) {
    var showList = view === 'list';
    listSection.style.display  = showList ? '' : 'none';
    boardSection.style.display = showList ? 'none' : '';
    tabs.forEach(function (t) {
      var isActive = t.getAttribute('data-view') === view;
      t.classList.toggle('view-tab--active', isActive);
      t.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    try { localStorage.setItem('jobs-view', view); } catch (_) {}
  }

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      switchTo(tab.getAttribute('data-view'));
    });
  });

  try {
    var saved = localStorage.getItem('jobs-view');
    if (saved === 'board') switchTo('board');
  } catch (_) {}
})();

// ── Kanban drag and drop ───────────────────────────────────────────────────
(function () {
  var board = document.querySelector('.kb-board');
  if (!board) return;

  var dragging  = null;
  var sourceCol = null;

  function colCount(col) {
    return col.querySelector('.kb-col__cards').querySelectorAll('.kb-card').length;
  }

  function updateCount(col) {
    var el = col.querySelector('.kb-col__count');
    if (el) el.textContent = colCount(col);
  }

  board.addEventListener('dragstart', function (e) {
    var card = e.target.closest('.kb-card');
    if (!card) return;
    dragging  = card;
    sourceCol = card.closest('.kb-col');
    setTimeout(function () { card.classList.add('kb-card--dragging'); }, 0);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', card.getAttribute('data-job-id'));
  });

  board.addEventListener('dragend', function () {
    if (dragging) { dragging.classList.remove('kb-card--dragging'); }
    dragging  = null;
    sourceCol = null;
    board.querySelectorAll('.kb-col--over').forEach(function (c) {
      c.classList.remove('kb-col--over');
    });
  });

  board.addEventListener('dragover', function (e) {
    var col = e.target.closest('.kb-col');
    if (!col) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    board.querySelectorAll('.kb-col--over').forEach(function (c) {
      if (c !== col) c.classList.remove('kb-col--over');
    });
    col.classList.add('kb-col--over');
  });

  board.addEventListener('dragleave', function (e) {
    var col = e.target.closest('.kb-col');
    if (col && !col.contains(e.relatedTarget)) {
      col.classList.remove('kb-col--over');
    }
  });

  board.addEventListener('drop', function (e) {
    e.preventDefault();
    var col = e.target.closest('.kb-col');
    if (!col || !dragging) return;
    col.classList.remove('kb-col--over');

    var newStatus = col.getAttribute('data-status');
    var jobId     = dragging.getAttribute('data-job-id');
    var oldStatus = dragging.getAttribute('data-status');
    var prevCol   = sourceCol;

    if (newStatus === oldStatus) return;

    col.querySelector('.kb-col__cards').appendChild(dragging);
    dragging.setAttribute('data-status', newStatus);
    if (prevCol) updateCount(prevCol);
    updateCount(col);

    fetch('/jobs/' + jobId + '/move', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ status: newStatus }),
    }).then(function (r) {
      if (!r.ok && prevCol) {
        prevCol.querySelector('.kb-col__cards').appendChild(dragging);
        dragging.setAttribute('data-status', oldStatus);
        updateCount(prevCol);
        updateCount(col);
      }
    }).catch(function () {
      if (prevCol) {
        prevCol.querySelector('.kb-col__cards').appendChild(dragging);
        dragging.setAttribute('data-status', oldStatus);
        updateCount(prevCol);
        updateCount(col);
      }
    });
  });
})();

// ── Settings tab switcher ────────────────────────────────────────────────
(function () {
  var tabs   = document.querySelectorAll('.stab__tab');
  var panels = document.querySelectorAll('.stab__panel');
  if (!tabs.length) return;

  function activate(targetId) {
    tabs.forEach(function (t) {
      var on = t.getAttribute('data-tab') === targetId;
      t.classList.toggle('stab__tab--active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    panels.forEach(function (p) {
      var on = p.getAttribute('data-panel') === targetId;
      p.classList.toggle('stab__panel--active', on);
    });
    try { localStorage.setItem('settings-tab', targetId); } catch (_) {}
  }

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () { activate(tab.getAttribute('data-tab')); });
  });

  // Restore last-used tab
  try {
    var saved = localStorage.getItem('settings-tab');
    if (saved && document.querySelector('.stab__tab[data-tab="' + saved + '"]')) {
      activate(saved);
    }
  } catch (_) {}
})();

// ── Applicant competition meter ───────────────────────────────────────────
(function () {
  var meter   = document.getElementById('comp-meter');
  if (!meter) return;

  var needleG = document.getElementById('comp-needle-g');
  var labelEl = document.getElementById('comp-label');
  var n       = parseInt(meter.getAttribute('data-applicants'), 10) || 0;
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Map applicant count to a 0–180° gauge angle, zone-proportional
  function toAngle(n) {
    if (n <= 0)  return 0;
    if (n < 25)  return (n / 25) * 60;
    if (n < 75)  return 60 + ((n - 25) / 50) * 60;
    return Math.min(120 + ((n - 75) / 75) * 60, 180);
  }

  function bandFor(n) {
    if (n < 25) return { cls: 'green',  text: 'Low competition'      };
    if (n < 75) return { cls: 'yellow', text: 'Moderate competition' };
    return        { cls: 'red',    text: 'High competition'     };
  }

  var target = toAngle(n);
  var band   = bandFor(n);

  if (labelEl) {
    labelEl.textContent = band.text;
    labelEl.className   = 'comp-meter__label comp-meter__label--' + band.cls;
  }

  function setNeedle(deg) {
    if (needleG) needleG.setAttribute('transform', 'rotate(' + deg.toFixed(2) + ' 60 60)');
  }

  if (reduced) { setNeedle(target); return; }

  var DURATION = 950;
  var startTs  = null;

  function ease(t) { return 1 - Math.pow(1 - t, 3); }

  requestAnimationFrame(function tick(ts) {
    if (!startTs) startTs = ts;
    var p = Math.min((ts - startTs) / DURATION, 1);
    setNeedle(ease(p) * target);
    if (p < 1) requestAnimationFrame(tick);
  });
})();

// ── Daily application goal counter ────────────────────────────────────────
(function () {
  var card    = document.getElementById('daily-goal-card');
  if (!card) return;

  var applied  = parseInt(card.getAttribute('data-applied'), 10) || 0;
  var goal     = parseInt(card.getAttribute('data-goal'),    10) || 10;
  var fillEl   = document.getElementById('dg-fill');
  var countEl  = document.getElementById('dg-count');
  var pctEl    = document.getElementById('dg-pct');
  var burst    = document.getElementById('dg-burst');
  var reduced  = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var targetPct = Math.min(applied / goal, 1);

  if (reduced) {
    if (fillEl)  fillEl.style.width      = (targetPct * 100) + '%';
    if (countEl) countEl.textContent     = applied;
    if (pctEl)   pctEl.textContent       = Math.round(targetPct * 100) + '%';
    return;
  }

  var DURATION = 1100;
  var startTs  = null;

  function ease(t) { return 1 - Math.pow(1 - t, 3); }

  requestAnimationFrame(function tick(ts) {
    if (!startTs) startTs = ts;
    var p   = Math.min((ts - startTs) / DURATION, 1);
    var e   = ease(p);
    var pct = e * targetPct * 100;

    if (fillEl)  fillEl.style.width  = pct.toFixed(1) + '%';
    if (countEl) countEl.textContent = Math.round(e * applied);
    if (pctEl)   pctEl.textContent   = Math.round(pct) + '%';

    if (p < 1) {
      requestAnimationFrame(tick);
    } else if (applied >= goal) {
      spawnParticles();
    }
  });

  function spawnParticles() {
    if (!burst) return;
    var COLORS = [
      'var(--c-amber)', 'var(--accent)', 'var(--c-green)',
      'var(--c-amber)', '#ffffff',       'var(--accent-h)',
    ];
    var count = 16;
    for (var i = 0; i < count; i++) {
      var angle = (i / count) * Math.PI * 2;
      var dist  = 60 + Math.random() * 50;
      var size  = (4 + Math.random() * 5).toFixed(1);
      var p     = document.createElement('span');
      p.className = 'burst-particle';
      p.style.width  = size + 'px';
      p.style.height = size + 'px';
      p.style.background    = COLORS[i % COLORS.length];
      p.style.setProperty('--tx', (Math.cos(angle) * dist).toFixed(0) + 'px');
      p.style.setProperty('--ty', (Math.sin(angle) * dist).toFixed(0) + 'px');
      p.style.animationDelay = (i * 0.035) + 's';
      burst.appendChild(p);
    }
  }
})();

// ── Focus mode ────────────────────────────────────────────────────────────
(function () {
  var btn = document.getElementById('focus-btn');
  if (!btn) return;

  var body      = document.body;
  var iconEnter = btn.querySelector('.focus-icon-enter');
  var iconExit  = btn.querySelector('.focus-icon-exit');
  var label     = btn.querySelector('.focus-btn__label');

  // Create overlay and hint once
  var overlay = document.createElement('div');
  overlay.id = 'focus-overlay';
  overlay.setAttribute('aria-hidden', 'true');
  document.body.appendChild(overlay);

  var hint = document.createElement('div');
  hint.id = 'focus-exit-hint';
  hint.innerHTML = 'Press <kbd>Esc</kbd> to exit focus mode &ensp;&middot;&ensp; <kbd>F</kbd> to toggle';
  hint.setAttribute('role', 'status');
  hint.setAttribute('aria-live', 'polite');
  document.body.appendChild(hint);

  function resetHintAnimation() {
    hint.style.animation = 'none';
    hint.getBoundingClientRect();
    hint.style.animation = '';
  }

  function enterFocus() {
    body.classList.add('focus-mode');
    btn.setAttribute('aria-pressed', 'true');
    if (label) label.textContent = 'Exit';
    if (iconEnter) iconEnter.style.display = 'none';
    if (iconExit)  iconExit.style.display  = '';
    window.scrollTo({ top: 0, behavior: 'smooth' });
    resetHintAnimation();
  }

  function exitFocus() {
    body.classList.remove('focus-mode');
    btn.setAttribute('aria-pressed', 'false');
    if (label) label.textContent = 'Focus';
    if (iconEnter) iconEnter.style.display = '';
    if (iconExit)  iconExit.style.display  = 'none';
  }

  btn.addEventListener('click', function () {
    body.classList.contains('focus-mode') ? exitFocus() : enterFocus();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && body.classList.contains('focus-mode')) {
      exitFocus();
      return;
    }
    if (e.key === 'f' || e.key === 'F') {
      var tag = (document.activeElement || {}).tagName || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      body.classList.contains('focus-mode') ? exitFocus() : enterFocus();
    }
  });
})();

// ── Skills confidence chart ───────────────────────────────────────────────
(function () {
  var bars    = document.querySelectorAll('.skill-bar');
  var chart   = document.getElementById('skills-chart');
  if (!bars.length || !chart) return;

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function animateBars() {
    bars.forEach(function (bar) {
      bar.style.width = bar.dataset.w + 'px';
    });
  }

  if (reduced || !('IntersectionObserver' in window)) {
    animateBars();
    return;
  }

  var obs = new IntersectionObserver(function (entries) {
    if (entries[0].isIntersecting) {
      setTimeout(animateBars, 80);
      obs.disconnect();
    }
  }, { threshold: 0.25 });
  obs.observe(chart);
})();

// ── Logs auto-scroll ──────────────────────────────────────────────────────
(function () {
  var btn  = document.getElementById('autoscroll-btn');
  var wrap = document.getElementById('logs-table-wrap');
  if (!btn || !wrap) return;

  var enabled = false;
  var rafId   = null;

  function scrollToBottom() {
    wrap.scrollTop = wrap.scrollHeight;
  }

  function tick() {
    if (!enabled) return;
    scrollToBottom();
    rafId = requestAnimationFrame(tick);
  }

  function setEnabled(on) {
    enabled = on;
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    if (on) {
      scrollToBottom();
      rafId = requestAnimationFrame(tick);
    } else {
      if (rafId) cancelAnimationFrame(rafId);
    }
  }

  btn.addEventListener('click', function () {
    setEnabled(btn.getAttribute('aria-pressed') !== 'true');
  });

  // Stop auto-scroll if the user manually scrolls up
  wrap.addEventListener('scroll', function () {
    if (!enabled) return;
    var atBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 8;
    if (!atBottom) setEnabled(false);
  }, { passive: true });
})();

// ── Live stats bubble ──────────────────────────────────────────────────────
(function () {
  var toggle   = document.getElementById('lsb-toggle');
  var lsb      = document.getElementById('lsb');
  var totalEl  = document.getElementById('lsb-total');
  var appliedEl= document.getElementById('lsb-applied');
  var rateEl   = document.getElementById('lsb-rate');
  if (!toggle || !lsb) return;

  var collapsed = false;
  try { collapsed = localStorage.getItem('lsb-collapsed') === '1'; } catch(_) {}

  function applyState(animate) {
    lsb.classList.toggle('lsb--collapsed', collapsed);
    toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }

  applyState(false);

  toggle.addEventListener('click', function () {
    collapsed = !collapsed;
    applyState(true);
    try { localStorage.setItem('lsb-collapsed', collapsed ? '1' : '0'); } catch(_) {}
  });

  function loadStats() {
    fetch('/api/stats')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (totalEl)   totalEl.textContent   = d.total;
        if (appliedEl) appliedEl.textContent = d.applied;
        if (rateEl)    rateEl.textContent    = d.success_rate + '%';
      })
      .catch(function () {});
  }

  loadStats();
  setInterval(loadStats, 60000);
})();
