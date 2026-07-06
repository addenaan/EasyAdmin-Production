(function () {
    'use strict';

    if (!('serviceWorker' in navigator)) return;

    let waitingRegistration = null;
    let refreshingForUpdate = false;

    function ensureBanner() {
        let banner = document.getElementById('easyadminUpdatePrompt');
        if (banner) return banner;

        banner = document.createElement('button');
        banner.id = 'easyadminUpdatePrompt';
        banner.type = 'button';
        banner.textContent = 'New version available. Tap to update.';
        banner.setAttribute('aria-live', 'polite');
        banner.style.position = 'fixed';
        banner.style.left = '16px';
        banner.style.right = '16px';
        banner.style.bottom = 'calc(16px + env(safe-area-inset-bottom, 0px))';
        banner.style.zIndex = '99999';
        banner.style.border = '0';
        banner.style.borderRadius = '14px';
        banner.style.padding = '14px 16px';
        banner.style.fontWeight = '800';
        banner.style.boxShadow = '0 12px 30px rgba(0,0,0,0.25)';
        banner.style.background = '#0d6efd';
        banner.style.color = '#ffffff';
        banner.style.display = 'none';
        banner.style.cursor = 'pointer';
        banner.addEventListener('click', function () {
            refreshingForUpdate = true;
            if (waitingRegistration && waitingRegistration.waiting) {
                waitingRegistration.waiting.postMessage({ type: 'SKIP_WAITING' });
                setTimeout(function () { window.location.reload(); }, 800);
            } else {
                window.location.reload();
            }
        });
        document.body.appendChild(banner);
        return banner;
    }

    function showUpdatePrompt(registration) {
        waitingRegistration = registration || waitingRegistration;
        const banner = ensureBanner();
        banner.style.display = 'block';
    }

    function watchRegistration(registration) {
        if (!registration) return;
        if (registration.waiting && navigator.serviceWorker.controller) {
            showUpdatePrompt(registration);
        }
        registration.addEventListener('updatefound', function () {
            const newWorker = registration.installing;
            if (!newWorker) return;
            newWorker.addEventListener('statechange', function () {
                if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                    showUpdatePrompt(registration);
                }
            });
        });
    }

    navigator.serviceWorker.addEventListener('controllerchange', function () {
        if (refreshingForUpdate) {
            window.location.reload();
        } else {
            showUpdatePrompt(waitingRegistration);
        }
    });

    window.addEventListener('load', function () {
        navigator.serviceWorker.register('/service-worker.js')
            .then(function (registration) {
                watchRegistration(registration);
                if (registration.update) registration.update().catch(function () {});
            })
            .catch(function () {});
    });
})();
