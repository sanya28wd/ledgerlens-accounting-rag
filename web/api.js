import { API_URL } from '/config.js';

/** HTTP connector; credentials live only in the owning tab. */
export class Api {
  /** @param {string} key */
  constructor(key) {
    this.key = key;
  }

  /** @param {string} path @param {RequestInit} options @returns {Promise<Response>} */
  async request(path, options) {
    const headers = new Headers(options.headers);
    headers.set('Authorization', `Bearer ${this.key}`);
    const response = await fetch(`${API_URL}${path}`, { ...options, headers });
    if (!response.ok) {
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error(
          `Backend request failed (HTTP ${response.status}). Check the API URL and service health.`,
        );
      }
      /** @type {unknown} */
      const body = await response.json();
      const message =
        body && typeof body === 'object' && 'message' in body && typeof body.message === 'string'
          ? body.message
          : `Request failed (${response.status}). Check backend logs.`;
      throw new Error(message);
    }
    return response;
  }
  /** @returns {Promise<import('./types.js').Status>} */
  async status() {
    return (await this.request('/api/status', {})).json();
  }
  /** @returns {Promise<import('./types.js').DocumentRecord[]>} */
  async documents() {
    return (await this.request('/api/documents', {})).json();
  }
  /** @param {File} file @returns {Promise<import('./types.js').DocumentRecord>} */
  async upload(file) {
    const body = new FormData();
    body.append('file', file);
    return (await this.request('/api/documents', { method: 'POST', body })).json();
  }
  /** @param {string} id @returns {Promise<void>} */
  async delete(id) {
    await this.request(`/api/documents/${id}`, { method: 'DELETE' });
  }
  /** @param {string} id @returns {Promise<void>} */
  async retry(id) {
    await this.request(`/api/documents/${id}/retry`, { method: 'POST' });
  }
  /** @param {string} id @returns {Promise<Blob>} */
  async original(id) {
    return (await this.request(`/api/documents/${id}/file`, {})).blob();
  }
  /** @param {string} question @param {string[]} documentIds @param {import('./types.js').Message[]} history @param {string} asOf @returns {Promise<import('./types.js').Answer>} */
  async ask(question, documentIds, history, asOf) {
    return (
      await this.request('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, document_ids: documentIds, history, as_of: asOf }),
      })
    ).json();
  }
  /** @param {string} name @returns {Promise<File>} */
  async sample(name) {
    const response = await fetch(`${API_URL}/samples/${name}`);
    if (!response.ok) throw new Error(`Could not load sample ${name} (${response.status}).`);
    return new File([await response.blob()], name, { type: 'application/pdf' });
  }
}
