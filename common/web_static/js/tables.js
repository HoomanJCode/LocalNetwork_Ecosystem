/**
 * tables.js — Sortable, filterable, paginated tables.
 *
 * Usage:
 *   <table class="data-table" data-sortable data-paginate data-page-size="10">
 *   new Tables.init(document.querySelector('.data-table'), { searchInput: '#search' });
 */
(function (global) {
  'use strict';

  class DataTable {
    constructor(table, options = {}) {
      this.table = table;
      this.searchInput = document.querySelector(options.searchInput || 'input[data-table-search]');
      this.pageSize = parseInt(table.getAttribute('data-page-size'), 10) || 10;
      this.sortIdx = -1;
      this.sortAsc = true;
      this.page = 0;
      this.search = '';
      this._rows = Array.from(table.querySelectorAll('tbody tr'));
      this._buildControls();
      this._bindEvents();
      this.render();
    }

    _buildControls() {
      // Wrap in a container for controls
      if (!this.table.dataset.controlsBuilt) {
        const wrap = document.createElement('div');
        wrap.className = 'data-table-wrap';
        this.table.parentNode.insertBefore(wrap, this.table);
        wrap.appendChild(this.table);
        this.table.dataset.controlsBuilt = '1';
        this.controls = document.createElement('div');
        this.controls.className = 'table-pagination';
        this.controls.innerHTML = `<span class="table-info">Loading…</span>
          <span class="table-nav">
            <button type="button" class="btn btn--sm btn--ghost" data-prev>‹ Prev</button>
            <button type="button" class="btn btn--sm btn--ghost" data-next>Next ›</button>
          </span>`;
        wrap.appendChild(this.controls);
        this.info = this.controls.querySelector('.table-info');
        this.prevBtn = this.controls.querySelector('[data-prev]');
        this.nextBtn = this.controls.querySelector('[data-next]');
      }
    }

    _bindEvents() {
      // Sorting
      this.table.querySelectorAll('thead th').forEach((th, idx) => {
        if (th.getAttribute('data-sort') === 'false') return;
        th.style.cursor = 'pointer';
        th.addEventListener('click', () => this._sort(idx));
      });
      // Pagination
      this.prevBtn.addEventListener('click', () => { this.page -= 1; this.render(); });
      this.nextBtn.addEventListener('click', () => { this.page += 1; this.render(); });
      // Search
      if (this.searchInput) {
        this.searchInput.addEventListener('input', (e) => {
          this.search = e.target.value.toLowerCase();
          this.page = 0;
          this.render();
        });
      }
    }

    _sort(idx) {
      if (this.sortIdx === idx) {
        this.sortAsc = !this.sortAsc;
      } else {
        this.sortIdx = idx;
        this.sortAsc = true;
      }
      this.page = 0;
      this.render();
    }

    _filtered() {
      let rows = this._rows;
      if (this.search) {
        rows = rows.filter((row) => row.textContent.toLowerCase().includes(this.search));
      }
      if (this.sortIdx >= 0) {
        const idx = this.sortIdx;
        const asc = this.sortAsc;
        rows = rows.slice().sort((a, b) => {
          const av = (a.cells[idx] ? a.cells[idx].textContent : '').trim();
          const bv = (b.cells[idx] ? b.cells[idx].textContent : '').trim();
          const an = parseFloat(av.replace(/[^0-9.\-]/g, ''));
          const bn = parseFloat(bv.replace(/[^0-9.\-]/g, ''));
          const cmp = (!isNaN(an) && !isNaN(bn)) ? an - bn : av.localeCompare(bv);
          return asc ? cmp : -cmp;
        });
      }
      return rows;
    }

    render() {
      const rows = this._filtered();
      const totalPages = Math.max(1, Math.ceil(rows.length / this.pageSize));
      this.page = Math.min(this.page, totalPages - 1);
      const start = this.page * this.pageSize;
      const visible = rows.slice(start, start + this.pageSize);

      // Show/hide rows
      this._rows.forEach((row) => { row.style.display = 'none'; });
      visible.forEach((row) => { row.style.display = ''; });

      const end = Math.min(start + visible.length, rows.length);
      this.info.textContent = `Showing ${rows.length ? start + 1 : 0}-${end} of ${rows.length}`;
      this.prevBtn.disabled = this.page === 0;
      this.nextBtn.disabled = this.page >= totalPages - 1;

      // Sort indicators
      this.table.querySelectorAll('thead th').forEach((th, idx) => {
        const marker = th.querySelector('.sort-marker');
        if (idx === this.sortIdx) {
          th.setAttribute('aria-sort', this.sortAsc ? 'ascending' : 'descending');
          if (!marker) {
            const m = document.createElement('span');
            m.className = 'sort-marker';
            m.style.fontSize = '0.7em';
            m.style.marginLeft = '4px';
            th.appendChild(m);
          }
          th.querySelector('.sort-marker').textContent = this.sortAsc ? '▲' : '▼';
        } else if (marker) {
          marker.remove();
        }
      });
    }
  }

  function init(selector) {
    document.querySelectorAll(selector || 'table[data-sortable]').forEach((t) => new DataTable(t));
  }

  global.Tables = { DataTable, init };
})(window);
