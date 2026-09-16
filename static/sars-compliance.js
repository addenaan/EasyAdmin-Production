(function () {
    'use strict';

    const state = {
        context: 'payroll',
        defaultType: 'EMP201',
        selectedId: null,
        settings: null,
        openToken: 0,
        initialised: false
    };

    const TYPE_CONTEXT = {
        EMP201: 'payroll',
        EMP501: 'payroll',
        VAT201: 'accounting'
    };

    const TYPE_HELP = {
        EMP201: 'EMP201 is prepared from saved payslip ledger values for the selected month. Its liability is before ETI, penalties and interest and must be reconciled before filing or payment.',
        EMP501: 'EMP501 is an interim or annual reconciliation review based on saved payslip ledger values. It is not an e@syFile import file.',
        VAT201: 'VAT201 working values are prepared from posted Accounting records for the selected period. SARS field classifications must still be reviewed.'
    };

    function byId(id) {
        return document.getElementById(id);
    }

    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function normaliseContext(value) {
        return String(value || '').toLowerCase() === 'accounting' ? 'accounting' : 'payroll';
    }

    function allowedTypes(context) {
        return context === 'accounting' ? ['VAT201'] : ['EMP201', 'EMP501'];
    }

    function normaliseType(value, context) {
        const candidate = String(value || '').toUpperCase();
        const allowed = allowedTypes(context);
        return allowed.includes(candidate) ? candidate : allowed[0];
    }

    function displayDate(value) {
        if (!value) return '';
        const text = String(value);
        const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(text);
        const parsed = new Date(dateOnly ? text + 'T00:00:00' : text.replace(' ', 'T'));
        if (Number.isNaN(parsed.getTime())) return text;
        return parsed.toLocaleDateString('en-ZA', { year: 'numeric', month: 'short', day: '2-digit' });
    }

    function displayDateTime(value) {
        if (!value) return '';
        const parsed = new Date(String(value).replace(' ', 'T'));
        if (Number.isNaN(parsed.getTime())) return String(value);
        return parsed.toLocaleString('en-ZA', {
            year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit'
        });
    }

    function formatNumber(value, decimals) {
        const number = Number(value || 0);
        if (!Number.isFinite(number)) return escapeHtml(value);
        return number.toLocaleString('en-ZA', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });
    }

    function formatMoney(value) {
        return 'R ' + formatNumber(value, 2);
    }

    function formatReturnValue(item) {
        const field = String((item && item.field) || '').toLowerCase();
        if (field.includes('count')) return formatNumber(item.value, 0);
        return formatMoney(item && item.value);
    }

    function statusMeta(status) {
        const clean = String(status || 'prepared').toLowerCase();
        if (clean === 'approved') return { label: 'Approved review', badge: 'bg-success' };
        if (clean === 'rejected') return { label: 'Rejected', badge: 'bg-danger' };
        return { label: 'Prepared', badge: 'bg-primary' };
    }

    function setMessage(kind, message) {
        const node = byId('sarsComplianceMessage');
        if (!node) return;
        if (!message) {
            node.className = 'd-none';
            node.textContent = '';
            return;
        }
        const className = kind === 'success' ? 'alert-success' : kind === 'warning' ? 'alert-warning' : 'alert-danger';
        node.className = 'alert ' + className + ' shadow-sm';
        node.textContent = message;
    }

    function setBusy(button, busy, busyText) {
        if (!button) return;
        if (busy) {
            button.dataset.sarsOriginalText = button.textContent;
            button.disabled = true;
            button.textContent = busyText || 'Working…';
        } else {
            button.disabled = false;
            button.textContent = button.dataset.sarsOriginalText || button.textContent;
            delete button.dataset.sarsOriginalText;
        }
    }

    async function apiFetch(url, options) {
        const response = await fetch(url, options || {});
        const contentType = String(response.headers.get('content-type') || '').toLowerCase();
        let payload;
        if (contentType.includes('application/json')) {
            payload = await response.json();
        } else {
            const text = await response.text();
            payload = { status: 'error', message: text || ('Request failed with status ' + response.status) };
        }
        if (!response.ok || (payload && payload.status === 'error')) {
            const error = new Error((payload && payload.message) || ('Request failed with status ' + response.status));
            error.status = response.status;
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function jsonOptions(body) {
        return {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(body || {})
        };
    }

    function initialiseDates() {
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        if (byId('sarsEmp201Month')) byId('sarsEmp201Month').value = year + '-' + month;
        if (byId('sarsEmp501TaxYear')) byId('sarsEmp501TaxYear').value = now.getMonth() >= 2 ? year + 1 : year;
        if (byId('sarsEmp501Cycle')) byId('sarsEmp501Cycle').value = [8, 9].includes(now.getMonth()) ? 'interim' : 'annual';
        if (byId('sarsVatStartDate')) byId('sarsVatStartDate').value = year + '-' + month + '-01';
        if (byId('sarsVatEndDate')) byId('sarsVatEndDate').value = year + '-' + month + '-' + day;
    }

    function configureTypeOptions() {
        const select = byId('sarsReturnType');
        if (!select) return;
        const allowed = allowedTypes(state.context);
        Array.from(select.options).forEach(function (option) {
            option.hidden = !allowed.includes(option.value);
            option.disabled = !allowed.includes(option.value);
        });
        select.value = normaliseType(state.defaultType, state.context);
        updatePeriodInputs();
    }

    function updatePeriodInputs() {
        const type = normaliseType(byId('sarsReturnType') && byId('sarsReturnType').value, state.context);
        if (byId('sarsEmp201PeriodGroup')) byId('sarsEmp201PeriodGroup').classList.toggle('d-none', type !== 'EMP201');
        if (byId('sarsEmp501PeriodGroup')) byId('sarsEmp501PeriodGroup').classList.toggle('d-none', type !== 'EMP501');
        if (byId('sarsEmp501CycleGroup')) byId('sarsEmp501CycleGroup').classList.toggle('d-none', type !== 'EMP501');
        if (byId('sarsVatStartGroup')) byId('sarsVatStartGroup').classList.toggle('d-none', type !== 'VAT201');
        if (byId('sarsVatEndGroup')) byId('sarsVatEndGroup').classList.toggle('d-none', type !== 'VAT201');
        if (byId('sarsPrepareHelp')) {
            const canPrepare = !state.settings || (state.context === 'accounting' ? state.settings.can_prepare_accounting : state.settings.can_prepare_payroll);
            byId('sarsPrepareHelp').textContent = TYPE_HELP[type] + (canPrepare ? '' : ' Your access is read-only; you can inspect stored reviews but cannot prepare a new revision.');
        }
    }

    function populateSettings(data) {
        state.settings = data;
        if (byId('sarsComplianceCompanyName')) byId('sarsComplianceCompanyName').textContent = (data.company_name || 'Easy Admin') + ' · Review workspace';
        if (byId('sarsSettingCompanyName')) byId('sarsSettingCompanyName').value = data.company_name || '';
        if (byId('sarsSettingCompanyRegistration')) byId('sarsSettingCompanyRegistration').value = data.company_registration_number || '';
        if (byId('sarsSettingVatNumber')) byId('sarsSettingVatNumber').value = data.company_vat_number || '';
        if (byId('sarsSettingPayeReference')) byId('sarsSettingPayeReference').value = data.sars_paye_reference || '';
        if (byId('sarsSettingUifReference')) byId('sarsSettingUifReference').value = data.sars_uif_reference || '';
        if (byId('sarsSettingSdlReference')) byId('sarsSettingSdlReference').value = data.sars_sdl_reference || '';
        if (byId('sarsSettingContactName')) byId('sarsSettingContactName').value = data.sars_contact_name || '';
        if (byId('sarsSettingContactCapacity')) byId('sarsSettingContactCapacity').value = data.sars_contact_capacity || '';
        if (byId('sarsSettingContactPhone')) byId('sarsSettingContactPhone').value = data.sars_contact_phone || '';
        if (byId('sarsSettingContactEmail')) byId('sarsSettingContactEmail').value = data.sars_contact_email || '';
        if (byId('sarsSettingVatCategory')) byId('sarsSettingVatCategory').value = data.sars_vat_filing_category || 'Two-monthly';
        if (byId('sarsSettingVatBasis')) byId('sarsSettingVatBasis').value = data.sars_vat_accounting_basis || 'Invoice';

        const saveButton = byId('sarsSaveSettingsButton');
        if (saveButton) {
            saveButton.classList.toggle('d-none', !data.can_configure);
            saveButton.disabled = !data.can_configure;
        }
        const fields = byId('sarsSettingsPane') ? byId('sarsSettingsPane').querySelectorAll('input:not([readonly]), select') : [];
        fields.forEach(function (field) { field.disabled = !data.can_configure; });

        const canPrepare = state.context === 'accounting' ? data.can_prepare_accounting : data.can_prepare_payroll;
        const prepareButton = byId('sarsPrepareButton');
        if (prepareButton) {
            prepareButton.disabled = !canPrepare;
            prepareButton.title = canPrepare ? 'Prepare a new stored review revision' : 'Full application access is required to prepare a new revision';
        }

        if (byId('sarsPayrollSettingsGroup')) byId('sarsPayrollSettingsGroup').classList.toggle('d-none', state.context !== 'payroll');
        if (byId('sarsVatSettingsGroup')) byId('sarsVatSettingsGroup').classList.toggle('d-none', state.context !== 'accounting');
        updatePeriodInputs();
    }

    function settingsPayload() {
        return {
            company_vat_number: byId('sarsSettingVatNumber').value.trim(),
            sars_paye_reference: byId('sarsSettingPayeReference').value.trim(),
            sars_uif_reference: byId('sarsSettingUifReference').value.trim(),
            sars_sdl_reference: byId('sarsSettingSdlReference').value.trim(),
            sars_contact_name: byId('sarsSettingContactName').value.trim(),
            sars_contact_capacity: byId('sarsSettingContactCapacity').value.trim(),
            sars_contact_phone: byId('sarsSettingContactPhone').value.trim(),
            sars_contact_email: byId('sarsSettingContactEmail').value.trim(),
            sars_vat_filing_category: byId('sarsSettingVatCategory').value,
            sars_vat_accounting_basis: byId('sarsSettingVatBasis').value
        };
    }

    async function loadSettings(openToken) {
        const data = await apiFetch('/api/sars/settings', { headers: { 'Accept': 'application/json' } });
        if (openToken !== undefined && openToken !== state.openToken) return;
        populateSettings(data);
    }

    async function saveSettings() {
        const button = byId('sarsSaveSettingsButton');
        setBusy(button, true, 'Saving…');
        setMessage('', '');
        try {
            const data = await apiFetch('/api/sars/settings', jsonOptions(settingsPayload()));
            populateSettings(data);
            setMessage('success', 'Employer settings saved. Existing stored revisions were not changed.');
        } catch (error) {
            setMessage('error', error.message || 'Could not save employer settings.');
        } finally {
            setBusy(button, false);
        }
    }

    function renderReturnList(rows) {
        const list = byId('sarsReturnList');
        if (!list) return;
        if (!Array.isArray(rows) || !rows.length) {
            list.innerHTML = '<div class="text-center text-muted py-4 px-3">No stored return reviews are available for this application.</div>';
            return;
        }
        list.innerHTML = rows.map(function (row) {
            const meta = statusMeta(row.batch_status || row.status);
            const active = Number(state.selectedId) === Number(row.id) ? ' active' : '';
            return '<button type="button" class="list-group-item list-group-item-action' + active + '" data-sars-return-id="' + Number(row.id) + '">' +
                '<div class="d-flex justify-content-between align-items-start gap-2">' +
                    '<div><strong>' + escapeHtml(row.return_type) + '</strong> <span class="text-muted">v' + escapeHtml(row.version_no) + '</span>' +
                    '<div class="small">' + escapeHtml(row.period_label) + '</div></div>' +
                    '<span class="badge ' + meta.badge + '">' + escapeHtml(meta.label) + '</span>' +
                '</div>' +
                '<div class="small mt-2">' +
                    '<span class="text-danger">' + Number(row.validation_error_count || 0) + ' error(s)</span> · ' +
                    '<span class="text-warning-emphasis">' + Number(row.validation_warning_count || 0) + ' warning(s)</span>' +
                '</div>' +
                '<div class="small text-muted mt-1">Prepared ' + escapeHtml(displayDateTime(row.prepared_at)) + '</div>' +
            '</button>';
        }).join('');
        list.querySelectorAll('[data-sars-return-id]').forEach(function (button) {
            button.addEventListener('click', function () {
                loadReturnDetail(Number(button.dataset.sarsReturnId));
            });
        });
    }

    async function loadReturns(openToken) {
        const list = byId('sarsReturnList');
        if (list) list.innerHTML = '<div class="text-center text-muted py-4">Loading prepared reviews…</div>';
        try {
            const data = await apiFetch('/api/sars/returns?context=' + encodeURIComponent(state.context), { headers: { 'Accept': 'application/json' } });
            if (openToken !== undefined && openToken !== state.openToken) return;
            renderReturnList(data.returns || []);
        } catch (error) {
            if (openToken !== undefined && openToken !== state.openToken) return;
            if (list) list.innerHTML = '<div class="alert alert-danger m-3">' + escapeHtml(error.message || 'Could not load prepared reviews.') + '</div>';
            setMessage('error', error.message || 'Could not load prepared reviews.');
        }
    }

    function renderValidation(issues) {
        if (!Array.isArray(issues) || !issues.length) {
            return '<div class="alert alert-success"><strong>No validation findings.</strong> The stored values are ready for independent review.</div>';
        }
        const errors = issues.filter(function (item) { return String(item.severity || '').toLowerCase() === 'error'; });
        const warnings = issues.filter(function (item) { return String(item.severity || '').toLowerCase() !== 'error'; });
        function section(title, items, alertClass, badgeClass) {
            if (!items.length) return '';
            return '<div class="alert ' + alertClass + '"><h6 class="fw-bold">' + escapeHtml(title) + '</h6><ul class="mb-0 ps-3">' + items.map(function (item) {
                const entity = item.entity ? '<strong>' + escapeHtml(item.entity) + ':</strong> ' : '';
                return '<li class="mb-1">' + entity + escapeHtml(item.message || '') + ' <span class="badge ' + badgeClass + '">' + escapeHtml(item.code || 'REVIEW') + '</span></li>';
            }).join('') + '</ul></div>';
        }
        return section('Validation errors — resolve and prepare a new revision', errors, 'alert-danger', 'bg-danger') +
            section('Review warnings', warnings, 'alert-warning', 'bg-warning text-dark');
    }

    function renderReturnValues(snapshot) {
        const values = Array.isArray(snapshot && snapshot.return_values) ? snapshot.return_values : [];
        if (!values.length) return '<div class="text-muted small">No return values were stored.</div>';
        return '<div class="table-responsive"><table class="table table-sm table-hover align-middle mb-0">' +
            '<thead class="table-light"><tr><th>Working Field</th><th>Basis</th><th class="text-end">Value</th></tr></thead><tbody>' +
            values.map(function (item) {
                return '<tr><td><strong>' + escapeHtml(item.field || '') + '</strong></td><td>' + escapeHtml(item.label || '') + '</td><td class="text-end text-nowrap">' + formatReturnValue(item) + '</td></tr>';
            }).join('') + '</tbody></table></div>';
    }

    function detailColumnDefinitions(type) {
        if (type === 'EMP201') {
            return [
                ['employee_name', 'Employee'], ['payroll_date', 'Payroll Date'], ['taxable_remuneration', 'Taxable Remuneration'],
                ['paye', 'PAYE'], ['uif_employee', 'UIF Employee'], ['uif_employer', 'UIF Employer'], ['sdl_employer', 'SDL']
            ];
        }
        if (type === 'EMP501') {
            return [
                ['employee_number', 'Employee No.'], ['employee_name', 'Employee'], ['tax_number', 'Tax Number'],
                ['code_3699', 'Code 3699'], ['transport_unclassified', 'Transport (unclassified)'], ['code_4102', 'Code 4102'], ['code_4141', 'Code 4141'], ['code_4142', 'Code 4142'], ['code_4149', 'Code 4149']
            ];
        }
        return [
            ['date', 'Date'], ['reference', 'Reference'], ['source', 'Source'], ['vat_type', 'VAT Type'], ['description', 'Description'],
            ['gross_amount', 'Gross'], ['net_amount', 'Net'], ['vat_amount', 'VAT']
        ];
    }

    function isMoneyColumn(key) {
        return /^(code_\d+|transport_unclassified|taxable_remuneration|paye|uif_|sdl_|gross_amount|net_amount|vat_amount|derived_liability)/.test(key);
    }

    function renderDetailRows(snapshot, type) {
        const rows = Array.isArray(snapshot && snapshot.detail_rows) ? snapshot.detail_rows : [];
        if (!rows.length) return '<div class="text-muted small">No source detail rows were stored.</div>';
        const columns = detailColumnDefinitions(type);
        return '<div class="table-responsive" style="max-height: 360px; overflow: auto;"><table class="table table-sm table-striped table-hover align-middle mb-0">' +
            '<thead class="table-light position-sticky top-0"><tr>' + columns.map(function (column) {
                return '<th class="' + (isMoneyColumn(column[0]) ? 'text-end' : '') + '">' + escapeHtml(column[1]) + '</th>';
            }).join('') + '</tr></thead><tbody>' + rows.map(function (row) {
                return '<tr>' + columns.map(function (column) {
                    const key = column[0];
                    const value = row[key];
                    return '<td class="' + (isMoneyColumn(key) ? 'text-end text-nowrap' : '') + '">' + (isMoneyColumn(key) ? formatMoney(value) : escapeHtml(value)) + '</td>';
                }).join('') + '</tr>';
            }).join('') + '</tbody></table></div>';
    }

    function renderMonthlyReconciliation(snapshot) {
        const rows = Array.isArray(snapshot && snapshot.monthly_reconciliation) ? snapshot.monthly_reconciliation : [];
        if (!rows.length) return '';
        return '<div class="mt-4"><h6 class="fw-bold">Monthly ledger reconciliation</h6><div class="table-responsive"><table class="table table-sm table-hover">' +
            '<thead class="table-light"><tr><th>Month</th><th class="text-end">PAYE</th><th class="text-end">UIF</th><th class="text-end">SDL</th><th class="text-end">Derived Liability</th></tr></thead><tbody>' +
            rows.map(function (row) {
                return '<tr><td>' + escapeHtml(row.month) + '</td><td class="text-end">' + formatMoney(row.paye) + '</td><td class="text-end">' + formatMoney(row.uif_total) + '</td><td class="text-end">' + formatMoney(row.sdl) + '</td><td class="text-end fw-bold">' + formatMoney(row.derived_liability) + '</td></tr>';
            }).join('') + '</tbody></table></div></div>';
    }

    function renderApprovalControls(data, batchStatus) {
        if (batchStatus === 'approved') {
            return '<div class="alert alert-success"><strong>Independently approved by ' + escapeHtml(data.approved_by || 'authorised reviewer') + '</strong>' +
                (data.approved_at ? ' on ' + escapeHtml(displayDateTime(data.approved_at)) : '') + '.' +
                (data.approval_note ? '<div class="mt-2"><strong>Approval note:</strong> ' + escapeHtml(data.approval_note) + '</div>' : '') +
                '<div class="mt-2">This status means approved inside Easy Admin only. It is still <strong>not submitted to SARS</strong>.</div></div>' +
                (data.can_download ? '<a class="btn btn-success fw-bold" href="/download/sars/returns/' + Number(data.id) + '.zip">Download Approved Review Pack</a>' : '');
        }
        if (batchStatus === 'rejected') {
            return '<div class="alert alert-danger"><strong>Rejected by ' + escapeHtml(data.rejected_by || 'reviewer') + '</strong>' +
                (data.rejected_at ? ' on ' + escapeHtml(displayDateTime(data.rejected_at)) : '') + '.' +
                '<div class="mt-2"><strong>Reason:</strong> ' + escapeHtml(data.rejection_reason || 'No reason recorded.') + '</div>' +
                '<div class="mt-2">Correct the source data and prepare a new revision.</div></div>';
        }

        let html = '<div class="alert alert-info small"><strong>Independent review required.</strong> The preparer cannot approve their own revision. All validation errors must be resolved first.</div>';
        if (data.can_approve) {
            html += '<div class="border rounded p-3 mb-3 bg-white">' +
                '<div class="form-check mb-3"><input class="form-check-input" type="checkbox" id="sarsApprovalDeclaration"><label class="form-check-label" for="sarsApprovalDeclaration">' + escapeHtml(data.approval_declaration || 'I confirm that I reviewed this return revision. Approval does not submit it to SARS.') + '</label></div>' +
                '<label class="form-label small fw-bold" for="sarsApprovalNote">Approval Note (optional)</label>' +
                '<textarea class="form-control mb-3" id="sarsApprovalNote" rows="2" maxlength="1000" placeholder="Record the checks performed or matters considered."></textarea>' +
                '<button class="btn btn-success fw-bold" type="button" id="sarsApproveButton">Approve Review Pack</button>' +
            '</div>';
        }
        if (data.can_reject) {
            html += '<div class="border rounded p-3 bg-white">' +
                '<label class="form-label small fw-bold" for="sarsRejectionReason">Rejection Reason</label>' +
                '<textarea class="form-control mb-3" id="sarsRejectionReason" rows="2" maxlength="1000" placeholder="State what must be corrected before a new revision is prepared."></textarea>' +
                '<button class="btn btn-outline-danger fw-bold" type="button" id="sarsRejectButton">Reject Review</button>' +
            '</div>';
        }
        return html;
    }

    function renderReturnDetail(data) {
        const detail = byId('sarsReturnDetail');
        if (!detail) return;
        state.selectedId = Number(data.id);
        const batchStatus = data.batch_status || data.status_code || (data.status !== 'success' ? data.status : 'prepared');
        const meta = statusMeta(batchStatus);
        const snapshot = data.snapshot || {};
        const errors = Number(data.validation_error_count || 0);
        const warnings = Number(data.validation_warning_count || 0);
        const submission = String(data.submission_status || 'not_submitted').replace(/_/g, ' ');
        const integrityNotice = data.snapshot_integrity_ok === false || data.validation_integrity_ok === false
            ? '<div class="alert alert-danger"><strong>Integrity check failed.</strong> This stored snapshot cannot be approved or downloaded. Reject it and prepare a new revision.</div>'
            : '';
        detail.innerHTML =
            '<div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-3">' +
                '<div><h5 class="fw-bold mb-1">' + escapeHtml(data.return_type) + ' · ' + escapeHtml(data.period_label) + '</h5>' +
                '<div class="text-muted small">Revision ' + escapeHtml(data.version_no) + ' · Prepared by ' + escapeHtml(data.prepared_by || 'unknown') + ' on ' + escapeHtml(displayDateTime(data.prepared_at)) + '</div></div>' +
                '<div class="text-end"><span class="badge ' + meta.badge + '">' + escapeHtml(meta.label) + '</span><div class="small text-danger fw-bold mt-1">SARS: ' + escapeHtml(submission) + '</div></div>' +
            '</div>' +
            '<div class="row g-2 mb-3">' +
                '<div class="col-sm-4"><div class="border rounded p-2 bg-light"><strong>Validation errors</strong><div class="fs-5 text-danger">' + errors + '</div></div></div>' +
                '<div class="col-sm-4"><div class="border rounded p-2 bg-light"><strong>Warnings</strong><div class="fs-5 text-warning-emphasis">' + warnings + '</div></div></div>' +
                '<div class="col-sm-4"><div class="border rounded p-2 bg-light"><strong>Source</strong><div class="small">' + escapeHtml(snapshot.source || 'Stored source snapshot') + '</div></div></div>' +
            '</div>' +
            integrityNotice + '<h6 class="fw-bold">Validation findings</h6>' + renderValidation(data.validation || []) +
            '<h6 class="fw-bold mt-4">Return working values</h6>' + renderReturnValues(snapshot) +
            renderMonthlyReconciliation(snapshot) +
            '<div class="mt-4"><h6 class="fw-bold">Stored source detail</h6>' + renderDetailRows(snapshot, data.return_type) + '</div>' +
            '<div class="small text-muted mt-3 text-break"><strong>Snapshot hash:</strong> <code>' + escapeHtml(data.source_hash || '') + '</code></div>' +
            '<div class="mt-4"><h6 class="fw-bold">Review decision</h6>' + renderApprovalControls(data, batchStatus) + '</div>' +
            '<div class="alert alert-warning small mt-3 mb-0"><strong>Not submitted:</strong> This Easy Admin record and any downloaded ZIP are review materials only. Submission and payment must be completed separately through an authorised SARS channel.</div>';

        const approveButton = byId('sarsApproveButton');
        if (approveButton) approveButton.addEventListener('click', approveSelectedReturn);
        const rejectButton = byId('sarsRejectButton');
        if (rejectButton) rejectButton.addEventListener('click', rejectSelectedReturn);
        loadReturns().catch(function () {});
    }

    async function loadReturnDetail(id) {
        const detail = byId('sarsReturnDetail');
        if (detail) detail.innerHTML = '<div class="text-center text-muted py-5">Loading stored revision…</div>';
        try {
            const data = await apiFetch('/api/sars/returns/' + encodeURIComponent(id), { headers: { 'Accept': 'application/json' } });
            renderReturnDetail(data);
        } catch (error) {
            if (detail) detail.innerHTML = '<div class="alert alert-danger">' + escapeHtml(error.message || 'Could not load the return review.') + '</div>';
        }
    }

    function preparationPayload() {
        const type = normaliseType(byId('sarsReturnType').value, state.context);
        const payload = { return_type: type };
        if (type === 'EMP201') payload.month = byId('sarsEmp201Month').value;
        if (type === 'EMP501') {
            payload.tax_year = Number(byId('sarsEmp501TaxYear').value);
            payload.reconciliation_period = byId('sarsEmp501Cycle').value;
        }
        if (type === 'VAT201') {
            payload.start_date = byId('sarsVatStartDate').value;
            payload.end_date = byId('sarsVatEndDate').value;
        }
        return payload;
    }

    function validatePreparation(payload) {
        if (payload.return_type === 'EMP201' && !/^\d{4}-\d{2}$/.test(payload.month || '')) return 'Select a valid payroll month.';
        if (payload.return_type === 'EMP501' && (!Number.isInteger(payload.tax_year) || payload.tax_year < 2000 || payload.tax_year > 2200)) return 'Enter a valid four-digit tax year.';
        if (payload.return_type === 'EMP501' && !['interim', 'annual'].includes(payload.reconciliation_period)) return 'Select a valid EMP501 reconciliation period.';
        if (payload.return_type === 'VAT201') {
            if (!payload.start_date || !payload.end_date) return 'Select the VAT period start and end dates.';
            if (payload.start_date > payload.end_date) return 'VAT period start cannot be after the end date.';
        }
        return '';
    }

    async function prepareReturn() {
        const button = byId('sarsPrepareButton');
        const payload = preparationPayload();
        const validationMessage = validatePreparation(payload);
        if (validationMessage) {
            setMessage('error', validationMessage);
            return;
        }
        setBusy(button, true, 'Preparing…');
        setMessage('', '');
        try {
            const data = await apiFetch('/api/sars/returns/prepare', jsonOptions(payload));
            renderReturnDetail(data);
            setMessage(Number(data.validation_error_count || 0) ? 'warning' : 'success', data.message || 'Return review prepared.');
        } catch (error) {
            setMessage('error', error.message || 'Could not prepare the return review.');
        } finally {
            setBusy(button, false);
        }
    }

    async function approveSelectedReturn() {
        if (!state.selectedId) return;
        const declaration = byId('sarsApprovalDeclaration');
        if (!declaration || !declaration.checked) {
            setMessage('error', 'Accept the approval declaration before approving this review pack.');
            return;
        }
        const button = byId('sarsApproveButton');
        setBusy(button, true, 'Approving…');
        setMessage('', '');
        try {
            const data = await apiFetch('/api/sars/returns/' + encodeURIComponent(state.selectedId) + '/approve', jsonOptions({
                declaration_accepted: true,
                approval_note: (byId('sarsApprovalNote') && byId('sarsApprovalNote').value.trim()) || ''
            }));
            renderReturnDetail(data);
            setMessage('success', data.message || 'Review pack approved. It has not been submitted to SARS.');
        } catch (error) {
            setMessage('error', error.message || 'Could not approve the review pack.');
            setBusy(button, false);
        }
    }

    async function rejectSelectedReturn() {
        if (!state.selectedId) return;
        const reason = (byId('sarsRejectionReason') && byId('sarsRejectionReason').value.trim()) || '';
        if (!reason) {
            setMessage('error', 'Enter a rejection reason.');
            return;
        }
        const button = byId('sarsRejectButton');
        setBusy(button, true, 'Rejecting…');
        setMessage('', '');
        try {
            const data = await apiFetch('/api/sars/returns/' + encodeURIComponent(state.selectedId) + '/reject', jsonOptions({ reason: reason }));
            renderReturnDetail(data);
            setMessage('success', data.message || 'Review rejected. Prepare a new revision after correcting the source data.');
        } catch (error) {
            setMessage('error', error.message || 'Could not reject the review.');
            setBusy(button, false);
        }
    }

    function initialise() {
        if (state.initialised || !byId('easyAdminSarsComplianceModal')) return;
        state.initialised = true;
        initialiseDates();
        byId('sarsReturnType').addEventListener('change', updatePeriodInputs);
        byId('sarsPrepareButton').addEventListener('click', prepareReturn);
        byId('sarsRefreshButton').addEventListener('click', function () {
            setMessage('', '');
            loadReturns();
        });
        byId('sarsSaveSettingsButton').addEventListener('click', saveSettings);
    }

    window.openSarsCompliance = function (context, defaultType) {
        initialise();
        const modalElement = byId('easyAdminSarsComplianceModal');
        if (!modalElement) {
            window.alert('The SARS Compliance workspace is not available on this page.');
            return;
        }
        state.context = normaliseContext(context);
        state.defaultType = normaliseType(defaultType, state.context);
        state.selectedId = null;
        state.openToken += 1;
        const token = state.openToken;
        setMessage('', '');
        if (byId('sarsComplianceContextBadge')) byId('sarsComplianceContextBadge').textContent = state.context === 'accounting' ? 'Accounting' : 'Payroll';
        if (byId('sarsReturnDetail')) byId('sarsReturnDetail').innerHTML = '<div class="text-center text-muted py-5">Prepare a new review or select a stored revision to inspect its totals and validation findings.</div>';
        configureTypeOptions();
        if (byId('sarsPayrollSettingsGroup')) byId('sarsPayrollSettingsGroup').classList.toggle('d-none', state.context !== 'payroll');
        if (byId('sarsVatSettingsGroup')) byId('sarsVatSettingsGroup').classList.toggle('d-none', state.context !== 'accounting');

        if (!window.bootstrap || !window.bootstrap.Modal) {
            window.alert('The SARS Compliance workspace could not open because Bootstrap is unavailable. Refresh the page and try again.');
            return;
        }
        window.bootstrap.Modal.getOrCreateInstance(modalElement).show();
        Promise.allSettled([loadSettings(token), loadReturns(token)]).then(function (results) {
            const rejected = results.find(function (result) { return result.status === 'rejected'; });
            if (rejected && rejected.reason) setMessage('error', rejected.reason.message || 'Could not load the SARS Compliance workspace.');
        });
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialise, { once: true });
    } else {
        initialise();
    }
})();
