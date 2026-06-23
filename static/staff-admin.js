(function () {
    const $ = (id) => document.getElementById(id);
    const fmtDate = (value) => (window.EasyAdminFormat ? window.EasyAdminFormat.date(value) : String(value || '').slice(0, 10));
    const safe = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[ch]));
    const statusClass = (status) => {
        if (status === 'Approved') return 'success';
        if (status === 'Pending') return 'warning';
        if (status === 'Declined') return 'danger';
        return 'dark';
    };

    async function api(url, options) {
        const response = await fetch(url, Object.assign({ credentials: 'same-origin' }, options || {}));
        const contentType = response.headers.get('content-type') || '';
        let data;
        if (contentType.includes('application/json')) data = await response.json().catch(() => ({ status: 'error', message: 'The server returned invalid JSON.' }));
        else {
            const rawText = await response.text().catch(() => '');
            data = { status: 'error', message: rawText.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 220) || `Unexpected server response (${response.status}).` };
        }
        if (!response.ok || data.status === 'error') throw new Error(data.message || `Request failed (${response.status})`);
        return data;
    }

    function showToast(message, type) {
        const el = $('adminToast');
        el.textContent = message;
        el.className = `notice ${type || ''}`.trim();
        el.classList.remove('hidden');
        setTimeout(() => el.classList.add('hidden'), 3500);
    }

    function employeeRow(e) {
        const username = e.staff_username || (e.email || '').toLowerCase() || '';
        return `<article class="employee-row" data-employee-row="${e.id}">
            <div>
                <div class="row-title">${safe(e.name || 'Unnamed employee')}</div>
                <div class="row-meta">${safe([e.job_title, e.emp_number ? 'Employee No: ' + e.emp_number : '', e.status].filter(Boolean).join(' • '))}</div>
            </div>
            <div class="inline-form">
                <label>Username<input data-username="${e.id}" value="${safe(username)}" placeholder="staff username"></label>
                <label>Password<input data-password="${e.id}" type="password" placeholder="Set or reset password"></label>
                <button class="primary-btn" data-save-account="${e.id}">${e.staff_user_id ? 'Update' : 'Create'}</button>
            </div>
        </article>`;
    }

    function requestRow(r) {
        const canReview = r.status === 'Pending';
        return `<article class="request-row">
            <div class="item-top">
                <div>
                    <div class="row-title">${safe(r.employee_name)} — ${safe(r.leave_type)}</div>
                    <div class="row-meta">${safe(fmtDate(r.start_date))} to ${safe(fmtDate(r.end_date))} • ${safe(r.days)} day(s)<br>${r.reason ? safe(r.reason) : 'No reason supplied.'}${r.admin_note ? '<br>Admin note: ' + safe(r.admin_note) : ''}</div>
                </div>
                <span class="badge ${statusClass(r.status)}">${safe(r.status)}</span>
            </div>
            ${canReview ? `<div class="review-actions">
                <textarea data-admin-note="${r.id}" placeholder="Optional admin note"></textarea>
                <button class="primary-btn success" data-review="${r.id}" data-decision="Approve">Approve</button>
                <button class="primary-btn danger" data-review="${r.id}" data-decision="Decline">Decline</button>
            </div>` : ''}
        </article>`;
    }

    async function loadEmployees() {
        $('employeesList').innerHTML = 'Loading employees...';
        try {
            const data = await api('/api/staff/admin/employees');
            $('employeesList').classList.remove('loading-card');
            $('employeesList').innerHTML = data.employees.length ? data.employees.map(employeeRow).join('') : '<div class="empty-state">No employees found.</div>';
        } catch (err) {
            $('employeesList').innerHTML = `<div class="empty-state">${safe(err.message)}</div>`;
        }
    }

    async function saveAccount(employeeId) {
        const username = document.querySelector(`[data-username="${employeeId}"]`).value.trim();
        const password = document.querySelector(`[data-password="${employeeId}"]`).value.trim();
        try {
            const data = await api('/api/staff/admin/accounts/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ employee_id: employeeId, username, password, enabled: true })
            });
            showToast(data.message || 'Staff account saved.', 'success');
            await loadEmployees();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    async function loadLeaveRequests() {
        $('leaveRequestsList').innerHTML = 'Loading leave requests...';
        const status = $('leaveStatusFilter').value;
        try {
            const data = await api(`/api/staff/admin/leave_requests?status=${encodeURIComponent(status)}`);
            $('leaveRequestsList').classList.remove('loading-card');
            $('leaveRequestsList').innerHTML = data.leave_requests.length ? data.leave_requests.map(requestRow).join('') : '<div class="empty-state">No leave requests found.</div>';
        } catch (err) {
            $('leaveRequestsList').innerHTML = `<div class="empty-state">${safe(err.message)}</div>`;
        }
    }

    async function reviewLeave(requestId, decision) {
        const noteEl = document.querySelector(`[data-admin-note="${requestId}"]`);
        try {
            const data = await api(`/api/staff/admin/leave_requests/${requestId}/review`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision, admin_note: noteEl ? noteEl.value : '' })
            });
            showToast(data.message || 'Leave request updated.', 'success');
            await loadLeaveRequests();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }

    function bindEvents() {
        $('refreshEmployeesBtn').addEventListener('click', loadEmployees);
        $('refreshLeaveBtn').addEventListener('click', loadLeaveRequests);
        $('leaveStatusFilter').addEventListener('change', loadLeaveRequests);
        document.addEventListener('click', (event) => {
            const saveBtn = event.target.closest('[data-save-account]');
            const reviewBtn = event.target.closest('[data-review]');
            if (saveBtn) return saveAccount(saveBtn.dataset.saveAccount);
            if (reviewBtn) return reviewLeave(reviewBtn.dataset.review, reviewBtn.dataset.decision);
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        bindEvents();
        loadEmployees();
        loadLeaveRequests();
    });
})();
