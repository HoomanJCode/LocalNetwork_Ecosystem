/**
 * ui.js — Toasts, modals, sidebar toggle, and small UI helpers.
 */
(function (global) {
  'use strict';

  const ui = {};

  /* ---------------- Toasts ---------------- */

  function ensureToastContainer() {
    let container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
      container.setAttribute('aria-live', 'polite');
      document.body.appendChild(container);
    }
    return container;
  }

  const TOAST_ICONS = {
    success: '✅',
    error: '❌',
    warning: '⚠️',
    info: 'ℹ️',
  };

  /**
   * ui.toast('Saved!', 'success')
   * Auto-dismisses after 5s. role="alert" for errors.
   */
  ui.toast = function (message, variant = 'info') {
    const container = ensureToastContainer();
    const toast = document.createElement('div');
    toast.className = `toast toast--${variant}`;
    toast.setAttribute('role', variant === 'error' ? 'alert' : 'status');
    toast.innerHTML = `
      <span class="toast__icon">${TOAST_ICONS[variant] || TOAST_ICONS.info}</span>
      <span class="toast__msg"></span>
      <button type="button" class="toast__close" aria-label="Dismiss">✕</button>`;
    toast.querySelector('.toast__msg').textContent = message;
    container.appendChild(toast);

    const dismiss = () => {
      toast.classList.add('is-leaving');
      setTimeout(() => toast.remove(), 200);
    };
    toast.querySelector('.toast__close').addEventListener('click', dismiss);
    setTimeout(dismiss, 5000);
  };

  /* ---------------- Modals ---------------- */

  /**
   * ui.openModal({ title, body, confirmText, onConfirm, danger })
   * Returns the overlay element.
   */
  ui.openModal = function ({ title = 'Confirm', body = '', confirmText = 'Confirm', cancelText = 'Cancel', onConfirm = null, danger = false }) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-label="${title}">
        <div class="modal__header">
          <h3 class="modal__title">${title}</h3>
          <button type="button" class="modal__close" aria-label="Close">×</button>
        </div>
        <div class="modal__body"></div>
        <div class="modal__footer">
          <button type="button" class="btn btn--secondary" data-cancel>${cancelText}</button>
          <button type="button" class="btn ${danger ? 'btn--danger' : 'btn--primary'}" data-confirm>${confirmText}</button>
        </div>
      </div>`;
    const bodyEl = overlay.querySelector('.modal__body');
    if (typeof body === 'string') bodyEl.innerHTML = body;
    else if (body instanceof Node) bodyEl.appendChild(body);

    const close = () => {
      overlay.classList.remove('is-open');
      setTimeout(() => overlay.remove(), 200);
      document.removeEventListener('keydown', onKey);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close();
    };

    overlay.querySelector('.modal__close').addEventListener('click', close);
    overlay.querySelector('[data-cancel]').addEventListener('click', close);
    overlay.querySelector('[data-confirm]').addEventListener('click', () => {
      if (onConfirm) onConfirm(overlay);
      else close();
    });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close();
    });
    document.addEventListener('keydown', onKey);

    document.body.appendChild(overlay);
    // Trigger transition on next frame
    requestAnimationFrame(() => overlay.classList.add('is-open'));
    const firstInput = overlay.querySelector('input, button[data-confirm]');
    if (firstInput) firstInput.focus();
    return overlay;
  };

  /* ---------------- Sidebar (mobile) ---------------- */

  function initSidebar() {
    const sidebar = document.querySelector('.app-sidebar');
    const toggle = document.querySelector('[data-sidebar-toggle]');
    if (!sidebar || !toggle) return;

    let backdrop = document.querySelector('.sidebar-backdrop');
    if (!backdrop) {
      backdrop = document.createElement('div');
      backdrop.className = 'sidebar-backdrop';
      document.body.appendChild(backdrop);
    }

    const close = () => {
      sidebar.classList.remove('is-open');
      backdrop.classList.remove('is-visible');
    };
    toggle.addEventListener('click', () => {
      sidebar.classList.toggle('is-open');
      backdrop.classList.toggle('is-visible');
    });
    backdrop.addEventListener('click', close);
    sidebar.querySelectorAll('.app-sidebar__item').forEach((item) => {
      item.addEventListener('click', close);
    });
  }

  /* ---------------- Misc helpers ---------------- */

  /** Simple fetch wrapper that shows toasts on failure. */
  ui.fetchJSON = async function (url, options = {}) {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = text; }
    if (!res.ok) {
      const msg = (data && (data.error || data.message)) || `Request failed (${res.status})`;
      ui.toast(msg, 'error');
      throw new Error(msg);
    }
    return data;
  };

  /** Clipboard helper with toast feedback. */
  ui.copy = function (text) {
    navigator.clipboard.writeText(text).then(() => ui.toast('Copied to clipboard', 'success'));
  };

  ui.init = function () {
    initSidebar();
  };

  global.UI = ui;
})(window);

// Boot when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => window.UI && window.UI.init());
} else {
  window.UI && window.UI.init();
}
