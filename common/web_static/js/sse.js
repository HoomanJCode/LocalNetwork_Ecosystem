/**
 * sse.js — Reusable Server-Sent Events helper.
 *
 * Usage:
 *   const events = new SSE('/logs/stream', {
 *     onMessage: (data) => console.log(data),
 *     onEvent: { 'log': (data) => appendLog(data) },
 *     onStatus: (connected) => setDot(connected),
 *   });
 *   events.start();
 *   // events.stop();
 */
(function (global) {
  'use strict';

  class SSE {
    /**
     * @param {string} url       Endpoint returning text/event-stream
     * @param {object} [options]
     * @param {Function} [options.onMessage]  Generic handler for any message
     * @param {Object<string, Function>} [options.onEvent]  Named event handlers
     * @param {Function} [options.onStatus]   Called with true/false on connect/disconnect
     * @param {number} [options.retryMs=2000] Base reconnect delay
     */
    constructor(url, options = {}) {
      this.url = url;
      this.onMessage = options.onMessage || (() => {});
      this.onEvent = options.onEvent || {};
      this.onStatus = options.onStatus || (() => {});
      this.retryMs = options.retryMs || 2000;
      this.source = null;
      this.stopped = false;
      this._retryTimer = null;
      this._retryCount = 0;
    }

    start() {
      this.stopped = false;
      this._open();
    }

    stop() {
      this.stopped = true;
      if (this._retryTimer) {
        clearTimeout(this._retryTimer);
        this._retryTimer = null;
      }
      if (this.source) {
        this.source.close();
        this.source = null;
      }
    }

    _open() {
      if (this.stopped) return;
      const source = new EventSource(this.url);
      this.source = source;

      source.onopen = () => {
        this._retryCount = 0;
        this.onStatus(true);
      };

      source.onmessage = (event) => {
        let data = event.data;
        try { data = JSON.parse(data); } catch (_) { /* keep raw */ }
        this.onMessage(data);
      };

      source.onerror = () => {
        this.onStatus(false);
        if (source === this.source) this._scheduleReconnect();
      };

      // Named events: <event: name>\n data: ...
      for (const name of Object.keys(this.onEvent)) {
        source.addEventListener(name, (event) => {
          let data = event.data;
          try { data = JSON.parse(data); } catch (_) { /* keep raw */ }
          this.onEvent[name](data);
        });
      }
    }

    _scheduleReconnect() {
      if (this.stopped || this._retryTimer) return;
      this._retryCount += 1;
      const delay = Math.min(this.retryMs * Math.pow(2, this._retryCount - 1), 30000);
      this._retryTimer = setTimeout(() => {
        this._retryTimer = null;
        this._open();
      }, delay);
    }
  }

  global.SSE = SSE;
})(window);
