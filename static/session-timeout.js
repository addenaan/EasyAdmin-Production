(function () {
    'use strict';

    var script = document.querySelector('script[data-easyadmin-session-timeout]');
    if (!script) return;

    var timeoutSeconds = parseInt(script.getAttribute('data-timeout-seconds') || '900', 10);
    var warningSeconds = parseInt(script.getAttribute('data-warning-seconds') || '300', 10);
    if (!Number.isFinite(timeoutSeconds) || timeoutSeconds < 60) timeoutSeconds = 900;
    if (!Number.isFinite(warningSeconds) || warningSeconds < 30) warningSeconds = 300;
    if (warningSeconds >= timeoutSeconds) warningSeconds = Math.max(30, Math.floor(timeoutSeconds / 3));

    var timeoutMs = timeoutSeconds * 1000;
    var warningMs = warningSeconds * 1000;
    var pingThrottleMs = 60000;
    var logoutInProgress = false;
    var warningVisible = false;
    var timeoutTimer = null;
    var warningTimer = null;
    var countdownTimer = null;
    var redirectTimer = null;
    var deadlineAt = 0;
    var lastPingAt = 0;
    var lastActivityHandledAt = 0;
    var originalFetch = window.fetch ? window.fetch.bind(window) : null;

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? (meta.getAttribute('content') || '') : '';
    }

    function sessionHeaders() {
        var headers = { 'X-Requested-With': 'XMLHttpRequest' };
        var token = csrfToken();
        if (token) headers['X-CSRFToken'] = token;
        return headers;
    }

    function formatTime(totalSeconds) {
        totalSeconds = Math.max(0, Math.ceil(totalSeconds));
        var minutes = Math.floor(totalSeconds / 60);
        var seconds = totalSeconds % 60;
        return String(minutes) + ':' + String(seconds).padStart(2, '0');
    }

    function injectStyles() {
        if (document.getElementById('easyadmin-session-timeout-styles')) return;
        var style = document.createElement('style');
        style.id = 'easyadmin-session-timeout-styles';
        style.textContent = [
            '#easyadmin-session-overlay{position:fixed;inset:0;display:none;align-items:center;justify-content:center;padding:20px;background:rgba(15,23,42,.58);z-index:2147483000;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}',
            '#easyadmin-session-overlay.eas-open{display:flex}',
            '#easyadmin-session-dialog{width:min(520px,100%);background:#fff;border-radius:16px;box-shadow:0 24px 70px rgba(15,23,42,.32);overflow:hidden;color:#172033}',
            '#easyadmin-session-dialog .eas-head{padding:22px 24px 10px;font-size:1.28rem;font-weight:800}',
            '#easyadmin-session-dialog .eas-body{padding:0 24px 20px;line-height:1.55;color:#475569}',
            '#easyadmin-session-dialog .eas-countdown{font-size:2rem;font-weight:800;color:#b45309;margin:14px 0 6px;letter-spacing:.02em}',
            '#easyadmin-session-dialog .eas-actions{display:flex;justify-content:flex-end;gap:10px;padding:16px 24px 22px;border-top:1px solid #e5e7eb}',
            '#easyadmin-session-dialog button{border:0;border-radius:9px;padding:10px 16px;font-weight:700;cursor:pointer}',
            '#easyadmin-session-stay{background:#0d6efd;color:#fff}',
            '#easyadmin-session-stay:hover{background:#0b5ed7}',
            '#easyadmin-session-stay:disabled{opacity:.65;cursor:wait}',
            '#easyadmin-session-login{background:#0d6efd;color:#fff}',
            '#easyadmin-session-status{font-size:.9rem;margin-top:8px;color:#64748b}',
            '@media (max-width:576px){#easyadmin-session-dialog .eas-head{font-size:1.12rem}#easyadmin-session-dialog .eas-countdown{font-size:1.65rem}}'
        ].join('');
        document.head.appendChild(style);
    }

    function ensureDialog() {
        var existing = document.getElementById('easyadmin-session-overlay');
        if (existing) return existing;
        injectStyles();
        var overlay = document.createElement('div');
        overlay.id = 'easyadmin-session-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-labelledby', 'easyadmin-session-title');
        overlay.innerHTML = [
            '<div id="easyadmin-session-dialog">',
            '  <div class="eas-head" id="easyadmin-session-title">Your session is about to expire</div>',
            '  <div class="eas-body">',
            '    <div id="easyadmin-session-message">You will be logged out due to inactivity. Any unsaved information may be lost.</div>',
            '    <div class="eas-countdown" id="easyadmin-session-countdown" aria-live="polite"></div>',
            '    <div id="easyadmin-session-status">Select <strong>Stay Logged In</strong> to continue working.</div>',
            '  </div>',
            '  <div class="eas-actions" id="easyadmin-session-actions">',
            '    <button type="button" id="easyadmin-session-stay">Stay Logged In</button>',
            '  </div>',
            '</div>'
        ].join('');
        document.body.appendChild(overlay);
        var stayButton = document.getElementById('easyadmin-session-stay');
        if (stayButton) stayButton.addEventListener('click', stayLoggedIn);
        return overlay;
    }

    function clearTimers() {
        if (warningTimer) clearTimeout(warningTimer);
        if (timeoutTimer) clearTimeout(timeoutTimer);
        warningTimer = null;
        timeoutTimer = null;
    }

    function hideWarning() {
        warningVisible = false;
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = null;
        var overlay = document.getElementById('easyadmin-session-overlay');
        if (overlay) overlay.classList.remove('eas-open');
        var stayButton = document.getElementById('easyadmin-session-stay');
        if (stayButton) {
            stayButton.disabled = false;
            stayButton.textContent = 'Stay Logged In';
        }
    }

    function updateCountdown() {
        if (!warningVisible) return;
        var remainingMs = deadlineAt - Date.now();
        if (remainingMs <= 0) {
            endSession();
            return;
        }
        var counter = document.getElementById('easyadmin-session-countdown');
        if (counter) counter.textContent = formatTime(remainingMs / 1000);
    }

    function showWarning() {
        if (logoutInProgress || warningVisible) return;
        warningVisible = true;
        var overlay = ensureDialog();
        var title = document.getElementById('easyadmin-session-title');
        var message = document.getElementById('easyadmin-session-message');
        var actions = document.getElementById('easyadmin-session-actions');
        var status = document.getElementById('easyadmin-session-status');
        if (title) title.textContent = 'Your session is about to expire';
        if (message) message.textContent = 'You will be logged out due to inactivity. Any unsaved information may be lost.';
        if (status) status.innerHTML = 'Select <strong>Stay Logged In</strong> to continue working.';
        if (actions) actions.innerHTML = '<button type="button" id="easyadmin-session-stay">Stay Logged In</button>';
        var stayButton = document.getElementById('easyadmin-session-stay');
        if (stayButton) stayButton.addEventListener('click', stayLoggedIn);
        overlay.classList.add('eas-open');
        updateCountdown();
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = setInterval(updateCountdown, 1000);
        if (stayButton) stayButton.focus();
    }

    function showExpiredNotice() {
        if (countdownTimer) clearInterval(countdownTimer);
        countdownTimer = null;
        warningVisible = true;
        var overlay = ensureDialog();
        var title = document.getElementById('easyadmin-session-title');
        var message = document.getElementById('easyadmin-session-message');
        var counter = document.getElementById('easyadmin-session-countdown');
        var status = document.getElementById('easyadmin-session-status');
        var actions = document.getElementById('easyadmin-session-actions');
        if (title) title.textContent = 'Your Easy Admin session has expired';
        if (message) message.textContent = 'For your security, you have been logged out after a period of inactivity.';
        if (counter) counter.textContent = '';
        if (status) status.textContent = 'You will be taken to the login screen. Unsaved changes on this page cannot be submitted after the session has expired.';
        if (actions) actions.innerHTML = '<button type="button" id="easyadmin-session-login">Go to Login</button>';
        var loginButton = document.getElementById('easyadmin-session-login');
        if (loginButton) loginButton.addEventListener('click', redirectToLogin);
        overlay.classList.add('eas-open');
        if (loginButton) loginButton.focus();
        if (redirectTimer) clearTimeout(redirectTimer);
        redirectTimer = setTimeout(redirectToLogin, 3500);
    }

    function redirectToLogin() {
        if (redirectTimer) clearTimeout(redirectTimer);
        if (window.location.pathname !== '/login') {
            window.location.href = '/login?timeout=1';
        }
    }

    function scheduleCycle() {
        if (logoutInProgress) return;
        clearTimers();
        hideWarning();
        var now = Date.now();
        deadlineAt = now + timeoutMs;
        var warningDelay = Math.max(0, timeoutMs - warningMs);
        warningTimer = setTimeout(showWarning, warningDelay);
        timeoutTimer = setTimeout(endSession, timeoutMs);
    }

    function pingServer(force) {
        if (!originalFetch || logoutInProgress) return Promise.resolve(false);
        var now = Date.now();
        if (!force && now - lastPingAt < pingThrottleMs) return Promise.resolve(true);
        lastPingAt = now;

        return originalFetch('/api/session/ping', {
            method: 'POST',
            credentials: 'same-origin',
            headers: sessionHeaders(),
            keepalive: true
        }).then(function (response) {
            if (response.status === 401) {
                logoutInProgress = true;
                clearTimers();
                showExpiredNotice();
                return false;
            }
            if (!response.ok) throw new Error('Session keep-alive failed with status ' + response.status);
            return response.json().then(function (data) {
                if (data && Number.isFinite(Number(data.timeout_seconds))) {
                    timeoutSeconds = Number(data.timeout_seconds);
                    timeoutMs = timeoutSeconds * 1000;
                }
                if (data && Number.isFinite(Number(data.warning_seconds))) {
                    warningSeconds = Number(data.warning_seconds);
                    warningMs = warningSeconds * 1000;
                }
                return true;
            }).catch(function () { return true; });
        }).catch(function () {
            return false;
        });
    }

    function stayLoggedIn(event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        if (logoutInProgress) return;
        var button = document.getElementById('easyadmin-session-stay');
        var status = document.getElementById('easyadmin-session-status');
        if (button) {
            button.disabled = true;
            button.textContent = 'Keeping you logged in…';
        }
        if (status) status.textContent = 'Refreshing your secure session…';

        pingServer(true).then(function (ok) {
            if (!ok) {
                if (!logoutInProgress) {
                    if (button) {
                        button.disabled = false;
                        button.textContent = 'Try Again';
                    }
                    if (status) status.textContent = 'Easy Admin could not refresh your session. Check your connection and try again before the countdown ends.';
                }
                return;
            }
            lastActivityHandledAt = Date.now();
            scheduleCycle();
        });
    }

    function endSession() {
        if (logoutInProgress) return;
        logoutInProgress = true;
        clearTimers();

        if (!originalFetch) {
            showExpiredNotice();
            return;
        }

        originalFetch('/api/session/timeout', {
            method: 'POST',
            credentials: 'same-origin',
            headers: sessionHeaders(),
            keepalive: true
        }).catch(function () {
            // The browser is redirected regardless; the server also enforces expiry.
        }).finally(showExpiredNotice);
    }

    function activityDetected() {
        if (logoutInProgress || warningVisible) return;
        var now = Date.now();
        if (now - lastActivityHandledAt < 1000) return;
        lastActivityHandledAt = now;
        scheduleCycle();
        pingServer(false);
    }

    if (originalFetch) {
        window.fetch = function () {
            return originalFetch.apply(null, arguments).then(function (response) {
                if (response && response.status === 401 && !logoutInProgress) {
                    response.clone().json().then(function (data) {
                        if (data && (data.status === 'timeout' || data.redirect)) {
                            logoutInProgress = true;
                            clearTimers();
                            showExpiredNotice();
                        }
                    }).catch(function () {
                        // Leave unrelated non-JSON 401 responses to the calling page.
                    });
                }
                return response;
            });
        };
    }

    ['click', 'mousemove', 'keydown', 'scroll', 'touchstart', 'touchmove', 'input', 'change'].forEach(function (eventName) {
        window.addEventListener(eventName, activityDetected, { passive: true });
    });

    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState !== 'visible' || logoutInProgress) return;
        var now = Date.now();
        if (now >= deadlineAt) {
            endSession();
        } else if (now >= deadlineAt - warningMs) {
            showWarning();
        } else {
            activityDetected();
        }
    });

    scheduleCycle();
    pingServer(true);
})();
