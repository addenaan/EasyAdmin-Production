(function () {
  if (window.__easyAdminLiveRefreshInstalled) return;
  window.__easyAdminLiveRefreshInstalled = true;

  const MUTATION_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
  let refreshTimer = null;

  function callIfAvailable(name) {
    try {
      const fn = window[name];
      if (typeof fn === 'function') fn();
    } catch (err) {
      console.warn('Easy Admin refresh function failed:', name, err);
    }
  }

  function refreshFullCalendar() {
    try {
      if (window.calendar && typeof window.calendar.refetchEvents === 'function') {
        window.calendar.refetchEvents();
      }
    } catch (err) {
      console.warn('Easy Admin calendar refresh failed:', err);
    }
  }

  function refreshKnownPageSections(reason) {
    refreshFullCalendar();
    [
      'loadDashboardLists',
      'refreshWizardStaffAvailability',
      'refreshProjectsData',
      'loadProjectDirectory',
      'loadBookingOpsReports',
      'loadFinanceData',
      'loadExpenses',
      'loadInvoices',
      'loadQuotes',
      'loadInvoiceSalesReport',
      'refreshInvoiceList',
      'refreshCurrentMonthPayrollStatuses',
      'loadPayrollLedger',
      'loadStaffAccounts',
      'loadStaffAdminData',
      'loadStaffPortalData',
      'loadStaffDashboard',
      'loadStaffPayslips',
      'loadAccountingEntries',
      'loadChartOfAccounts',
      'loadGeneralLedger',
      'loadCashbookBatches',
      'loadAccountingReports',
      'loadCompanyList',
      'loadUsers',
      'loadAuditTrail'
    ].forEach(callIfAvailable);

    try {
      window.dispatchEvent(new CustomEvent('easyadmin:data-changed', { detail: { reason: reason || 'data-change' } }));
    } catch (err) {
      // Older browsers can ignore custom event refresh notifications.
    }
  }

  function markChanged(reason) {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(function () {
      refreshKnownPageSections(reason);
    }, 350);
  }

  function isMutationRequest(input, init) {
    let method = (init && init.method) || '';
    if (!method && input && typeof input === 'object' && input.method) method = input.method;
    method = String(method || 'GET').toUpperCase();
    return MUTATION_METHODS.has(method);
  }

  function withNoCache(init) {
    const next = Object.assign({}, init || {});
    next.cache = 'no-store';
    try {
      const headers = new Headers(next.headers || {});
      headers.set('Cache-Control', 'no-cache');
      headers.set('Pragma', 'no-cache');
      next.headers = headers;
    } catch (err) {
      // If headers cannot be cloned, keep the original request options.
    }
    return next;
  }

  const originalFetch = window.fetch;
  if (typeof originalFetch === 'function') {
    window.fetch = function (input, init) {
      const mutation = isMutationRequest(input, init);
      const nextInit = withNoCache(init || {});
      return originalFetch(input, nextInit).then(function (response) {
        if (mutation && response && response.ok) markChanged('fetch-mutation');
        return response;
      });
    };
  }

  window.EasyAdminRefresh = {
    markChanged: markChanged,
    refreshNow: refreshKnownPageSections
  };
})();
