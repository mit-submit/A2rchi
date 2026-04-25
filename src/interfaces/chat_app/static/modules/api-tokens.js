(function () {
  const tbody = document.getElementById('tokens-tbody');
  const form = document.getElementById('create-form');
  const nameInput = document.getElementById('token-name');
  const ttlSelect = document.getElementById('token-ttl');
  const modal = document.getElementById('token-modal');
  const tokenBox = document.getElementById('token-plaintext');
  const copyBtn = document.getElementById('copy-btn');
  const closeBtn = document.getElementById('close-modal-btn');

  function fmt(ts) {
    if (!ts) return '—';
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
  }

  function fmtExpiry(ts) {
    if (!ts) return 'Never';
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    const expired = d.getTime() < Date.now();
    return expired
      ? `<span style="color: var(--danger-color);">${d.toLocaleString()} (expired)</span>`
      : d.toLocaleString();
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  async function loadTokens() {
    const res = await fetch('/api/admin/api-tokens', { credentials: 'same-origin' });
    if (!res.ok) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-state">Failed to load tokens (HTTP ${res.status}).</td></tr>`;
      return;
    }
    const data = await res.json();
    const tokens = data.tokens || [];
    if (tokens.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No tokens yet. Create one above.</td></tr>';
      return;
    }
    tbody.innerHTML = tokens.map(t => {
      const revoked = Boolean(t.revoked_at);
      const expired = t.expires_at && new Date(t.expires_at).getTime() < Date.now();
      let statusHtml;
      if (revoked) statusHtml = '<span class="status-pill status-revoked">Revoked</span>';
      else if (expired) statusHtml = '<span class="status-pill status-revoked">Expired</span>';
      else statusHtml = '<span class="status-pill status-active">Active</span>';
      const actionHtml = revoked
        ? ''
        : `<button class="btn-danger" data-revoke="${escapeHtml(t.id)}" data-name="${escapeHtml(t.name)}">Revoke</button>`;
      return `
        <tr class="${(revoked || expired) ? 'revoked' : ''}">
          <td>${escapeHtml(t.name)}</td>
          <td class="mono">${escapeHtml(t.user_email || t.user_id)}</td>
          <td>${fmt(t.created_at)}</td>
          <td>${fmtExpiry(t.expires_at)}</td>
          <td>${fmt(t.last_used_at)}</td>
          <td>${statusHtml}</td>
          <td>${actionHtml}</td>
        </tr>
      `;
    }).join('');
  }

  async function createToken(name, ttlDays) {
    const body = { name };
    // Empty string -> Never expires; numeric string -> integer days
    if (ttlDays !== '' && ttlDays !== null && ttlDays !== undefined) {
      body.ttl_days = Number(ttlDays);
    } else {
      body.ttl_days = null;
    }
    const res = await fetch('/api/admin/api-tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.message || data.error || `HTTP ${res.status}`);
    }
    return data;
  }

  async function revokeToken(id) {
    const res = await fetch(`/api/admin/api-tokens/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      credentials: 'same-origin',
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.message || body.error || `HTTP ${res.status}`);
    }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = nameInput.value.trim();
    if (!name) return;
    const ttl = ttlSelect ? ttlSelect.value : '';
    try {
      const result = await createToken(name, ttl);
      tokenBox.textContent = result.token;
      modal.classList.add('show');
      nameInput.value = '';
      if (ttlSelect) ttlSelect.value = '90';
      await loadTokens();
    } catch (err) {
      alert(`Failed to create token: ${err.message}`);
    }
  });

  tbody.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-revoke]');
    if (!btn) return;
    const id = btn.getAttribute('data-revoke');
    const name = btn.getAttribute('data-name');
    if (!confirm(`Revoke token "${name}"? This cannot be undone.`)) return;
    try {
      await revokeToken(id);
      await loadTokens();
    } catch (err) {
      alert(`Failed to revoke: ${err.message}`);
    }
  });

  copyBtn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(tokenBox.textContent);
      copyBtn.textContent = 'Copied!';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
    } catch {
      const range = document.createRange();
      range.selectNode(tokenBox);
      window.getSelection().removeAllRanges();
      window.getSelection().addRange(range);
    }
  });

  closeBtn.addEventListener('click', () => {
    modal.classList.remove('show');
    tokenBox.textContent = '';
  });

  document.addEventListener('DOMContentLoaded', loadTokens);
})();
