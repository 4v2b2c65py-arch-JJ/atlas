#!/usr/bin/env node
/**
 * GLASMO Persistent Connection Keeper
 * Maintains WebSocket connections to organic-iptv (:3032) and prism-avatar (:3033)
 * Auto-reconnects with exponential backoff. Runs as background service.
 */

const WebSocket = require('ws');

const ENDPOINTS = [
  { name: 'organic-iptv', url: 'ws://localhost:3032/signals/organic' },
  { name: 'prism-avatar', url: 'ws://localhost:3033/avatar/prism' }
];

const RECONNECT_BASE_MS = 1000;
const MAX_RECONNECT_MS = 30000;

class PersistentConnection {
  constructor(endpoint) {
    this.endpoint = endpoint;
    this.ws = null;
    this.reconnectAttempt = 0;
    this.alive = false;
    this.keepAliveInterval = null;
  }

  connect() {
    console.log(`[${this.endpoint.name}] Connecting to ${this.endpoint.url}...`);
    this.ws = new WebSocket(this.endpoint.url);

    this.ws.on('open', () => {
      console.log(`[${this.endpoint.name}] Connected`);
      this.alive = true;
      this.reconnectAttempt = 0;
      this.startKeepAlive();
    });

    this.ws.on('message', (data) => {
      const msg = data.toString();
      if (msg !== 'ping') {
        console.log(`[${this.endpoint.name}] Received: ${msg.slice(0, 100)}`);
      }
    });

    this.ws.on('pong', () => {
      this.alive = true;
    });

    this.ws.on('close', (code, reason) => {
      console.log(`[${this.endpoint.name}] Disconnected (${code}): ${reason}`);
      this.alive = false;
      this.stopKeepAlive();
      this.scheduleReconnect();
    });

    this.ws.on('error', (err) => {
      console.error(`[${this.endpoint.name}] Error:`, err.message);
    });
  }

  startKeepAlive() {
    this.keepAliveInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.alive = false;
        this.ws.ping();
        setTimeout(() => {
          if (!this.alive) {
            console.log(`[${this.endpoint.name}] Keep-alive timeout, forcing reconnect`);
            this.ws.terminate();
          }
        }, 5000);
      }
    }, 15000);
  }

  stopKeepAlive() {
    if (this.keepAliveInterval) {
      clearInterval(this.keepAliveInterval);
      this.keepAliveInterval = null;
    }
  }

  scheduleReconnect() {
    const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, this.reconnectAttempt), MAX_RECONNECT_MS);
    const jitter = Math.random() * 1000;
    const totalDelay = delay + jitter;

    console.log(`[${this.endpoint.name}] Reconnecting in ${Math.round(totalDelay)}ms (attempt ${this.reconnectAttempt + 1})`);
    this.reconnectAttempt++;

    setTimeout(() => this.connect(), totalDelay);
  }
}

console.log('=== GLASMO Persistent Connection Keeper ===');
console.log('Maintains live WebSocket connections to signal services');
console.log('Press Ctrl+C to stop\n');

const connections = ENDPOINTS.map(ep => new PersistentConnection(ep));
connections.forEach(c => c.connect());

process.on('SIGINT', () => {
  console.log('\nShutting down...');
  connections.forEach(c => {
    c.stopKeepAlive();
    if (c.ws) c.ws.close(1000, 'shutdown');
  });
  setTimeout(() => process.exit(0), 1000);
});

process.on('SIGTERM', () => process.emit('SIGINT'));