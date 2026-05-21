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
