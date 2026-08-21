(function () {
    const state = {
        dashboard: null,
        currentTab: 'dashboard',
        deferredPrompt: null,
        routeData: [],
    };

    const $ = (id) => document.getElementById(id);
    const money = (value) => (window.EasyAdminFormat ? window.EasyAdminFormat.money(value) : Number(value || 0).toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
    const fmtDate = (value) => (window.EasyAdminFormat ? window.EasyAdminFormat.date(value) : String(value || '').slice(0, 10));
    const safe = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[ch]));
    const statusClass = (status) => {
        if (status === 'Completed') return 'success';
        if (status === 'In Progress') return 'warning';
        if (status === 'Cancelled') return 'danger';
        return 'dark';
    };
    const routeStartStorageKey = () => {
        const companyId = document.body.dataset.companyId || 'company';
        const username = document.body.dataset.username || 'user';
        return `easyadmin:daily-route-start:${companyId}:${username}`;
    };
    const getRememberedRouteStart = () => {
        try {
            return localStorage.getItem(routeStartStorageKey()) || '';
        } catch (error) {
            return '';
        }
    };
    const rememberRouteStart = (value) => {
        const cleanValue = String(value || '').trim();
        if (!cleanValue) return;
        try {
            localStorage.setItem(routeStartStorageKey(), cleanValue);
        } catch (error) {}
    };

    async function api(url, options) {
        const response = await fetch(url, Object.assign({ credentials: 'same-origin' }, options || {}));
        if (response.redirected && response.url.includes('/login')) {
            window.location.href = '/login';
            return null;
        }
        const contentType = response.headers.get('content-type') || '';
        let data;
        if (contentType.includes('application/json')) {
            data = await response.json().catch(() => ({ status: 'error', message: 'The server returned invalid JSON.' }));
        } else {
            const rawText = await response.text().catch(() => '');
            const cleanText = rawText.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
            data = { status: 'error', message: cleanText ? cleanText.slice(0, 220) : `Unexpected server response (${response.status}).` };
        }
        if (!response.ok || data.status === 'error') {
            throw new Error(data.message || `Request failed (${response.status})`);
        }
        return data;
    }

    function showToast(message, type) {
        const el = $('toast');
        el.textContent = message;
        el.className = `notice ${type || ''}`.trim();
        el.classList.remove('hidden');
        setTimeout(() => el.classList.add('hidden'), 3500);
    }

    function empty(message) {
        return `<div class="empty-state">${safe(message)}</div>`;
    }

    function bookingCard(b) {
        const subtitle = [b.day, fmtDate(b.date), b.time].filter(Boolean).join(' • ');
        const metaHtml = [b.employee, b.service, b.project_name ? `Project: ${b.project_name}` : '', b.attachment_count ? `${b.attachment_count} file(s)` : '']
            .filter(Boolean).map(safe).join('<br>');
        return `<article class="item-card" data-booking-id="${b.id}">
            <div class="item-top">
                <div>
                    <div class="item-title">${safe(b.client)}</div>
                    <div class="item-meta">${safe(subtitle)}${metaHtml ? '<br>' + metaHtml : ''}</div>
                </div>
                <span class="badge ${statusClass(b.mobile_status)}">${safe(b.mobile_status || 'Scheduled')}</span>
            </div>
        </article>`;
    }

    function projectCard(p) {
        const subtitleHtml = [p.client, p.status, p.start_date ? `Start: ${fmtDate(p.start_date)}` : '', p.booking_count ? `${p.booking_count} booking(s)` : '', p.attachment_count ? `${p.attachment_count} file(s)` : '']
            .filter(Boolean).map(safe).join('<br>');
        return `<article class="item-card" data-project-id="${p.id}">
            <div class="item-top">
                <div>
                    <div class="item-title">${safe(p.project_name || 'Untitled Project')}</div>
                    <div class="item-meta">${safe(p.project_code || '')}${p.project_code ? '<br>' : ''}${subtitleHtml}</div>
                </div>
                <span class="badge dark">${safe(p.status || 'Project')}</span>
            </div>
        </article>`;
    }

    function invoiceCard(i) {
        return `<article class="item-card">
            <div class="item-top">
                <div>
                    <div class="item-title">${safe(i.client_name || 'Client')}</div>
                    <div class="item-meta">Invoice #${safe(i.id)}<br>Date: ${safe(fmtDate(i.date || ''))}${i.due_date ? '<br>Due: ' + safe(fmtDate(i.due_date)) : ''}</div>
                </div>
                <span class="badge ${i.status === 'Paid' ? 'success' : 'warning'}">${safe(i.status || 'Unpaid')}</span>
            </div>
            <div class="quick-actions"><span class="action-btn">${money(i.total)}</span></div>
        </article>`;
    }

    function renderStats(stats) {
        const items = [
            ['Today', stats.today_bookings || 0],
            ['Upcoming', stats.upcoming_bookings || 0],
            ['Projects', stats.active_projects || 0],
            ['Unpaid', stats.unpaid_invoices || 0],
        ];
        $('dashboardStats').innerHTML = items.map(([label, value]) => `<div class="stat-card"><div class="stat-value">${safe(value)}</div><div class="stat-label">${safe(label)}</div></div>`).join('');
    }

    async function loadDashboard() {
        try {
            const data = await api('/api/mobile/dashboard');
            state.dashboard = data;
            renderStats(data.stats || {});
            $('todayBookings').classList.remove('loading-card');
            $('upcomingBookings').classList.remove('loading-card');
            $('activeProjects').classList.remove('loading-card');
            $('todayBookings').innerHTML = data.today_bookings.length ? data.today_bookings.map(bookingCard).join('') : empty('No bookings scheduled for today.');
            $('upcomingBookings').innerHTML = data.upcoming_bookings.length ? data.upcoming_bookings.map(bookingCard).join('') : empty('No upcoming bookings in the next two weeks.');
            $('activeProjects').innerHTML = data.active_projects.length ? data.active_projects.map(projectCard).join('') : empty('No active projects found.');
            if (!data.modules.invoicing) document.querySelector('[data-tab="invoices"]').classList.add('hidden');
            if (data.modules.booking) {
                $('planDailyRouteBtn').classList.remove('hidden');
            } else {
                $('planDailyRouteBtn').classList.add('hidden');
                document.querySelector('[data-tab="bookings"]').classList.add('hidden');
                document.querySelector('[data-tab="projects"]').classList.add('hidden');
            }
        } catch (err) {
            $('todayBookings').innerHTML = empty(err.message);
        }
    }

    async function loadBookings() {
        const start = $('bookingStart').value;
        const end = $('bookingEnd').value;
        const status = $('bookingStatus').value;
        $('bookingsList').innerHTML = 'Loading bookings...';
        $('bookingsList').classList.add('loading-card');
        try {
            const params = new URLSearchParams({ start_date: start, end_date: end });
            if (status) params.set('status', status);
            const data = await api(`/api/mobile/bookings?${params.toString()}`);
            $('bookingsList').classList.remove('loading-card');
            $('bookingsList').innerHTML = data.bookings.length ? data.bookings.map(bookingCard).join('') : empty('No bookings found for this range.');
        } catch (err) {
            $('bookingsList').innerHTML = empty(err.message);
        }
    }

    async function loadProjects() {
        $('projectsList').innerHTML = 'Loading projects...';
        $('projectsList').classList.add('loading-card');
        try {
            const q = $('projectSearch').value.trim();
            const params = q ? `?q=${encodeURIComponent(q)}` : '';
            const data = await api(`/api/mobile/projects${params}`);
            $('projectsList').classList.remove('loading-card');
            $('projectsList').innerHTML = data.projects.length ? data.projects.map(projectCard).join('') : empty('No projects found.');
        } catch (err) {
            $('projectsList').innerHTML = empty(err.message);
        }
    }

    async function loadInvoices() {
        $('invoicesList').innerHTML = 'Loading invoices...';
        $('invoicesList').classList.add('loading-card');
        try {
            const params = new URLSearchParams({ start_date: $('invoiceStart').value, end_date: $('invoiceEnd').value });
            const data = await api(`/api/mobile/invoices?${params.toString()}`);
            $('invoicesList').classList.remove('loading-card');
            $('invoicesList').innerHTML = data.invoices.length ? data.invoices.map(invoiceCard).join('') : empty('No invoices found for this range.');
        } catch (err) {
            $('invoicesList').innerHTML = empty(err.message);
        }
    }

    function openSheet(title, kicker, html) {
        $('sheetTitle').textContent = title;
        $('sheetKicker').textContent = kicker || '';
        $('sheetContent').innerHTML = html;
        $('detailSheet').classList.remove('hidden');
        document.body.style.overflow = 'hidden';
    }

    function closeSheet() {
        $('detailSheet').classList.add('hidden');
        document.body.style.overflow = '';
    }

    function attachmentList(items) {
        if (!items || !items.length) return empty('No files uploaded yet.');
        return items.map((a) => `<div class="file-row"><span>${safe(a.original_filename)}</span><a href="/download_attachment/${a.id}" target="_blank" rel="noopener">Open</a></div>`).join('');
    }

    function mobileRouteDateValue() {
        if (state.dashboard && state.dashboard.today) return state.dashboard.today;
        const now = new Date();
        const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
        return local.toISOString().slice(0, 10);
    }

    function openMobileRoutePlanner() {
        if (!state.dashboard || !state.dashboard.modules || !state.dashboard.modules.booking) {
            showToast('Booking & Operations access is required to plan a route.', 'error');
            return;
        }
        state.routeData = [];
        openSheet('Daily Route Planner', 'Booking & Ops', `<div class="route-planner-mobile">
            <label>Select Date<input id="mobileRouteDate" type="date" value="${safe(mobileRouteDateValue())}"></label>
            <label>Starting Point (e.g., Office Address)<input id="mobileRouteStart" type="text" value="${safe(getRememberedRouteStart())}" placeholder="Enter office or starting address"></label>
            <button class="primary-btn" type="button" data-route-load>Load bookings</button>
            <div class="route-bookings-box">
                <div class="detail-label">Bookings on Route</div>
                <div id="mobileRouteList" class="route-stop-list">Select a date and tap Load bookings.</div>
            </div>
            <label class="route-checkbox-row">
                <input id="mobileOptimizeRoute" type="checkbox">
                <span><strong>Auto-Optimize geographical route</strong><br><small>Leave unchecked to keep the chronological booking order.</small></span>
            </label>
            <button class="primary-btn route-maps-btn" type="button" data-route-open-maps>📍 Open in Google Maps</button>
        </div>`);
    }

    async function loadMobileRouteBookings() {
        const dateEl = $('mobileRouteDate');
        const list = $('mobileRouteList');
        const date = dateEl ? dateEl.value : '';
        if (!date) {
            showToast('Please select a date.', 'error');
            return;
        }
        if (list) list.innerHTML = '<div class="loading-card">Loading bookings...</div>';
        try {
            const data = await api('/daily_route', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ date })
            });
            state.routeData = Array.isArray(data.route) ? data.route : [];
            renderMobileRouteList();
        } catch (err) {
            state.routeData = [];
            if (list) list.innerHTML = empty(err.message);
        }
    }

    function renderMobileRouteList() {
        const list = $('mobileRouteList');
        if (!list) return;
        if (!state.routeData.length) {
            list.innerHTML = empty('No bookings found for this date.');
            return;
        }
        list.innerHTML = state.routeData.map((booking, index) => {
            const hasAddress = booking.address && booking.address !== 'No address on file';
            return `<div class="route-stop-card">
                <div class="route-stop-main">
                    <div class="item-title">${safe(booking.time || '')} · ${safe(booking.client || 'Client')}</div>
                    <div class="item-meta ${hasAddress ? '' : 'route-missing-address'}">📍 ${safe(hasAddress ? booking.address : 'Missing address')}</div>
                </div>
                <button class="action-btn route-remove-btn" type="button" data-route-remove="${index}">Remove</button>
            </div>`;
        }).join('');
    }

    function removeMobileRouteStop(index) {
        if (!Number.isInteger(index) || index < 0 || index >= state.routeData.length) return;
        state.routeData.splice(index, 1);
        renderMobileRouteList();
    }

    function openMobileRouteInMaps() {
        const startEl = $('mobileRouteStart');
        const optimizeEl = $('mobileOptimizeRoute');
        const startPoint = startEl ? startEl.value.trim() : '';
        const optimize = Boolean(optimizeEl && optimizeEl.checked);
        if (!startPoint) {
            showToast('Please enter a starting point.', 'error');
            return;
        }
        rememberRouteStart(startPoint);
        if (!state.routeData.length) {
            showToast('There are no valid bookings left on the route.', 'error');
            return;
        }
        const validStops = state.routeData.filter((booking) => booking.address && booking.address !== 'No address on file');
        if (!validStops.length) {
            showToast('None of the remaining bookings have valid addresses.', 'error');
            return;
        }
        const origin = encodeURIComponent(startPoint);
        const destination = encodeURIComponent(validStops[validStops.length - 1].address);
        let waypointsParam = '';
        if (validStops.length > 1) {
            const intermediateStops = validStops.slice(0, -1).map((booking) => encodeURIComponent(booking.address));
            const optimizeFlag = optimize ? 'optimize:true|' : '';
            waypointsParam = `&waypoints=${optimizeFlag}${intermediateStops.join('|')}`;
        }
        const mapUrl = `https://www.google.com/maps/dir/?api=1&origin=${origin}&destination=${destination}${waypointsParam}`;
        window.open(mapUrl, '_blank', 'noopener');
    }

    async function openBooking(id) {
        try {
            const data = await api(`/api/mobile/bookings/${id}`);
            const b = data.booking;
            openSheet(b.client, 'Booking details', `<div class="detail-grid">
                <div class="detail-row"><div class="detail-label">Date & Time</div><div class="detail-value">${safe([b.day, fmtDate(b.date), b.time].filter(Boolean).join(' • '))}</div></div>
                <div class="detail-row"><div class="detail-label">Employee</div><div class="detail-value">${safe(b.employee)}</div></div>
                <div class="detail-row"><div class="detail-label">Service</div><div class="detail-value">${safe(b.service || 'Not set')}</div></div>
                ${b.project_name ? `<div class="detail-row"><div class="detail-label">Project</div><div class="detail-value">${safe(b.project_name)}</div></div>` : ''}
                ${b.client_phone ? `<div class="detail-row"><div class="detail-label">Phone</div><div class="detail-value"><a href="tel:${safe(b.client_phone)}">${safe(b.client_phone)}</a></div></div>` : ''}
                ${b.client_email ? `<div class="detail-row"><div class="detail-label">Email</div><div class="detail-value"><a href="mailto:${safe(b.client_email)}">${safe(b.client_email)}</a></div></div>` : ''}
                ${b.client_address ? `<div class="detail-row"><div class="detail-label">Address</div><div class="detail-value">${safe(b.client_address)}</div></div>` : ''}
                <div class="detail-row"><div class="detail-label">Status</div><div class="detail-value"><span class="badge ${statusClass(b.mobile_status)}">${safe(b.mobile_status)}</span></div></div>
            </div>
            <div>
                <div class="detail-label">Update mobile status</div>
                <div class="status-grid">
                    ${['Scheduled', 'In Progress', 'Completed', 'Cancelled'].map((status) => `<button class="action-btn" data-status="${status}" data-booking-status-id="${b.id}">${status}</button>`).join('')}
                </div>
            </div>
            <div>
                <label>Booking notes<textarea id="bookingNotesInput">${safe(b.notes || '')}</textarea></label>
                <button class="primary-btn" type="button" data-save-notes="${b.id}">Save notes</button>
            </div>
            <div class="upload-box">
                <div class="detail-label">Upload files or photos</div>
                <input id="bookingFilesInput" type="file" multiple accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt">
                <button class="primary-btn" type="button" data-upload-booking="${b.id}">Upload selected files</button>
            </div>
            <div><div class="detail-label">Attachments</div><div id="bookingAttachmentList">${attachmentList(b.attachments)}</div></div>`);
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function openProject(id) {
        try {
            const data = await api(`/api/mobile/projects/${id}`);
            const p = data.project;
            openSheet(p.project_name || 'Project', 'Project details', `<div class="detail-grid">
                ${p.project_code ? `<div class="detail-row"><div class="detail-label">Code</div><div class="detail-value">${safe(p.project_code)}</div></div>` : ''}
                <div class="detail-row"><div class="detail-label">Status</div><div class="detail-value">${safe(p.status || 'Not set')}</div></div>
                ${p.client ? `<div class="detail-row"><div class="detail-label">Client</div><div class="detail-value">${safe(p.client)}</div></div>` : ''}
                ${p.site_address ? `<div class="detail-row"><div class="detail-label">Site Address</div><div class="detail-value">${safe(p.site_address)}</div></div>` : ''}
                ${p.description ? `<div class="detail-row"><div class="detail-label">Description</div><div class="detail-value">${safe(p.description)}</div></div>` : ''}
                ${p.notes ? `<div class="detail-row"><div class="detail-label">Notes</div><div class="detail-value">${safe(p.notes)}</div></div>` : ''}
                <div class="detail-row"><div class="detail-label">Value</div><div class="detail-value">${money(p.fixed_price)}</div></div>
            </div>
            <div class="upload-box">
                <div class="detail-label">Upload project files</div>
                <input id="projectFilesInput" type="file" multiple accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt">
                <button class="primary-btn" type="button" data-upload-project="${p.id}">Upload selected files</button>
            </div>
            <div><div class="detail-label">Project attachments</div><div id="projectAttachmentList">${attachmentList(p.attachments)}</div></div>
            <div><div class="detail-label">Linked bookings</div><div class="card-list">${p.bookings.length ? p.bookings.map(bookingCard).join('') : empty('No bookings linked to this project yet.')}</div></div>`);
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function updateBookingStatus(id, status) {
        try {
            await api(`/api/mobile/bookings/${id}/status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status })
            });
            showToast(`Status updated to ${status}.`);
            await openBooking(id);
            await loadDashboard();
            if (state.currentTab === 'bookings') await loadBookings();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function saveNotes(id) {
        try {
            await api(`/api/mobile/bookings/${id}/notes`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ notes: $('bookingNotesInput').value })
            });
            showToast('Notes saved.');
            await loadDashboard();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function uploadFiles(type, id, inputId) {
        const input = $(inputId);
        if (!input || !input.files.length) {
            showToast('Choose at least one file first.', 'error');
            return;
        }
        const form = new FormData();
        form.append('linked_type', type);
        form.append('linked_id', id);
        Array.from(input.files).forEach((file) => form.append('files', file));
        try {
            await api('/api/attachments/upload', { method: 'POST', body: form });
            showToast('Files uploaded.');
            if (type === 'booking') await openBooking(id);
            if (type === 'project') await openProject(id);
            await loadDashboard();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    function setTab(tab) {
        state.currentTab = tab;
        document.querySelectorAll('.tab-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === tab));
        document.querySelectorAll('.view').forEach((view) => view.classList.remove('active-view'));
        $(`${tab}View`).classList.add('active-view');
        if (tab === 'projects') loadProjects();
        if (tab === 'invoices') loadInvoices();
        if (tab === 'bookings' && !$('bookingsList').dataset.loaded) {
            $('bookingsList').dataset.loaded = '1';
            loadBookings();
        }
    }

    function setDefaultDates() {
        const today = new Date();
        const plus14 = new Date(Date.now() + 14 * 24 * 60 * 60 * 1000);
        const plus30 = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000);
        const fmt = (d) => d.toISOString().slice(0, 10);
        $('bookingStart').value = fmt(today);
        $('bookingEnd').value = fmt(plus14);
        $('invoiceStart').value = fmt(today);
        $('invoiceEnd').value = fmt(plus30);
    }

    function bindEvents() {
        document.querySelectorAll('.tab-btn').forEach((btn) => btn.addEventListener('click', () => setTab(btn.dataset.tab)));
        document.querySelectorAll('[data-jump]').forEach((btn) => btn.addEventListener('click', () => setTab(btn.dataset.jump)));
        $('loadBookingsBtn').addEventListener('click', loadBookings);
        $('loadProjectsBtn').addEventListener('click', loadProjects);
        $('loadInvoicesBtn').addEventListener('click', loadInvoices);
        $('planDailyRouteBtn').addEventListener('click', openMobileRoutePlanner);
        $('projectSearch').addEventListener('keydown', (event) => { if (event.key === 'Enter') loadProjects(); });
        document.addEventListener('click', (event) => {
            const bookingCardEl = event.target.closest('[data-booking-id]');
            const projectCardEl = event.target.closest('[data-project-id]');
            const closeEl = event.target.closest('[data-close-sheet]');
            const statusEl = event.target.closest('[data-booking-status-id]');
            const notesEl = event.target.closest('[data-save-notes]');
            const bookingUploadEl = event.target.closest('[data-upload-booking]');
            const projectUploadEl = event.target.closest('[data-upload-project]');
            const routeLoadEl = event.target.closest('[data-route-load]');
            const routeMapsEl = event.target.closest('[data-route-open-maps]');
            const routeRemoveEl = event.target.closest('[data-route-remove]');
            if (routeLoadEl) return loadMobileRouteBookings();
            if (routeMapsEl) return openMobileRouteInMaps();
            if (routeRemoveEl) return removeMobileRouteStop(Number(routeRemoveEl.dataset.routeRemove));
            if (statusEl) return updateBookingStatus(statusEl.dataset.bookingStatusId, statusEl.dataset.status);
            if (notesEl) return saveNotes(notesEl.dataset.saveNotes);
            if (bookingUploadEl) return uploadFiles('booking', bookingUploadEl.dataset.uploadBooking, 'bookingFilesInput');
            if (projectUploadEl) return uploadFiles('project', projectUploadEl.dataset.uploadProject, 'projectFilesInput');
            if (closeEl) return closeSheet();
            if (bookingCardEl) return openBooking(bookingCardEl.dataset.bookingId);
            if (projectCardEl) return openProject(projectCardEl.dataset.projectId);
        });
        document.addEventListener('change', (event) => {
            if (event.target && event.target.id === 'mobileRouteStart') rememberRouteStart(event.target.value);
        });
        window.addEventListener('online', () => $('offlineNotice').classList.add('hidden'));
        window.addEventListener('offline', () => $('offlineNotice').classList.remove('hidden'));
    }

    function bindInstall() {
        const btn = $('installBtn');
        window.addEventListener('beforeinstallprompt', (event) => {
            event.preventDefault();
            state.deferredPrompt = event;
            btn.classList.remove('hidden');
        });
        btn.addEventListener('click', async () => {
            if (!state.deferredPrompt) return;
            state.deferredPrompt.prompt();
            await state.deferredPrompt.userChoice;
            state.deferredPrompt = null;
            btn.classList.add('hidden');
        });
    }

    function registerServiceWorker() {
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/service-worker.js').catch(() => {});
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        setDefaultDates();
        bindEvents();
        bindInstall();
        registerServiceWorker();
        if (!navigator.onLine) $('offlineNotice').classList.remove('hidden');
        loadDashboard();
    });
})();
