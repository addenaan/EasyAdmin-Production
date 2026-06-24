(function () {
    const state = { dashboard: null, bookings: [], payslips: [], currentTab: 'home', currentBooking: null, deferredPrompt: null };
    const $ = (id) => document.getElementById(id);
    const fmtDate = (value) => (window.EasyAdminFormat ? window.EasyAdminFormat.date(value) : String(value || '').slice(0, 10));
    const fmtMoney = (value) => (window.EasyAdminFormat ? window.EasyAdminFormat.money(value) : Number(value || 0).toFixed(2));
    const safe = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[ch]));
    const statusClass = (status) => {
        if (status === 'Approved' || status === 'Completed') return 'success';
        if (status === 'Pending' || status === 'In Progress') return 'warning';
        if (status === 'Declined' || status === 'Cancelled') return 'danger';
        return 'dark';
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
        if (!response.ok || data.status === 'error') throw new Error(data.message || `Request failed (${response.status})`);
        return data;
    }

    function showToast(message, type) {
        const el = $('toast');
        el.textContent = message;
        el.className = `notice ${type || ''}`.trim();
        el.classList.remove('hidden');
        setTimeout(() => el.classList.add('hidden'), 3500);
    }

    function empty(message) { return `<div class="empty-state">${safe(message)}</div>`; }

    function bookingCard(b) {
        const subtitle = [b.day, fmtDate(b.date), b.time].filter(Boolean).join(' • ');
        const metaHtml = [b.service, b.project_name ? `Project: ${b.project_name}` : '', b.transport ? `Transport: ${b.transport}` : '', Number(b.attachment_count || 0) ? `${b.attachment_count} file(s)` : '']
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

    function leaveCard(r) {
        return `<article class="item-card">
            <div class="item-top">
                <div>
                    <div class="item-title">${safe(r.leave_type)}</div>
                    <div class="item-meta">${safe(fmtDate(r.start_date))} to ${safe(fmtDate(r.end_date))}<br>${safe(r.days)} day(s)${r.reason ? '<br>' + safe(r.reason) : ''}${r.admin_note ? '<br>Office note: ' + safe(r.admin_note) : ''}</div>
                </div>
                <span class="badge ${statusClass(r.status)}">${safe(r.status)}</span>
            </div>
        </article>`;
    }

    function payslipCard(p) {
        const period = p.month || p.date || 'Payslip';
        return `<article class="item-card" data-payslip-id="${safe(p.id)}">
            <div class="item-top">
                <div>
                    <div class="item-title">${safe(p.title || 'Final Payslip')} - ${safe(period)}</div>
                    <div class="item-meta">Net pay: ${safe(fmtMoney(p.net_salary))}<br>Issued: ${safe(fmtDate(p.date))}${p.adjustment_reason ? '<br>Adjustment: ' + safe(p.adjustment_reason) : ''}</div>
                </div>
                <span class="badge success">Finalised</span>
            </div>
            <div class="card-actions">
                <button class="primary-btn compact" type="button" data-download-payslip="${safe(p.id)}">Download PDF</button>
            </div>
        </article>`;
    }


    function renderProfile(data) {
        const e = data.employee || {};
        $('profileCard').classList.remove('loading-card');
        $('profileCard').innerHTML = `<h2>${safe(e.name || 'Staff member')}</h2><div class="profile-meta">${safe([e.job_title, e.emp_number ? 'Employee No: ' + e.emp_number : '', e.start_date ? 'Started: ' + fmtDate(e.start_date) : ''].filter(Boolean).join(' • '))}</div>`;
        const b = data.balances || {};
        $('balanceCards').innerHTML = [
            ['Annual leave', b.annual], ['Sick leave', b.sick], ['Family leave', b.family]
        ].map(([label, value]) => `<div class="stat-card"><div class="stat-value">${safe(value)}</div><div class="stat-label">${safe(label)}</div></div>`).join('');
    }

    async function loadDashboard() {
        try {
            const data = await api('/api/staff/dashboard');
            state.dashboard = data;
            renderProfile(data);
            $('todayBookings').classList.remove('loading-card');
            $('todayBookings').innerHTML = data.today_bookings.length ? data.today_bookings.map(bookingCard).join('') : empty('No bookings scheduled for today.');
            $('upcomingBookings').innerHTML = data.upcoming_bookings.length ? data.upcoming_bookings.map(bookingCard).join('') : empty('No upcoming bookings in the next two weeks.');
            $('leaveHistory').classList.remove('loading-card');
            $('leaveHistory').innerHTML = data.leave_requests.length ? data.leave_requests.map(leaveCard).join('') : empty('No leave requests submitted yet.');
        } catch (err) {
            $('profileCard').innerHTML = empty(err.message);
            $('todayBookings').innerHTML = empty(err.message);
        }
    }

    async function loadBookings() {
        $('bookingsList').innerHTML = 'Loading bookings...';
        $('bookingsList').classList.add('loading-card');
        try {
            const params = new URLSearchParams({ start_date: $('bookingStart').value, end_date: $('bookingEnd').value });
            const data = await api(`/api/staff/bookings?${params.toString()}`);
            state.bookings = data.bookings || [];
            $('bookingsList').classList.remove('loading-card');
            $('bookingsList').innerHTML = data.bookings.length ? data.bookings.map(bookingCard).join('') : empty('No bookings found for this range.');
        } catch (err) {
            $('bookingsList').innerHTML = empty(err.message);
        }
    }

    async function loadLeaveRequests() {
        try {
            const data = await api('/api/staff/leave_requests');
            $('leaveHistory').classList.remove('loading-card');
            $('leaveHistory').innerHTML = data.leave_requests.length ? data.leave_requests.map(leaveCard).join('') : empty('No leave requests submitted yet.');
        } catch (err) {
            $('leaveHistory').innerHTML = empty(err.message);
        }
    }

    async function loadPayslips() {
        const list = $('payslipsList');
        if (!list) return;
        list.innerHTML = 'Loading finalised payslips...';
        list.classList.add('loading-card');
        try {
            const data = await api('/api/staff/payslips');
            state.payslips = data.payslips || [];
            list.classList.remove('loading-card');
            list.innerHTML = state.payslips.length ? state.payslips.map(payslipCard).join('') : empty('No finalised payslips are available yet. Ask payroll/admin to finalise your payslip first.');
        } catch (err) {
            list.innerHTML = empty(err.message);
        }
    }


    async function submitLeave(event) {
        event.preventDefault();
        const form = new FormData($('leaveForm'));
        try {
            const data = await api('/api/staff/leave_requests', { method: 'POST', body: form });
            showToast(data.message || 'Leave request submitted.', 'success');
            $('leaveForm').reset();
            setDefaultDates();
            await loadLeaveRequests();
            await loadDashboard();
        } catch (err) {
            showToast(err.message, 'error');
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
        state.currentBooking = null;
    }

    function attachmentList(attachments) {
        if (!attachments || !attachments.length) return '<div class="empty-state compact">No files uploaded yet.</div>';
        return `<div class="attachment-list">${attachments.map((a) => `<a class="attachment-pill" href="${safe(a.download_url || '#')}" target="_blank" rel="noopener">${safe(a.original_filename || 'File')}</a>`).join('')}</div>`;
    }

    function bookingDetailHtml(b) {
        const canStart = (b.mobile_status || 'Scheduled') !== 'In Progress' && (b.mobile_status || '') !== 'Completed';
        const canComplete = (b.mobile_status || '') !== 'Completed';
        return `<div class="detail-grid">
            <div class="detail-row"><div class="detail-label">Date & Time</div><div class="detail-value">${safe([b.day, fmtDate(b.date), b.time].filter(Boolean).join(' • '))}</div></div>
            <div class="detail-row"><div class="detail-label">Service</div><div class="detail-value">${safe(b.service || 'Not set')}</div></div>
            ${b.project_name ? `<div class="detail-row"><div class="detail-label">Project</div><div class="detail-value">${safe(b.project_name)}</div></div>` : ''}
            ${b.client_address ? `<div class="detail-row"><div class="detail-label">Address</div><div class="detail-value">${safe(b.client_address)}</div></div>` : ''}
            <div class="detail-row"><div class="detail-label">Status</div><div class="detail-value"><span class="badge ${statusClass(b.mobile_status)}">${safe(b.mobile_status || 'Scheduled')}</span></div></div>
            <div class="action-row">
                <button class="primary-btn" type="button" data-staff-action="start" ${canStart ? '' : 'disabled'}>Start Job</button>
                <button class="primary-btn success" type="button" data-staff-action="complete" ${canComplete ? '' : 'disabled'}>Complete Job</button>
            </div>
            <div class="detail-row"><div class="detail-label">Booking Notes</div><div class="detail-value">${b.notes ? safe(b.notes) : 'No notes yet.'}</div></div>
            <form id="bookingNoteForm" class="mini-form">
                <label>Add booking note<textarea id="bookingNoteText" placeholder="Type a note for the office"></textarea></label>
                <button class="primary-btn" type="submit" data-staff-action="note">Add Note</button>
            </form>
            <form id="bookingFileForm" class="mini-form">
                <label>Upload photos/files<input id="bookingFiles" name="files" type="file" multiple accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt"></label>
                <button class="primary-btn" type="submit" data-staff-action="upload">Upload Files</button>
            </form>
            <div class="detail-row"><div class="detail-label">Uploaded Files</div><div id="bookingAttachments" class="detail-value">${attachmentList(b.attachments || [])}</div></div>
        </div>`;
    }

    function payslipDetailHtml(p) {
        return `<div class="detail-grid payslip-detail">
            <div class="detail-row"><div class="detail-label">Payslip Period</div><div class="detail-value">${safe(p.month || p.date || '')}</div></div>
            <div class="detail-row"><div class="detail-label">Status</div><div class="detail-value"><span class="badge success">Finalised</span></div></div>
            ${p.adjustment_reason ? `<div class="detail-row"><div class="detail-label">Adjustment Reason</div><div class="detail-value">${safe(p.adjustment_reason)}</div></div>` : ''}
            <div class="payslip-summary-grid">
                <div class="detail-row"><div class="detail-label">Gross Salary</div><div class="detail-value">${safe(fmtMoney(p.gross_salary))}</div></div>
                <div class="detail-row"><div class="detail-label">Overtime</div><div class="detail-value">${safe(fmtMoney(p.overtime))}</div></div>
                <div class="detail-row"><div class="detail-label">Bonus</div><div class="detail-value">${safe(fmtMoney(p.bonus))}</div></div>
                <div class="detail-row"><div class="detail-label">Transport</div><div class="detail-value">${safe(fmtMoney(p.transport))}</div></div>
                <div class="detail-row"><div class="detail-label">Reimbursable Expenses</div><div class="detail-value">${safe(fmtMoney(p.reimbursable_expenses))}</div></div>
                <div class="detail-row"><div class="detail-label">UIF</div><div class="detail-value">${safe(fmtMoney(p.uif))}</div></div>
                <div class="detail-row"><div class="detail-label">PAYE</div><div class="detail-value">${safe(fmtMoney(p.paye))}</div></div>
                <div class="detail-row"><div class="detail-label">Loan Repayment</div><div class="detail-value">${safe(fmtMoney(p.loan_repayment))}</div></div>
            </div>
            <div class="detail-row net-pay-row"><div class="detail-label">Net Pay</div><div class="detail-value">${safe(fmtMoney(p.net_salary))}</div></div>
            <a class="primary-btn" href="${safe(p.download_url || ('/staff/download_payslip/' + p.id))}" target="_blank" rel="noopener">Download PDF</a>
        </div>`;
    }

    async function openPayslipDetail(id) {
        openSheet('Payslip details', 'Loading...', '<div class="loading-card">Loading payslip...</div>');
        try {
            const data = await api(`/api/staff/payslips/${id}`);
            openSheet((data.payslip && data.payslip.title) || 'Payslip', 'Finalised payslip', payslipDetailHtml(data.payslip));
        } catch (err) {
            openSheet('Payslip details', 'Error', empty(err.message));
        }
    }


    async function openBookingDetail(id) {
        openSheet('Booking details', 'Loading...', '<div class="loading-card">Loading booking...</div>');
        try {
            const data = await api(`/api/staff/bookings/${id}`);
            state.currentBooking = data.booking;
            openSheet(data.booking.client || 'Booking', 'Booking details', bookingDetailHtml(data.booking));
        } catch (err) {
            openSheet('Booking details', 'Error', empty(err.message));
        }
    }

    async function refreshCurrentBooking() {
        if (!state.currentBooking || !state.currentBooking.id) return;
        const data = await api(`/api/staff/bookings/${state.currentBooking.id}`);
        state.currentBooking = data.booking;
        openSheet(data.booking.client || 'Booking', 'Booking details', bookingDetailHtml(data.booking));
    }

    async function updateJobStatus(action) {
        if (!state.currentBooking || !state.currentBooking.id) return;
        const endpoint = action === 'complete' ? 'complete' : 'start';
        try {
            const data = await api(`/api/staff/bookings/${state.currentBooking.id}/${endpoint}`, { method: 'POST' });
            showToast(data.message || 'Booking updated.', 'success');
            await refreshCurrentBooking();
            await loadDashboard();
            if (state.currentTab === 'bookings') await loadBookings();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function addBookingNote(event) {
        event.preventDefault();
        if (!state.currentBooking || !state.currentBooking.id) return;
        const note = ($('bookingNoteText') ? $('bookingNoteText').value : '').trim();
        try {
            const data = await api(`/api/staff/bookings/${state.currentBooking.id}/notes`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ note })
            });
            showToast(data.message || 'Booking note added.', 'success');
            await refreshCurrentBooking();
            await loadDashboard();
            if (state.currentTab === 'bookings') await loadBookings();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function uploadBookingFiles(event) {
        event.preventDefault();
        if (!state.currentBooking || !state.currentBooking.id) return;
        const fileInput = $('bookingFiles');
        const form = new FormData();
        Array.from(fileInput && fileInput.files ? fileInput.files : []).forEach((file) => form.append('files', file));
        if (!form.has('files')) {
            showToast('Please choose at least one file to upload.', 'error');
            return;
        }
        try {
            const data = await api(`/api/staff/bookings/${state.currentBooking.id}/attachments`, { method: 'POST', body: form });
            showToast(data.message || 'File upload complete.', 'success');
            await refreshCurrentBooking();
            await loadDashboard();
            if (state.currentTab === 'bookings') await loadBookings();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    function setTab(tab) {
        state.currentTab = tab;
        document.querySelectorAll('.tab-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === tab));
        document.querySelectorAll('.view').forEach((view) => view.classList.remove('active-view'));
        $(`${tab}View`).classList.add('active-view');
        if (tab === 'bookings' && !$('bookingsList').dataset.loaded) {
            $('bookingsList').dataset.loaded = '1';
            loadBookings();
        }
        if (tab === 'leave') loadLeaveRequests();
        if (tab === 'payslips') loadPayslips();
    }

    function setDefaultDates() {
        const today = new Date();
        const plus30 = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000);
        const fmt = (d) => d.toISOString().slice(0, 10);
        $('bookingStart').value = fmt(today);
        $('bookingEnd').value = fmt(plus30);
        $('leaveStart').value = fmt(today);
        $('leaveEnd').value = fmt(today);
    }

    function bindEvents() {
        document.querySelectorAll('.tab-btn').forEach((btn) => btn.addEventListener('click', () => setTab(btn.dataset.tab)));
        document.querySelectorAll('[data-jump]').forEach((btn) => btn.addEventListener('click', () => setTab(btn.dataset.jump)));
        $('loadBookingsBtn').addEventListener('click', loadBookings);
        if ($('loadPayslipsBtn')) $('loadPayslipsBtn').addEventListener('click', loadPayslips);
        $('leaveForm').addEventListener('submit', submitLeave);
        document.addEventListener('click', (event) => {
            const closeEl = event.target.closest('[data-close-sheet]');
            if (closeEl) return closeSheet();
            const actionEl = event.target.closest('[data-staff-action]');
            if (actionEl && (actionEl.dataset.staffAction === 'start' || actionEl.dataset.staffAction === 'complete')) return updateJobStatus(actionEl.dataset.staffAction);
            const downloadPayslipEl = event.target.closest('[data-download-payslip]');
            if (downloadPayslipEl) {
                event.preventDefault();
                event.stopPropagation();
                window.open(`/staff/download_payslip/${downloadPayslipEl.dataset.downloadPayslip}`, '_blank');
                return;
            }
            const payslipCardEl = event.target.closest('[data-payslip-id]');
            if (payslipCardEl) return openPayslipDetail(payslipCardEl.dataset.payslipId);
            const bookingCardEl = event.target.closest('[data-booking-id]');
            if (bookingCardEl) return openBookingDetail(bookingCardEl.dataset.bookingId);
        });
        document.addEventListener('submit', (event) => {
            if (event.target && event.target.id === 'bookingNoteForm') return addBookingNote(event);
            if (event.target && event.target.id === 'bookingFileForm') return uploadBookingFiles(event);
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
        if ('serviceWorker' in navigator) navigator.serviceWorker.register('/service-worker.js').catch(() => {});
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
