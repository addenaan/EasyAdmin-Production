(function () {
  'use strict';

  const MONEY_FORMATTER = new Intl.NumberFormat('en-ZA', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });

  function normaliseNumber(value) {
    if (value === null || value === undefined || value === '') return 0;
    if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
    let text = String(value).replace(/\u00a0/g, ' ').trim();
    let negative = /^-/.test(text) || /\(.*\)/.test(text);
    text = text.replace(/[()]/g, '').replace(/[Rr]|ZAR/gi, '').replace(/\s+/g, '');
    const hasComma = text.includes(',');
    const hasDot = text.includes('.');
    if (hasComma && hasDot) {
      // Last separator is treated as the decimal separator.
      if (text.lastIndexOf(',') > text.lastIndexOf('.')) text = text.replace(/\./g, '').replace(',', '.');
      else text = text.replace(/,/g, '');
    } else if (hasComma) {
      text = text.replace(',', '.');
    }
    text = text.replace(/[^0-9.\-]/g, '');
    let num = Number(text || 0);
    if (!Number.isFinite(num)) num = 0;
    return negative ? -Math.abs(num) : num;
  }

  function money(value) {
    return MONEY_FORMATTER.format(normaliseNumber(value));
  }

  function date(value) {
    if (value === null || value === undefined) return '';
    const text = String(value).trim();
    if (!text) return '';
    const iso = text.match(/^(\d{4})[-/](\d{2})[-/](\d{2})/);
    if (iso) return `${iso[1]}-${iso[2]}-${iso[3]}`;
    const sa = text.match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/);
    if (sa) {
      const d = String(sa[1]).padStart(2, '0');
      const m = String(sa[2]).padStart(2, '0');
      return `${sa[3]}-${m}-${d}`;
    }
    return text;
  }

  function escapeRegExp(s) { return String(s || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  function formatCurrencyInText(text) {
    if (!text || !/[Rr]|ZAR|\d{1,3}[\s\u00a0,]\d{3}[,.]\d{2}/.test(text)) return text;
    let out = text;
    out = out.replace(/-\s*(?:R|ZAR)\s*([0-9][0-9\s\u00a0.,]*[.,][0-9]{2})/gi, function (_, amount) {
      return money('-' + amount);
    });
    out = out.replace(/(?:R|ZAR)\s*([-]?[0-9][0-9\s\u00a0.,]*[.,][0-9]{2})/gi, function (_, amount) {
      return money(amount);
    });
    return out;
  }

  function formatDatesInText(text) {
    if (!text || !/\d{1,4}[/-]\d{1,2}[/-]\d{2,4}/.test(text)) return text;
    return text
      .replace(/\b(\d{4})\/(\d{2})\/(\d{2})\b/g, '$1-$2-$3')
      .replace(/\b(\d{1,2})\/(\d{1,2})\/(\d{4})\b/g, function (_, d, m, y) {
        return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      });
  }

  function shouldSkip(el) {
    if (!el || el.nodeType !== 1) return true;
    if (el.closest('[data-easyadmin-no-format]')) return true;
    return !!el.closest('script, style, textarea, input, select, option, code, pre');
  }

  function elementLooksMoney(el) {
    const cls = (el.className || '').toString().toLowerCase();
    const id = (el.id || '').toLowerCase();
    const hint = `${cls} ${id}`;
    return /\bmoney\b|amount|total|subtotal|vat|balance|revenue|cost|profit|price|paid|outstanding|due|debit|credit|net|gross|paye|uif/.test(hint);
  }

  function elementLooksDate(el) {
    const cls = (el.className || '').toString().toLowerCase();
    const id = (el.id || '').toLowerCase();
    return /\bdate\b|due|period|month/.test(`${cls} ${id}`);
  }

  function formatElementText(el) {
    if (shouldSkip(el)) return;
    if (el.children.length) return;
    const original = el.textContent;
    if (!original || !original.trim()) return;
    let next = formatCurrencyInText(original);
    next = formatDatesInText(next);

    if (elementLooksMoney(el)) {
      const stripped = next.replace(/\u00a0/g, ' ').trim();
      if (/^-?(?:R\s*)?[0-9][0-9\s,.]*$/.test(stripped) && /[0-9]/.test(stripped)) {
        next = money(stripped);
      }
    }
    if (elementLooksDate(el)) {
      const stripped = next.trim();
      if (/^\d{4}[-/]\d{2}[-/]\d{2}/.test(stripped) || /^\d{1,2}[/-]\d{1,2}[/-]\d{4}$/.test(stripped)) {
        next = date(stripped);
      }
    }
    if (next !== original) el.textContent = next;
  }

  function formatTextNodes(root) {
    const walker = document.createTreeWalker(root || document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || shouldSkip(parent)) return NodeFilter.FILTER_REJECT;
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      const original = node.nodeValue;
      let next = formatCurrencyInText(original);
      next = formatDatesInText(next);
      if (next !== original) node.nodeValue = next;
    });
  }

  let scheduled = false;
  function refresh(root) {
    const base = root && root.nodeType === 1 ? root : document.body;
    if (!base) return;
    base.querySelectorAll('.money, .col-amount, .amount-col, .unit-price-col, .sub-cost span:last-child, [data-format="money"], [data-format="date"]').forEach(formatElementText);
    formatTextNodes(base);
  }

  function scheduleRefresh(root) {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(() => {
      scheduled = false;
      refresh(root || document.body);
    });
  }

  window.EasyAdminFormat = { money, date, refresh };
  window.formatMoney = window.formatMoney || money;
  window.formatDisplayMoney = window.formatDisplayMoney || money;
  window.formatDisplayDate = window.formatDisplayDate || date;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => scheduleRefresh(document.body));
  } else {
    scheduleRefresh(document.body);
  }

  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'childList' || m.type === 'characterData') {
        scheduleRefresh(document.body);
        break;
      }
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    if (document.body) observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });
})();
