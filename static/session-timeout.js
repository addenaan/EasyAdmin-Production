(function () {
    'use strict';

    var script = document.querySelector('script[data-easyadmin-session-timeout]');
    var timeoutSeconds = parseInt((script && script.getAttribute('data-timeout-seconds')) || '300', 10);
    if (!Number.isFinite(timeoutSeconds) || timeoutSeconds < 30) timeoutSeconds = 300;

    var timeoutMs = timeoutSeconds * 1000;
    var pingThrottleMs = 60000;
    var logoutInProgress = false;
    var timeoutTimer = null;
    var lastPingAt = 0;
    var originalFetch = window.fetch ? window.fetch.bind(window) : null;

    function redirectToLogin() {
        if (window.location.pathname !== '/login') {
            window.location.href = '/login?timeout=1';
        }
    }

    function endSession() {
        if (logoutInProgress) return;
        logoutInProgress = true;

        if (navigator.sendBeacon) {
            try {
                navigator.sendBeacon('/api/session/timeout', new Blob([], { type: 'application/json' }));
            } catch (err) {
                // Fall back to fetch below.
            }
        }

        if (originalFetch) {
            originalFetch('/api/session/timeout', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                keepalive: true
            }).finally(redirectToLogin).catch(redirectToLogin);
        } else {
            redirectToLogin();
        }
    }

    function scheduleTimeout() {
        if (timeoutTimer) clearTimeout(timeoutTimer);
        timeoutTimer = setTimeout(endSession, timeoutMs);
    }

    function pingServer(force) {
        if (!originalFetch || logoutInProgress) return;
        var now = Date.now();
        if (!force && now - lastPingAt < pingThrottleMs) return;
        lastPingAt = now;

        originalFetch('/api/session/ping', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            keepalive: true
        }).then(function (response) {
            if (response.status === 401) redirectToLogin();
        }).catch(function () {
            // Network failures should not disable the client-side timeout.
        });
    }

    function activityDetected() {
        if (logoutInProgress) return;
        scheduleTimeout();
        pingServer(false);
    }

    if (originalFetch) {
        window.fetch = function () {
            return originalFetch.apply(null, arguments).then(function (response) {
                if (response && response.status === 401) {
                    response.clone().json().then(function (data) {
                        if (data && data.redirect) redirectToLogin();
                    }).catch(function () {
                        // Non-JSON 401 responses are left for the calling code.
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
        if (document.visibilityState === 'visible') {
            activityDetected();
        }
    });

    scheduleTimeout();
    pingServer(true);
})();
