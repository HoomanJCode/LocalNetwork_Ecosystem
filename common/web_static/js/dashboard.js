/**
 * dashboard.js — Stat counters (count-up), sparklines, auto-refresh helpers.
 */
(function (global) {
  'use strict';

  /** Animate a number from 0 (or fromValue) to its data-value attribute. */
  function countUp(el, fromValue) {
    const target = parseFloat(el.getAttribute('data-value'));
    if (isNaN(target)) return;
    const duration = 500; // ms, ease-out
    const start = performance.now();
    const from = fromValue || 0;
    function tick(now) {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      const value = from + (target - from) * eased;
      el.textContent = formatNumber(value);
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  /** 1243.4 -> "1,243"; 2.4 -> "2.4" */
  function formatNumber(value, decimals) {
    const isFloat = !Number.isInteger(value) && Math.abs(value) < 10000;
    const digits = decimals !== undefined ? decimals : (isFloat ? 1 : 0);
    return value.toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  /** Initialize all [data-countup] elements on the page. */
  function initCounters() {
    document.querySelectorAll('[data-countup]').forEach((el) => {
      countUp(el);
    });
  }

  /**
   * Tiny inline SVG sparkline. el: container, values: array of numbers.
   * Renders a polyline with accent stroke and a soft fill.
   */
  function sparkline(el, values, { width = 160, height = 40, stroke = null } = {}) {
    if (!values || values.length < 2) return;
    const max = Math.max(...values);
    const min = Math.min(...values);
    const range = max - min || 1;
    const stepX = width / (values.length - 1);
    const points = values.map((v, i) => {
      const x = i * stepX;
      const y = height - 3 - ((v - min) / range) * (height - 8);
      return `${x},${y}`;
    });
    const color = stroke || getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#6366f1';
    const svg = `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="none" aria-hidden="true">
      <polyline fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"
                points="${points.join(' ')}" style="filter: drop-shadow(0 0 2px ${color}66)"/>
      <polygon fill="${color}" opacity="0.12" points="0,${height} ${points.join(' ')} ${width},${height}"/>
    </svg>`;
    el.innerHTML = svg;
  }

  /**
   * Auto-refresh a JSON endpoint into elements.
   * @param {string} url        Endpoint returning JSON
   * @param {object} handlers   { '.selector': (data) => textOrValue }
   * @param {number} [intervalMs=5000]
   */
  function autoRefresh(url, handlers, intervalMs = 5000) {
    async function refresh() {
      try {
        const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (!res.ok) return;
        const data = await res.json();
        for (const [selector, extract] of Object.entries(handlers)) {
          document.querySelectorAll(selector).forEach((el) => {
            const value = extract(data);
            if (typeof value === 'number' && el.hasAttribute('data-countup')) {
              countUp(el);
            } else if (value !== undefined && value !== null) {
              el.textContent = value;
            }
          });
        }
      } catch (_) {
        /* network hiccup — retry next tick */
      }
    }
    refresh();
    return setInterval(refresh, intervalMs);
  }

  global.Dashboard = { countUp, formatNumber, initCounters, sparkline, autoRefresh };
})(window);
