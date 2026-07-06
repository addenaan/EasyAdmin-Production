(function () {
    const $ = (id) => document.getElementById(id);
    const state = { employees: [], notificationRecipientSearch: '', selectedNotificationEmployeeIds: new Set() };
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


    function notificationHistoryRow(n) {
        return `<article class="request-row">
            <div class="item-top">
                <div>
                    <div class="row-title">${safe(n.title || 'Notification')} ${n.employee_name ? '— ' + safe(n.employee_name) : ''}</div>
                    <div class="row-meta">${safe(n.message || '')}<br>${safe(fmtDate(n.created_at || ''))} • ${safe(n.notification_type || 'message')} • ${safe(n.sent_status || 'pending')}${n.error_message ? '<br>Error: ' + safe(n.error_message) : ''}</div>
                </div>
                <span class="badge ${n.sent_status === 'sent' ? 'success' : (n.sent_status === 'failed' ? 'danger' : 'dark')}">${safe(n.sent_status || 'pending')}</span>
            </div>
        </article>`;
    }

    function activeStaffEmployees() {
        return state.employees.filter((e) => e.staff_user_id || e.staff_enabled);
    }

    function normalizeSearch(value) {
        return String(value || '').toLowerCase().trim();
    }

    function notificationRecipientSearchText(e) {
        return [
            e.name,
            e.job_title,
            e.emp_number,
            e.staff_username,
            e.email,
            e.status
        ].filter(Boolean).join(' ').toLowerCase();
    }

    function filteredNotificationEmployees() {
        const staff = activeStaffEmployees();
        const query = normalizeSearch(state.notificationRecipientSearch);
        if (!query) return staff;
        return staff.filter((e) => notificationRecipientSearchText(e).includes(query));
    }

    function syncSelectedNotificationEmployeeIds() {
        const activeIds = new Set(activeStaffEmployees().map((e) => String(e.id)));
        state.selectedNotificationEmployeeIds = new Set(
            Array.from(state.selectedNotificationEmployeeIds).filter((id) => activeIds.has(String(id)))
        );
    }

    function getSelectedNotificationEmployeeIds() {
        syncSelectedNotificationEmployeeIds();
        return Array.from(state.selectedNotificationEmployeeIds)
            .map((value) => parseInt(value, 10))
            .filter((value) => Number.isFinite(value) && value > 0);
    }

    function updateNotificationRecipientCount() {
        const countEl = $('notificationRecipientCount');
        const selectAll = $('selectAllNotificationStaff');
        const staff = activeStaffEmployees();
        const visibleStaff = filteredNotificationEmployees();
        const selectedIds = getSelectedNotificationEmployeeIds();
        const selectedSet = new Set(selectedIds.map(String));
        const visibleSelectedCount = visibleStaff.filter((e) => selectedSet.has(String(e.id))).length;
        const query = normalizeSearch(state.notificationRecipientSearch);
        if (countEl) {
            if (!staff.length) countEl.textContent = 'Create staff portal accounts first.';
            else if (query) {
                const selectedText = selectedIds.length ? `${selectedIds.length} selected. ` : '';
                countEl.textContent = `${selectedText}Showing ${visibleStaff.length} of ${staff.length} staff member(s).`;
            } else {
                countEl.textContent = selectedIds.length
                    ? `${selectedIds.length} staff member(s) selected.`
                    : 'Select one or more staff members below.';
            }
        }
        if (selectAll) {
            selectAll.disabled = visibleStaff.length === 0;
            selectAll.checked = visibleStaff.length > 0 && visibleSelectedCount === visibleStaff.length;
            selectAll.indeterminate = visibleSelectedCount > 0 && visibleSelectedCount < visibleStaff.length;
            const labelText = $('selectAllNotificationStaffText');
            if (labelText) labelText.textContent = query ? 'Select visible' : 'Select all';
        }
    }

    function renderNotificationRecipients() {
        const list = $('notificationEmployeeChecklist');
        if (!list) return;
        syncSelectedNotificationEmployeeIds();
        const staff = activeStaffEmployees();
        const visibleStaff = filteredNotificationEmployees();
        list.classList.remove('loading-card');
        if (!staff.length) {
            list.innerHTML = '<div class="empty-state compact">No active staff portal accounts found. Create staff portal accounts first.</div>';
            updateNotificationRecipientCount();
            return;
        }
        if (!visibleStaff.length) {
            list.innerHTML = '<div class="empty-state compact">No staff recipients match your search.</div>';
            updateNotificationRecipientCount();
            return;
        }
        list.innerHTML = visibleStaff.map((e) => {
            const id = String(e.id);
            const checked = state.selectedNotificationEmployeeIds.has(id) ? ' checked' : '';
            const meta = [e.job_title, e.emp_number ? 'Employee No: ' + e.emp_number : '', e.staff_username ? 'Username: ' + e.staff_username : ''].filter(Boolean).join(' • ');
            return `<label class="recipient-checkbox-row">
                <input type="checkbox" data-notification-employee value="${safe(id)}"${checked}>
                <span>
                    <strong>${safe(e.name || 'Employee')}</strong>
                    ${meta ? `<small>${safe(meta)}</small>` : ''}
                </span>
            </label>`;
        }).join('');
        updateNotificationRecipientCount();
    }

    async function loadEmployees() {
        $('employeesList').innerHTML = 'Loading employees...';
        try {
            const data = await api('/api/staff/admin/employees');
            state.employees = data.employees || [];
            renderNotificationRecipients();
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


    async function loadNotificationHistory() {
        const list = $('notificationsHistoryList');
        if (!list) return;
        list.innerHTML = 'Loading notifications...';
        try {
            const data = await api('/api/staff/admin/notifications/history');
            const items = data.notifications || [];
            list.classList.remove('loading-card');
            list.innerHTML = items.length ? items.map(notificationHistoryRow).join('') : '<div class="empty-state">No staff notifications have been sent yet.</div>';
        } catch (err) {
            list.innerHTML = `<div class="empty-state">${safe(err.message)}</div>`;
        }
    }

    async function sendStaffNotification() {
        const employeeIds = getSelectedNotificationEmployeeIds();
        const title = $('notificationTitle').value.trim();
        const message = $('notificationMessage').value.trim();
        if (!employeeIds.length) {
            showToast('Please tick at least one staff member to receive this notification.', 'error');
            return;
        }
        try {
            const data = await api('/api/staff/admin/notifications/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ employee_ids: employeeIds, title, message })
            });
            showToast(data.message || 'Notification sent.', 'success');
            $('notificationTitle').value = '';
            $('notificationMessage').value = '';
            state.selectedNotificationEmployeeIds.clear();
            document.querySelectorAll('[data-notification-employee]').forEach((input) => { input.checked = false; });
            updateNotificationRecipientCount();
            await loadNotificationHistory();
        } catch (err) {
            showToast(err.message, 'error');
        }
    }


    function setActiveStaffAdminTab(tabName, persist) {
        const selected = tabName || 'accounts';
        document.querySelectorAll('[data-staff-admin-tab]').forEach((btn) => {
            const isActive = btn.dataset.staffAdminTab === selected;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        document.querySelectorAll('[data-staff-admin-panel]').forEach((panel) => {
            const isActive = panel.dataset.staffAdminPanel === selected;
            panel.classList.toggle('active', isActive);
        });
        if (persist !== false) {
            try { localStorage.setItem('easyadmin_staff_admin_active_tab', selected); } catch (err) {}
        }
    }

    function resolveInitialStaffAdminTab() {
        const hash = String(window.location.hash || '').replace('#', '').toLowerCase();
        if (['accounts', 'leave', 'notifications'].includes(hash)) return hash;
        try {
            const stored = localStorage.getItem('easyadmin_staff_admin_active_tab');
            if (['accounts', 'leave', 'notifications'].includes(stored)) return stored;
        } catch (err) {}
        return 'accounts';
    }

    function bindEvents() {
        document.querySelectorAll('[data-staff-admin-tab]').forEach((btn) => {
            btn.addEventListener('click', () => {
                setActiveStaffAdminTab(btn.dataset.staffAdminTab);
                if (history.replaceState) history.replaceState(null, '', '#' + btn.dataset.staffAdminTab);
                else window.location.hash = btn.dataset.staffAdminTab;
            });
        });
        $('refreshEmployeesBtn').addEventListener('click', loadEmployees);
        $('refreshLeaveBtn').addEventListener('click', loadLeaveRequests);
        if ($('sendNotificationBtn')) $('sendNotificationBtn').addEventListener('click', sendStaffNotification);
        if ($('refreshNotificationsHistoryBtn')) $('refreshNotificationsHistoryBtn').addEventListener('click', loadNotificationHistory);
        if ($('selectAllNotificationStaff')) {
            $('selectAllNotificationStaff').addEventListener('change', (event) => {
                const shouldCheck = event.target.checked;
                filteredNotificationEmployees().forEach((e) => {
                    const id = String(e.id);
                    if (shouldCheck) state.selectedNotificationEmployeeIds.add(id);
                    else state.selectedNotificationEmployeeIds.delete(id);
                });
                document.querySelectorAll('[data-notification-employee]').forEach((input) => { input.checked = shouldCheck; });
                updateNotificationRecipientCount();
            });
        }
        const recipientSearch = $('notificationRecipientSearch');
        if (recipientSearch) {
            recipientSearch.addEventListener('input', (event) => {
                state.notificationRecipientSearch = event.target.value || '';
                renderNotificationRecipients();
            });
        }
        const clearRecipientSearch = $('clearNotificationRecipientSearch');
        if (clearRecipientSearch) {
            clearRecipientSearch.addEventListener('click', () => {
                state.notificationRecipientSearch = '';
                if (recipientSearch) recipientSearch.value = '';
                renderNotificationRecipients();
            });
        }
        const recipientList = $('notificationEmployeeChecklist');
        if (recipientList) {
            recipientList.addEventListener('change', (event) => {
                const input = event.target.closest('[data-notification-employee]');
                if (!input) return;
                if (input.checked) state.selectedNotificationEmployeeIds.add(String(input.value));
                else state.selectedNotificationEmployeeIds.delete(String(input.value));
                updateNotificationRecipientCount();
            });
        }
        $('leaveStatusFilter').addEventListener('change', loadLeaveRequests);
        document.addEventListener('click', (event) => {
            const saveBtn = event.target.closest('[data-save-account]');
            const reviewBtn = event.target.closest('[data-review]');
            if (saveBtn) return saveAccount(saveBtn.dataset.saveAccount);
            if (reviewBtn) return reviewLeave(reviewBtn.dataset.review, reviewBtn.dataset.decision);
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        setActiveStaffAdminTab(resolveInitialStaffAdminTab(), false);
        bindEvents();
        loadEmployees();
        loadLeaveRequests();
        loadNotificationHistory();
    });
})();
