import { Api } from './api.js';

/** @template {HTMLElement} T @param {string} id @param {new (...args:never[]) => T} kind @returns {T} */
function element(id, kind) {
  const found = document.getElementById(id);
  if (!(found instanceof kind)) throw new Error(`Missing interface element: ${id}`);
  return found;
}
/** @param {string} tag @param {string} text @param {string} className @returns {HTMLElement} */
function node(tag, text, className) {
  const result = document.createElement(tag);
  result.textContent = text;
  result.className = className;
  return result;
}
/** @param {unknown} error @returns {string} */
function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

function start() {
  const accessDialog = element('access-dialog', HTMLDialogElement);
  const question = element('question', HTMLTextAreaElement);
  const fileInput = element('file-input', HTMLInputElement);
  const asOf = element('as-of', HTMLInputElement);
  const conversation = element('conversation', HTMLDivElement);
  const evidenceContent = element('evidence-content', HTMLDivElement);
  const welcome = element('welcome', HTMLDivElement);
  const emptyEvidence = evidenceContent.cloneNode(true);
  const previewDialog = element('preview-dialog', HTMLDialogElement);
  const date = new Date();
  asOf.value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  /** @type {Api|null} */ let api = null;
  /** @type {import('./types.js').DocumentRecord[]} */ let documents = [];
  /** @type {Set<string>} */ let selected = new Set();
  /** @type {Set<string>} */ let knownReady = new Set();
  /** @type {import('./types.js').Message[]} */ let history = [];
  /** @type {number|null} */ let poll = null;
  /** @type {string|null} */ let previewUrl = null;
  let sending = false;

  /** @param {string} text */
  function notice(text) {
    const target = element('notice', HTMLDivElement);
    target.textContent = text;
    target.hidden = !text;
  }
  /** @returns {Api} */
  function requireApi() {
    if (api === null) {
      accessDialog.showModal();
      throw new Error('Unlock the workspace first.');
    }
    return api;
  }
  function updateScope() {
    element('scope-label', HTMLSpanElement).textContent = selected.size
      ? `${selected.size} document${selected.size === 1 ? '' : 's'} selected · ready to explore`
      : 'No documents selected';
  }

  /** @param {import('./types.js').DocumentRecord} item @param {number} page */
  async function preview(item, page) {
    try {
      const blob = await requireApi().original(item.id);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(blob);
      element('preview-name', HTMLElement).textContent = `${item.name} · page ${page}`;
      const frame = element('preview-frame', HTMLIFrameElement);
      const image = element('preview-image', HTMLImageElement);
      frame.hidden = item.media_type !== 'application/pdf';
      image.hidden = !frame.hidden;
      if (!frame.hidden) frame.src = `${previewUrl}#page=${page}`;
      else image.src = previewUrl;
      const download = element('preview-download', HTMLAnchorElement);
      download.href = previewUrl;
      download.download = item.name;
      previewDialog.showModal();
    } catch (error) {
      notice(errorText(error));
    }
  }

  /** @param {import('./types.js').DocumentRecord} item */
  function showDocument(item) {
    evidenceContent.replaceChildren();
    const card = node('div', '', 'source-card');
    card.append(
      node('h3', item.name, ''),
      node('small', `${item.page_count} pages · ${item.ocr_pages} OCR pages`, ''),
    );
    if (item.accounting) {
      card.append(
        node(
          'p',
          `${item.accounting.document_type} · ${item.accounting.complete ? 'Accounting extraction complete' : 'Accounting review needed'}`,
          '',
        ),
      );
      for (const issue of item.accounting.issues) card.append(node('p', issue, 'answer-issue'));
    }
    const button = document.createElement('button');
    button.className = 'text-button';
    button.textContent = 'Open original ↗';
    button.onclick = () => {
      void preview(item, 1);
    };
    card.append(button);
    evidenceContent.append(card);
    element('evidence-count', HTMLSpanElement).textContent = '1';
  }

  function renderDocuments() {
    const list = element('document-list', HTMLDivElement);
    list.replaceChildren();
    element('document-count', HTMLSpanElement).textContent = String(documents.length);
    if (!documents.length) {
      const empty = node('div', '', 'library-empty');
      empty.append(
        node('span', '▤', 'library-icon'),
        node('p', 'Your library is ready for its first document.', ''),
      );
      list.append(empty);
    }
    for (const item of documents) {
      const card = node('div', '', `document-item ${item.status === 'failed' ? 'failed' : ''}`);
      const top = node('div', '', 'document-top');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = selected.has(item.id);
      checkbox.disabled = item.status !== 'ready';
      checkbox.setAttribute('aria-label', `Include ${item.name} in questions`);
      checkbox.onchange = () => {
        selected = checkbox.checked
          ? new Set([...selected, item.id])
          : new Set([...selected].filter((id) => id !== item.id));
        updateScope();
      };
      const title = document.createElement('button');
      title.className = 'document-title';
      title.textContent = item.name;
      title.title = item.name;
      title.onclick = () => showDocument(item);
      top.append(checkbox, title);
      card.append(top);
      card.append(
        node(
          'div',
          item.status === 'ready'
            ? `${item.page_count} pages · ${item.ocr_pages ? 'OCR · ' : ''}${item.accounting?.complete ? 'Ready' : 'Review extraction'}`
            : item.stage,
          'document-meta',
        ),
      );
      if (item.error) {
        const problem = node('div', item.error, 'document-meta error-text');
        card.append(problem);
      }
      const actions = node('div', '', 'document-actions');
      const original = document.createElement('button');
      original.className = 'text-button';
      original.textContent = 'Preview';
      original.onclick = () => {
        void preview(item, 1);
      };
      actions.append(original);
      if (item.status === 'failed') {
        const retry = document.createElement('button');
        retry.className = 'text-button';
        retry.textContent = 'Retry';
        retry.onclick = () => {
          void requireApi()
            .retry(item.id)
            .then(refresh)
            .catch((error) => notice(errorText(error)));
        };
        actions.append(retry);
      }
      const remove = document.createElement('button');
      remove.className = 'text-button';
      remove.textContent = 'Delete';
      remove.disabled = item.status === 'processing';
      remove.onclick = () => {
        if (window.confirm(`Delete ${item.name} and its indexed evidence?`)) {
          void requireApi()
            .delete(item.id)
            .then(refresh)
            .catch((error) => notice(errorText(error)));
        }
      };
      actions.append(remove);
      card.append(actions);
      list.append(card);
    }
    updateScope();
  }

  async function refresh() {
    if (!api) return;
    documents = await api.documents();
    const ready = documents.filter((item) => item.status === 'ready');
    selected = new Set([...selected].filter((id) => ready.some((item) => item.id === id)));
    for (const item of ready) if (!knownReady.has(item.id)) selected.add(item.id);
    knownReady = new Set(ready.map((item) => item.id));
    renderDocuments();
    if (poll !== null) window.clearTimeout(poll);
    if (documents.some((item) => ['queued', 'processing'].includes(item.status)))
      poll = window.setTimeout(() => {
        void refresh().catch((error) => notice(errorText(error)));
      }, 1800);
  }

  /** @param {File[]} files */
  async function uploadFiles(files) {
    if (!api) {
      accessDialog.showModal();
      return;
    }
    const failures = [];
    for (const file of files) {
      try {
        notice(`Uploading ${file.name}…`);
        await api.upload(file);
      } catch (error) {
        failures.push(`${file.name}: ${errorText(error)}`);
      }
    }
    await refresh();
    notice(
      failures.length
        ? failures.join(' ')
        : 'Documents accepted. Extraction and indexing will continue in the background.',
    );
  }

  /** @param {import('./types.js').Source[]} sources @param {string|null} focused */
  function renderSources(sources, focused) {
    evidenceContent.replaceChildren();
    /** @type {Map<string, import('./types.js').Source[]>} */
    const groups = new Map();
    for (const source of sources) {
      const key = `${source.document_id}:${source.page}`;
      groups.set(key, [...(groups.get(key) || []), source]);
    }
    element('evidence-count', HTMLSpanElement).textContent = String(groups.size);
    for (const group of groups.values()) {
      const source = group[0];
      const active = group.some((item) => item.id === focused);
      const card = node('div', '', `source-card ${active ? 'active' : ''}`);
      card.append(
        node('h3', source.document_name, ''),
        node('small', `PAGE ${source.page} · VERIFIED EXCERPT`, ''),
      );
      for (const excerpt of new Set(group.map((item) => item.excerpt))) {
        card.append(node('blockquote', excerpt, ''));
      }
      const button = document.createElement('button');
      button.textContent = 'View original page ↗';
      button.className = 'text-button';
      button.onclick = () => {
        const item = documents.find((item) => item.id === source.document_id);
        if (item) void preview(item, source.page);
        else notice('This document was deleted. Its preview is no longer available.');
      };
      card.append(button);
      evidenceContent.append(card);
      if (active) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    if (!sources.length)
      evidenceContent.append(
        node(
          'p',
          'This response has no supported source claims. Check the answer for missing information or clarification.',
          'answer-issue',
        ),
      );
  }

  /** @param {import('./types.js').Answer} answer @returns {HTMLElement} */
  function renderAnswer(answer) {
    const message = node('article', '', 'message assistant');
    const label = node('div', '', 'message-label');
    label.append(node('span', '↗', 'mini-brand'), node('span', 'LEDGERLENS', ''));
    message.append(label, node('h3', answer.title, ''));
    const sourceMap = new Map(answer.sources.map((source) => [source.id, source]));
    for (const claim of answer.claims) {
      const paragraph = node('p', claim.text, 'claim');
      const citedPages = new Set();
      for (const id of claim.source_ids) {
        const source = sourceMap.get(id);
        if (!source) continue;
        const key = `${source.document_id}:${source.page}`;
        if (citedPages.has(key)) continue;
        citedPages.add(key);
        const button = document.createElement('button');
        button.className = 'citation';
        button.textContent = `${source.document_name} · p.${source.page}`;
        button.onclick = () => renderSources(answer.sources, id);
        paragraph.append(document.createTextNode(' '), button);
      }
      message.append(paragraph);
    }
    if (answer.calculations.length) {
      const table = node('table', '', 'calculation-table');
      const head = node('thead', '', '');
      const heading = node('tr', '', '');
      heading.append(node('th', 'CONTRIBUTING RECORD', ''), node('th', 'AMOUNT', ''));
      head.append(heading);
      table.append(head);
      const body = node('tbody', '', '');
      for (const row of answer.calculations) {
        const line = node('tr', '', '');
        line.append(node('td', row.label, ''), node('td', `${row.currency} ${row.amount}`, ''));
        body.append(line);
      }
      table.append(body);
      message.append(table);
    }
    for (const issue of answer.issues) message.append(node('p', issue, 'answer-issue'));
    message.append(
      node(
        'div',
        `${answer.status.replaceAll('_', ' ')} · ${answer.coverage.replaceAll('_', ' ')} · as of ${answer.as_of}`,
        'answer-meta',
      ),
    );
    return message;
  }

  element('access-button', HTMLButtonElement).onclick = () => accessDialog.showModal();
  element('cancel-access', HTMLButtonElement).onclick = () => accessDialog.close();
  element('access-form', HTMLFormElement).onsubmit = async (event) => {
    event.preventDefault();
    const key = element('reviewer-key', HTMLInputElement);
    try {
      const candidate = new Api(key.value);
      const status = await candidate.status();
      api = candidate;
      key.value = '';
      element('access-label', HTMLSpanElement).textContent = 'Workspace unlocked';
      element('access-error', HTMLParagraphElement).textContent = '';
      accessDialog.close();
      await refresh();
      notice(
        !status.model_configured
          ? 'Workspace connected. Add OPENAI_API_KEY to the backend .env file and restart to enable uploads and questions.'
          : !status.ocr_installed
            ? 'Tesseract is missing on the backend. Install English OCR before uploading scans.'
            : '',
      );
    } catch (error) {
      if (accessDialog.open)
        element('access-error', HTMLParagraphElement).textContent = errorText(error);
      else notice(errorText(error));
    }
  };
  element('upload-button', HTMLButtonElement).onclick = () => {
    if (api) fileInput.click();
    else accessDialog.showModal();
  };
  fileInput.onchange = () => {
    void uploadFiles(Array.from(fileInput.files || [])).catch((error) => notice(errorText(error)));
    fileInput.value = '';
  };
  const drop = element('drop-zone', HTMLDivElement);
  drop.onclick = () => {
    if (api) fileInput.click();
    else accessDialog.showModal();
  };
  drop.onkeydown = (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      drop.click();
    }
  };
  drop.ondragover = (event) => {
    event.preventDefault();
    drop.classList.add('dragover');
  };
  drop.ondragleave = () => drop.classList.remove('dragover');
  drop.ondrop = (event) => {
    event.preventDefault();
    drop.classList.remove('dragover');
    void uploadFiles(Array.from(event.dataTransfer?.files || [])).catch((error) =>
      notice(errorText(error)),
    );
  };
  element('select-all', HTMLButtonElement).onclick = () => {
    selected = new Set(documents.filter((item) => item.status === 'ready').map((item) => item.id));
    renderDocuments();
  };
  for (const button of document.querySelectorAll('.question-card')) {
    if (button instanceof HTMLButtonElement)
      button.onclick = () => {
        question.value = button.dataset['question'] || '';
        question.focus();
      };
  }
  element('samples-button', HTMLButtonElement).onclick = async () => {
    if (!api) {
      accessDialog.showModal();
      return;
    }
    const button = element('samples-button', HTMLButtonElement);
    button.disabled = true;
    try {
      const files = [];
      for (const name of [
        'cedar-001.pdf',
        'cedar-002.pdf',
        'cedar-003.pdf',
        'nova-001.pdf',
        'october-statement.pdf',
      ])
        files.push(await api.sample(name));
      await uploadFiles(files);
    } catch (error) {
      notice(errorText(error));
    } finally {
      button.disabled = false;
    }
  };
  element('chat-form', HTMLFormElement).onsubmit = async (event) => {
    event.preventDefault();
    if (sending) return;
    if (!api) {
      accessDialog.showModal();
      return;
    }
    if (!selected.size) {
      notice('Select at least one ready document in your library.');
      return;
    }
    const text = question.value.trim();
    if (!text) return;
    sending = true;
    element('send-button', HTMLButtonElement).disabled = true;
    welcome.remove();
    notice('');
    conversation.append(node('article', text, 'message user'));
    question.value = '';
    const thinking = node('div', 'Reading the selected evidence…', 'thinking');
    conversation.append(thinking);
    thinking.scrollIntoView({ block: 'nearest' });
    try {
      const answer = await api.ask(text, [...selected], history, asOf.value);
      conversation.append(renderAnswer(answer));
      /** @type {import('./types.js').Message[]} */
      const nextHistory = [
        ...history,
        { role: 'user', content: text },
        {
          role: 'assistant',
          content: [answer.title, ...answer.claims.map((claim) => claim.text), ...answer.issues]
            .join('\n')
            .slice(0, 6000),
        },
      ];
      history = nextHistory.slice(-8);
      renderSources(answer.sources, null);
    } catch (error) {
      notice(errorText(error));
    } finally {
      thinking.remove();
      sending = false;
      element('send-button', HTMLButtonElement).disabled = false;
      conversation.scrollTop = conversation.scrollHeight;
    }
  };
  question.onkeydown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      element('chat-form', HTMLFormElement).requestSubmit();
    }
  };
  element('clear-chat', HTMLButtonElement).onclick = () => {
    if (sending) return;
    history = [];
    conversation.replaceChildren(welcome);
    if (emptyEvidence instanceof HTMLElement)
      evidenceContent.replaceChildren(...Array.from(emptyEvidence.cloneNode(true).childNodes));
    element('evidence-count', HTMLSpanElement).textContent = '0';
  };
  element('close-preview', HTMLButtonElement).onclick = () => previewDialog.close();
  previewDialog.onclose = () => {
    element('preview-frame', HTMLIFrameElement).src = 'about:blank';
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  };
}
start();
