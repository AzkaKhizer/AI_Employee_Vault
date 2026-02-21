/**
 * Odoo JSON-RPC client for Odoo 19+.
 * Handles authentication and model method calls.
 */

import fetch from "node-fetch";

export class OdooClient {
  constructor({ url, db, username, password }) {
    this.url = url.replace(/\/+$/, "");
    this.db = db;
    this.username = username;
    this.password = password;
    this.uid = null;
    this.sessionCookie = null;
  }

  async _jsonrpc(endpoint, params) {
    const headers = { "Content-Type": "application/json" };
    if (this.sessionCookie) headers["Cookie"] = this.sessionCookie;

    const res = await fetch(`${this.url}${endpoint}`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: Date.now(),
        method: "call",
        params,
      }),
    });

    // Capture session cookie on authenticate
    const setCookie = res.headers.get("set-cookie");
    if (setCookie) {
      this.sessionCookie = setCookie.split(";")[0];
    }

    const data = await res.json();
    if (data.error) {
      const msg = data.error.data?.message || data.error.message || JSON.stringify(data.error);
      throw new Error(`Odoo RPC error: ${msg}`);
    }
    return data.result;
  }

  async authenticate() {
    if (this.uid) return this.uid;
    const result = await this._jsonrpc("/web/session/authenticate", {
      db: this.db,
      login: this.username,
      password: this.password,
    });
    if (!result || !result.uid) {
      throw new Error("Odoo authentication failed — check credentials.");
    }
    this.uid = result.uid;
    return this.uid;
  }

  async call(model, method, args = [], kwargs = {}) {
    await this.authenticate();
    return this._jsonrpc("/web/dataset/call_kw", {
      model,
      method,
      args,
      kwargs: { ...kwargs, context: kwargs.context || {} },
    });
  }

  async searchRead(model, domain, fields, options = {}) {
    await this.authenticate();
    return this._jsonrpc("/web/dataset/call_kw", {
      model,
      method: "search_read",
      args: [domain],
      kwargs: {
        fields,
        limit: options.limit || 0,
        offset: options.offset || 0,
        order: options.order || "",
        context: options.context || {},
      },
    });
  }

  async create(model, values) {
    await this.authenticate();
    return this._jsonrpc("/web/dataset/call_kw", {
      model,
      method: "create",
      args: [values],
      kwargs: { context: {} },
    });
  }
}
