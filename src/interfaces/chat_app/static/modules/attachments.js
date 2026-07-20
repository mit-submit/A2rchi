/**
 * Attachments Module — per-conversation private file attachments.
 * "Attachment" (private to this conversation) is NOT the admin "Upload"
 * (shared knowledge base). See CONTEXT.md.
 *
 * Interaction model (ChatGPT/Claude style): pending attachments render as
 * cards inside the chat box, above the text field; once the message is sent
 * they attach to that message in the chat window. Removal is the ✕ on the
 * card itself. Warnings are model-facing only — the UI never shows ⚠.
 */

// Safety net for a POST whose connection stalls without ever settling: the
// upload is aborted after this long so its finally-block still runs and the
// Send button (disabled while uploads are pending) can recover. Generous
// enough not to abort a legitimately slow large-file upload + extraction.
const UPLOAD_TIMEOUT_MS = 180000;

class AttachmentsManager {
  constructor() {
    this.enabled = document.body.dataset.attachmentsEnabled === 'true';
    this.maxMb = parseInt(document.body.dataset.attachmentsMaxMb || '20', 10);
    this.items = [];              // current conversation's attachments (server truth)
    this._warnings = new Map();   // attachment_id -> first extraction warning (composer cards only)
    this.getConversationIdFn = null;
    this.setConversationIdFn = null;
    this.clientIdFn = null;
    this.liveRegion = null;       // polite aria-live node for SR announcements
    this._conversationCreation = null;  // shared promise while a batch mints its conversation
    this._resolveCreation = null;
    this._rejectCreation = null;
    this._pendingUploads = 0;     // in-flight upload POSTs
    this._lastNotifiedPending = false;
    this.onUploadStateChangeFn = null;  // host callback: pending-uploads 0↔n transitions
    this._generation = 0;         // bumped by reset(); uploads from a left conversation go quiet
  }

  init({ getConversationId, setConversationId, getClientId, onUploadStateChange }) {
    if (!this.enabled) return;
    if (this._initialized) return;
    this._initialized = true;
    this.getConversationIdFn = getConversationId;
    this.setConversationIdFn = setConversationId;
    this.clientIdFn = getClientId;
    this.onUploadStateChangeFn = onUploadStateChange || null;

    this.composer = document.querySelector('.composer-attachments');
    this.fileInput = document.getElementById('attachment-file-input');
    this.attachBtn = document.querySelector('.attach-btn');
    this.liveRegion = document.querySelector('.attachment-live-region');
    if (!this.composer || !this.fileInput || !this.attachBtn) return;

    this.attachBtn.addEventListener('click', () => this.fileInput.click());
    this.fileInput.addEventListener('change', () => {
      const files = Array.from(this.fileInput.files || []);
      this.fileInput.value = '';
      files.forEach((f) => this.upload(f));
    });

    // Drag & drop anywhere on the page, but only intercept when the drag
    // actually carries files — plain text/link drags stay native.
    const dragHasFiles = (e) =>
      Array.from(e.dataTransfer?.types || []).includes('Files');
    document.addEventListener('dragover', (e) => {
      if (dragHasFiles(e)) e.preventDefault();
    });
    document.addEventListener('drop', (e) => {
      if (!dragHasFiles(e)) return;
      e.preventDefault();
      Array.from(e.dataTransfer?.files || []).forEach((f) => this.upload(f));
    });
  }

