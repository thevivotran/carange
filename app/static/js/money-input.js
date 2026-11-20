/*
 * Money inputs with live digit grouping ("500000000" → "500,000,000").
 *
 * Mark a field with data-money:
 *   <label for="amount">Amount</label>
 *   <input type="number" id="amount" name="amount" data-money required>
 *
 * bindMoneyInputs() keeps that element as a hidden field — same id and name, holding
 * the plain integer — so existing code (`el.value`, form/HTMX serialisation, `el.value = n`)
 * keeps working unchanged. A visible text input showing the grouped value is inserted
 * in its place, takes over the label, `required` and `placeholder`, and writes back to
 * the hidden field on every keystroke. Setting `.value` on the hidden field from JS
 * updates the display too.
 */
(function () {
    'use strict';

    function group(n) {
        return window._groupDigits ? window._groupDigits(n) : Number(n).toLocaleString('en-US');
    }

    function digitsOf(v) {
        return String(v == null ? '' : v).split('.')[0].replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, 15);
    }

    // Regroup el's value in place, keeping the caret after the same digit. Returns the digits.
    function formatInPlace(el) {
        var digitsBeforeCaret = el.value.slice(0, el.selectionStart || 0).replace(/\D/g, '').length;
        var digits = digitsOf(el.value);
        el.value = digits ? group(Number(digits)) : '';
        var pos = 0, seen = 0;
        while (pos < el.value.length && seen < digitsBeforeCaret) {
            if (/\d/.test(el.value[pos])) seen++;
            pos++;
        }
        if (document.activeElement === el) el.setSelectionRange(pos, pos);
        return digits;
    }

    var nativeValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');

    function bind(raw) {
        if (raw.dataset.moneyBound) return;
        raw.dataset.moneyBound = '1';

        var display = document.createElement('input');
        display.type = 'text';
        display.inputMode = 'numeric';
        display.autocomplete = 'off';
        display.className = raw.className;
        display.placeholder = raw.placeholder || '';
        display.id = (raw.id || 'money-' + Math.random().toString(36).slice(2)) + '-display';
        display.required = raw.required;
        display.disabled = raw.disabled;
        ['title', 'aria-label', 'aria-describedby'].forEach(function (a) {
            if (raw.hasAttribute(a)) display.setAttribute(a, raw.getAttribute(a));
        });

        // The label now names the field the user actually types in.
        if (raw.id) {
            document.querySelectorAll('label[for="' + raw.id + '"]').forEach(function (l) { l.htmlFor = display.id; });
        }

        raw.required = false;
        raw.type = 'hidden';
        raw.parentNode.insertBefore(display, raw);

        function sync() { display.value = digitsOf(nativeValue.get.call(raw)) ? group(Number(digitsOf(nativeValue.get.call(raw)))) : ''; }

        Object.defineProperty(raw, 'value', {
            configurable: true,
            get: function () { return nativeValue.get.call(raw); },
            set: function (v) { nativeValue.set.call(raw, digitsOf(v)); sync(); },
        });
        raw.value = nativeValue.get.call(raw);

        display.addEventListener('input', function () {
            nativeValue.set.call(raw, formatInPlace(display));
            raw.dispatchEvent(new Event('input', { bubbles: true }));
        });
        display.addEventListener('change', function () {
            raw.dispatchEvent(new Event('change', { bubbles: true }));
        });
        if (raw.form) {
            raw.form.addEventListener('reset', function () { setTimeout(sync, 0); });
        }
    }

    window.bindMoneyInputs = function (root) {
        (root || document).querySelectorAll('input[data-money]').forEach(bind);
    };
    window.formatMoneyInputInPlace = formatInPlace;

    document.addEventListener('DOMContentLoaded', function () { window.bindMoneyInputs(); });
    document.addEventListener('htmx:afterSwap', function (e) { window.bindMoneyInputs(e.target); });
})();