  async upload(file) {
    if (file.size > this.maxMb * 1024 * 1024) {
      this._pendingError(file.name, `Larger than the ${this.maxMb} MB limit.`);
      return;
    }
    const card = this._pendingCard(file.name, file.size);
    const form = new FormData();
    form.append('file', file);
    form.append('client_id', this.clientIdFn());

    let leadingCreation = false;
    const generation = this._generation;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);
    this._pendingUploads += 1;
    this._notifyUploadState();
    try {
      // Single-flight: when there is no conversation yet, only the first
      // upload of a batch mints it; the rest await that same creation and
      // reuse its id instead of each spawning an orphan conversation.
      let convId = this.getConversationIdFn();
      while (convId == null && this._conversationCreation) {
        try { convId = await this._conversationCreation; }
        catch (_) { convId = this.getConversationIdFn(); }
      }
      if (convId == null) {
        leadingCreation = true;
        this._conversationCreation = new Promise((resolve, reject) => {
          this._resolveCreation = resolve;
          this._rejectCreation = reject;
        });
        // Keep the rejection handled: on reset()/error a batch with no awaiting
        // sibling would otherwise surface an unhandledrejection.
        this._conversationCreation.catch(() => {});
      }
      if (convId != null) form.append('conversation_id', convId);

      const resp = await fetch('/api/chat/attachments',
        { method: 'POST', body: form, signal: controller.signal });
      const body = await resp.json().catch(() => ({}));
      // Did the user leave this conversation while the POST was in flight?
      // A stale upload must neither settle a LATER batch's creation state nor
      // leak its conversation id into the new chat.
      const current = generation === this._generation;
      if (!resp.ok) {
        this._cardToError(card, body.error || `Attach failed (${resp.status})`);
        if (leadingCreation && current) this._failCreation();
        return;
      }
      // Batch siblings may be awaiting the minted conversation — settle them,
      // but only while this is still the active conversation (a stale leader's
      // creation promise was already abandoned by reset()).
      if (leadingCreation && current) this._settleCreation(body.conversation_id);
      card.remove();                       // replaced by the server-truth card
      if (!current) return;
      // Server may have created the conversation (attach-on-new-chat)
      if (body.conversation_id != null) this.setConversationIdFn(body.conversation_id);
      if (body.attachment_id && Array.isArray(body.warnings) && body.warnings.length) {
        this._warnings.set(body.attachment_id, String(body.warnings[0]));
      }
      await this.refresh(body.conversation_id);
      this._announce(`${body.filename || file.name} attached`);
    } catch (err) {
      this._cardToError(card,
        controller.signal.aborted ? 'Upload timed out — try again.' : 'Network error — try again.');
      if (leadingCreation && generation === this._generation) this._failCreation();
    } finally {
      clearTimeout(timeout);
      this._pendingUploads -= 1;
      if (this._pendingUploads <= 0) this._pendingUploads = 0;
      this._notifyUploadState();
    }
  }

  /** True while at least one upload POST has not yet settled. */
  hasPendingUploads() { return this._pendingUploads > 0; }

  /** Tells the host (chat.js) when the in-flight upload count crosses 0↔n. */
  _notifyUploadState() {
    if (!this.onUploadStateChangeFn) return;
    const pending = this._pendingUploads > 0;
    if (pending === this._lastNotifiedPending) return;
    this._lastNotifiedPending = pending;
    this.onUploadStateChangeFn(pending);
  }

  /** True when the composer holds at least one non-error attachment card. */
  hasComposerAttachments() {
    return !!(this.composer &&
      this.composer.querySelector('.attachment-card:not(.attachment-card-error)'));
  }

  _settleCreation(conversationId) {
    const resolve = this._resolveCreation;
    this._conversationCreation = null;
    this._resolveCreation = null;
    this._rejectCreation = null;
    if (resolve) resolve(conversationId);
  }

  _failCreation() {
    const reject = this._rejectCreation;
    this._conversationCreation = null;
    this._resolveCreation = null;
    this._rejectCreation = null;
    if (reject) reject(new Error('conversation creation failed'));
  }

  _announce(message) {
    if (!this.liveRegion) return;
    this.liveRegion.textContent = String(message);
  }

  async refresh(conversationId) {
    if (!this.enabled) { this.reset(); return; }
    const convId = conversationId != null ? conversationId : this.getConversationIdFn();
    if (convId == null) { this.reset(); return; }
    try {
      const resp = await fetch(
        `/api/chat/conversations/${convId}/attachments?client_id=${encodeURIComponent(this.clientIdFn())}`
      );
      if (!resp.ok) return;
      const body = await resp.json();
      this.items = body.attachments || [];
      this._render();
    } catch (_) { /* leave previous state */ }
  }

  onConversationLoaded(conversationId) { return this.refresh(conversationId); }

  /** Called by chat.js the moment a message is sent: pending cards move
   *  optimistically onto the just-sent message; refresh() reconciles later. */
  onMessageSent() {
    if (!this.composer) return;
    const pending = this.items.filter((a) => a.message_id == null);
    if (pending.length) {
      const candidates = document.querySelectorAll('.message.user');
      const host = candidates[candidates.length - 1];
      if (host && !host.querySelector('.message-attachment-chips')) {
        this._appendCards(host, pending);
      }
    }
    // Only clear cards already bound to server truth; a still-pending or
    // errored card (no data-attachment-id) must survive this send.
    this.composer.querySelectorAll('.attachment-card[data-attachment-id]').forEach((el) => el.remove());
    this._syncComposerVisibility();
  }

  reset() {
    // Leaving the conversation: anything still uploading belongs to it, not
    // to whatever chat comes next.
    this._generation += 1;
    // Abandon any in-flight conversation creation from the conversation we're
    // leaving. Without this, a fresh upload would await this dangling promise
    // and rejoin the conversation we just left instead of minting a new one.
    // Rejecting lets an old-batch sibling still awaiting it fall back instead
    // of hanging (the promise carries a no-op catch so an unawaited rejection
    // never surfaces as unhandledrejection).
    if (this._conversationCreation) this._failCreation();
    this.items = [];
    if (this.composer) {
      // Drop pending/error cards too — _render only clears server-truth ones.
      this.composer.querySelectorAll('.attachment-card').forEach((el) => el.remove());
      this._render();
    }
  }

  async remove(attachmentId) {
    if (!attachmentId) return;
    try {
      const resp = await fetch(
        `/api/chat/attachments/${attachmentId}?client_id=${encodeURIComponent(this.clientIdFn())}`,
        { method: 'DELETE' }
      );
      if (resp.status === 204) {
        this.items = this.items.filter((a) => a.attachment_id !== attachmentId);
        this._render();
        this._announce('Attachment removed');
      }
    } catch (err) {
      console.warn('Failed to remove attachment:', err);
    }
  }

  _render() {
    // Composer holds the attachments not yet bound to a sent message.
    this.composer.querySelectorAll('.attachment-card[data-attachment-id]').forEach((el) => el.remove());
    this.items
      .filter((a) => a.message_id == null)
      .forEach((a) => this.composer.appendChild(this._card(a)));
    this._syncComposerVisibility();
    this._renderMessageChips();
  }

  _syncComposerVisibility() {
    this.composer.hidden = this.composer.children.length === 0;
  }

  _renderMessageChips() {
    document.querySelectorAll('.message-attachment-chips').forEach((el) => el.remove());
    const byMessage = new Map();
    this.items.forEach((a) => {
      if (a.message_id == null) return;
      if (!byMessage.has(a.message_id)) byMessage.set(a.message_id, []);
      byMessage.get(a.message_id).push(a);
    });

    let highestId = null;
    byMessage.forEach((_atts, id) => {
      if (highestId == null || Number(id) > Number(highestId)) highestId = id;
    });

    const unhosted = [];
    byMessage.forEach((atts, messageId) => {
      const host = document.querySelector(
        `.message.user[data-message-id="${CSS.escape(String(messageId))}"]`
      );
      if (host) {
        this._appendCards(host, atts);
      } else {
        unhosted.push([messageId, atts]);
      }
    });

    // A freshly-sent user message keeps a client placeholder data-message-id
    // (e.g. "1720373921000-user") until the next reload, so the exact-match
    // pass above can't find a host for it; fall back to the last user
    // message in the DOM that hasn't been patched with a server id yet.
    unhosted.forEach(([messageId, atts]) => {
      if (messageId !== highestId) return;
      const candidates = document.querySelectorAll('.message.user');
      let target = null;
      candidates.forEach((el) => {
        const idAttr = el.getAttribute('data-message-id') || '';
        if (/^\d+$/.test(idAttr)) return;
        if (el.querySelector('.message-attachment-chips')) return;
        target = el;
      });
      if (target) this._appendCards(target, atts);
    });
  }

  _appendCards(host, atts) {
    const wrap = document.createElement('div');
    wrap.className = 'message-attachment-chips';
    atts.forEach((a) => wrap.appendChild(this._card(a, true)));
    // Same block as the message text (ChatGPT-style): inside .message-inner,
    // directly above .message-content — never at the band's outer edge.
    const inner = host.querySelector('.message-inner');
    const content = inner?.querySelector('.message-content');
    if (inner && content) {
      inner.insertBefore(wrap, content);
    } else {
      (inner || host).appendChild(wrap);
    }
  }

  _card(a, compact = false) {
    const card = document.createElement('div');
    card.className = 'attachment-card' + (compact ? ' attachment-card-compact' : '');
    if (a.attachment_id) card.dataset.attachmentId = a.attachment_id;
    // Compact chips live on already-sent messages: read-only, so no remove
    // button and no extraction-warning line.
    const warning = (!compact && a.attachment_id) ? this._warnings.get(a.attachment_id) : '';
    const warningLine = warning
      ? `<span class="attachment-card-warning" title="${this._esc(warning)}">${this._esc(warning)}</span>`
      : '';
    const removeBtn = compact ? '' : `
      <button class="attachment-card-remove" type="button"
              aria-label="Remove ${this._esc(a.filename)}">✕</button>`;
    card.innerHTML = `
      ${this._iconSvg(a)}
      <div class="attachment-card-info">
        <span class="attachment-card-name">${this._esc(a.filename)}</span>
        <span class="attachment-card-sub">${this._esc(this._subtitle(a))}</span>
        ${warningLine}
      </div>${removeBtn}`;
    if (!compact) {
      card.querySelector('.attachment-card-remove')
        .addEventListener('click', () => {
          if (a.attachment_id) {
            this.remove(a.attachment_id);
          } else {
            card.remove();                 // in-flight/error card: dismiss locally
            this._syncComposerVisibility();
          }
        });
    }
    return card;
  }

  _subtitle(a) {
    const bits = [];
    if (a.kind === 'bundle') bits.push('zip bundle');
    else if ((a.extension || '') === '.pdf') bits.push('PDF');
    if (a.size_bytes != null) bits.push(this._humanSize(a.size_bytes));
    return bits.join(' · ');
  }

  _humanSize(n) {
    n = Number(n) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  _iconSvg(a) {
    const stroke = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
    let body;
    if (a.kind === 'bundle') {
      body = `<path d="M12.89 1.45l8 4A2 2 0 0 1 22 7.24v9.53a2 2 0 0 1-1.11 1.79l-8 4a2 2 0 0 1-1.79 0l-8-4a2 2 0 0 1-1.1-1.8V7.24a2 2 0 0 1 1.11-1.79l8-4a2 2 0 0 1 1.78 0z"></path><polyline points="2.32 6.16 12 11 21.68 6.16"></polyline><line x1="12" y1="22.76" x2="12" y2="11"></line>`;
    } else if ((a.extension || '') === '.pdf') {
      body = `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line>`;
    } else {
      body = `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline>`;
    }
    return `<span class="attachment-card-icon"><svg width="14" height="14" viewBox="0 0 24 24" ${stroke} aria-hidden="true">${body}</svg></span>`;
  }

  _pendingCard(name, sizeBytes) {
    const card = this._card({ filename: name, size_bytes: sizeBytes });
    card.classList.add('attachment-card-pending');
    card.querySelector('.attachment-card-sub').textContent = 'Attaching…';
    this.composer.appendChild(card);
    this._syncComposerVisibility();
    return card;
  }

  _pendingError(name, message) {
    const card = this._pendingCard(name, null);
    this._cardToError(card, message);
  }

  _cardToError(card, message) {
    card.classList.remove('attachment-card-pending');
    card.classList.add('attachment-card-error');
    card.querySelector('.attachment-card-sub').textContent = message;
    this._announce(`Attachment failed: ${message}`);
    setTimeout(() => { card.remove(); this._syncComposerVisibility(); }, 8000);
  }

  _esc(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
}

window.Attachments = new AttachmentsManager();
