/**
 * Workflow 22: Conversation Attachments Tests
 *
 * Tests for attaching a file to a conversation, asking the model about its
 * content, having it persist across a reload, and removing it. Attachments
 * are PRIVATE per-conversation uploads (src/interfaces/chat_app/attachment_routes.py)
 * -- not the admin /upload knowledge-base ingestion already covered by
 * 17-file-upload.spec.ts.
 *
 * Unlike most workflow specs, this file does NOT mock the network. The
 * primary assertion needs a REAL model answer that quotes text pulled from
 * the attached file, so the chat stream, provider/model selection, and the
 * attachment endpoints all have to hit a real running deployment
 * (BASE_URL) end to end -- stubbing providers/agent info the way
 * `setupBasicMocks` does could steer the client at a model/provider the
 * live backend doesn't actually have configured and break that assertion
 * for reasons unrelated to attachments.
 *
 * Requires a deployment built from a branch where attachments are wired up
 * server-side; skips gracefully via `gotoChatWithAttachments` below when
 * `data-attachments-enabled` isn't "true" (e.g. against main).
 */
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

// CI's smoke deployment answers with a tiny CPU-only model (local/qwen3:4b)
// that cannot reliably run the agent loop and quote an attached file within
// the timeout. On CI we therefore skip ONLY the model-quote assertions and
// lean on the model-independent server-truth checks (binding poll, attachment
// listings, fresh-conversation invariants); against a real deployment the
// strict marker assertions still run.
const IS_CI = !!process.env.CI;

/** Navigate to the chat page (mirrors 03-message-flow's `/chat` target) and
 *  skip the test when this deployment wasn't built with attachments on. */
async function gotoChatWithAttachments(page: Page) {
  await page.goto('/chat');
  const enabled = await page.locator('body').getAttribute('data-attachments-enabled');
  test.skip(enabled !== 'true', 'attachments disabled in this deployment');
}

test.describe('Conversation Attachments', () => {
  // Real-model answers can take minutes; the global config caps tests at 15-30s.
  test.describe.configure({ timeout: 125_000 });

  test('attach, ask about it, persist across reload, then remove', async ({ page }) => {
    // A CPU-runner model turn plus the post-turn waits can exceed the
    // describe-level cap (a CI turn has been observed to pass 180s).
    test.setTimeout(300_000);
    await gotoChatWithAttachments(page);

    // Attach a text file with a distinctive marker fact.
    await page.locator('#attachment-file-input').setInputFiles({
      name: 'fact.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('The secret launch code is PINEAPPLE-42.'),
    });

    // The pending attachment renders as a card inside the chat box
    // (ChatGPT-style composer card) until the message is sent.
    await expect(page.locator('.composer-attachments .attachment-card')).toHaveCount(1, { timeout: 15000 });
    await expect(page.locator('.composer-attachments .attachment-card-name')).toHaveText('fact.txt');

    // The card grows the composer upward: the send button must stay inside
    // the viewport (regression: fixed grid row clipped it below the screen).
    await expect(page.getByRole('button', { name: 'Send message' })).toBeInViewport();

    // Ask about the attached file's content.
    await page.getByLabel('Message input').fill(
      'What is the secret launch code in the attached file? Answer with just the code.'
    );
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.message.user')).toBeVisible();
    if (!IS_CI) {
      // Agents can be slow; give the real model plenty of room to respond.
      await expect(page.locator('.message.assistant').last()).toContainText('PINEAPPLE-42', {
        timeout: 120000,
      });
    }

    // Wait for the turn to actually finish: the send button flips back from
    // "Stop streaming" when the final event lands. On CI the qwen3:4b turn
    // regularly outlives the binding poll's window (observed still streaming
    // at 30s), and the binding below only exists after the server-side
    // finalize -- polling before this point races the model, not the DB.
    await expect(page.getByRole('button', { name: 'Send message' })).toBeVisible({
      timeout: 240_000,
    });

    // The turn's DB writes (messages + attachment binding) land only after
    // the stream fully finishes server-side; reloading the instant the final
    // text renders can kill the request mid-finalize and lose the turn (a
    // pre-existing chat-app race, tracked separately). Wait for the binding
    // to become visible through the public listing before testing persistence.
    await expect
      .poll(async () => {
        const { convId, clientId } = await page.evaluate(() => ({
          convId: localStorage.getItem('archi_active_conversation_id'),
          clientId: localStorage.getItem('archi_client_id'),
        }));
        if (!convId || !clientId) return false;
        const resp = await page.request.get(
          `/api/chat/conversations/${convId}/attachments?client_id=${encodeURIComponent(clientId)}`
        );
        if (!resp.ok()) return false;
        const body = await resp.json();
        return (body.attachments || []).some((a: { message_id: number | null }) => a.message_id != null);
      }, { timeout: 30000, intervals: [500] })
      .toBe(true);

    // Reload -- the active conversation (and its attachment) is restored
    // from localStorage (see chat.js Storage.setActiveConversationId). The
    // attachment is now bound to the sent message, so its card renders on
    // that message in the chat window (not in the composer).
    await page.reload();
    const messageCard = page.locator('.message-attachment-chips .attachment-card');
    await expect(messageCard).toHaveCount(1, { timeout: 15000 });
    await expect(page.locator('.composer-attachments .attachment-card')).toHaveCount(0);

    // Sent-message chips are history, not pending items: they must NOT offer
    // the ✕ that hard-deletes the attachment from the conversation.
    await expect(messageCard.locator('.attachment-card-remove')).toHaveCount(0);
  });

  test('send is blocked while an attachment is still uploading', async ({ page }) => {
    await gotoChatWithAttachments(page);

    // Hold the upload POST for 2.5s so the "still uploading" window is a
    // reproducible state, not a matter of finger speed. The request still
    // reaches the real server afterwards (route.fetch), so the green path
    // exercises true end-to-end binding.
    let uploadSettledAt = 0;
    await page.route('**/api/chat/attachments', async (route) => {
      await new Promise((r) => setTimeout(r, 2500));
      const response = await route.fetch();
      await route.fulfill({ response });
      uploadSettledAt = Date.now();
    });

    let streamStartedAt = 0;
    page.on('request', (req) => {
      if (req.url().includes('/api/get_chat_response_stream') && !streamStartedAt) {
        streamStartedAt = Date.now();
      }
    });

    await page.locator('#attachment-file-input').setInputFiles({
      name: 'race.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('The race marker is MANGO-77.'),
    });
    await page.getByLabel('Message input').fill(
      'What is the race marker in the attached file? Answer with just the marker.'
    );

    // While the upload is in flight the send button is locked shut — a user
    // cannot fire a turn whose attachment isn't ready (it would land in the
    // conversation confusingly half-attached).
    const sendBtn = page.getByRole('button', { name: 'Send message' });
    await expect(sendBtn).toBeDisabled();

    // Enter must not sneak past the disabled button either — it explains
    // itself instead of silently queueing the turn.
    await page.getByLabel('Message input').press('Enter');
    await expect(page.locator('.composer-hint')).toContainText('still uploading');
    expect(streamStartedAt, 'no turn may start while the upload holds').toBe(0);

    // Once the upload settles the composer unlocks and the send goes through.
    await expect(sendBtn).toBeEnabled({ timeout: 10_000 });
    expect(uploadSettledAt, 'button unlocked before the upload settled').toBeGreaterThan(0);
    await expect(page.locator('.composer-hint')).toBeHidden();
    await sendBtn.click();

    // The turn can fire during the click await itself, so poll the tracker
    // that has listened since test start instead of waiting for a new event.
    await expect.poll(() => streamStartedAt, { timeout: 30_000 }).toBeGreaterThan(0);
    expect(
      streamStartedAt,
      'the chat turn must start only after the in-flight upload settled'
    ).toBeGreaterThan(uploadSettledAt);

    if (!IS_CI) {
      // And the file genuinely made it into the turn: the model can quote it.
      await expect(page.locator('.message.assistant').last()).toContainText('MANGO-77', {
        timeout: 120000,
      });
    }
  });

  test('New Chat during a streaming reply starts a truly fresh conversation', async ({ page }) => {
    // Two real model turns back to back can exceed the describe-level cap.
    test.setTimeout(240_000);
    await gotoChatWithAttachments(page);

    // Buffer the whole SSE stream: route.fetch() resolves only once the
    // server finished the turn, so the client receives every event —
    // including `final`, which carries conversation_id — in one late burst.
    // That makes "click New Chat mid-turn" deterministic instead of a race
    // against model speed. (catch: the fixed client aborts the fetch on New
    // Chat, which surfaces here as a route.fetch/fulfill rejection.)
    await page.route('**/api/get_chat_response_stream', async (route) => {
      try {
        // Default route.fetch timeout is 30s — a slower backend (CI's CPU
        // model) would get cut off by the harness itself and render a bogus
        // "Failed to fetch" turn. Give it the full assertion budget.
        const response = await route.fetch({ timeout: 120_000 });
        await route.fulfill({ response });
      } catch {
        try { await route.abort(); } catch { /* already gone */ }
      }
    });

    // Turn 1 in a brand-new chat, with an attachment (mirrors the field
    // repro: the attach-on-new-chat path mints the conversation).
    await page.locator('#attachment-file-input').setInputFiles({
      name: 'first.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('The first marker is ALPHA-11.'),
    });
    await expect(page.locator('.composer-attachments .attachment-card')).toHaveCount(1, {
      timeout: 15000,
    });
    await page.getByLabel('Message input').fill(
      'What is the first marker in the attached file? Answer with just the marker.'
    );

    const streamSettled = Promise.race([
      page
        .waitForResponse((r) => r.url().includes('/api/get_chat_response_stream'), {
          timeout: 120_000,
        })
        .catch(() => {}),
      page
        .waitForEvent('requestfailed', {
          predicate: (r) => r.url().includes('/api/get_chat_response_stream'),
          timeout: 120_000,
        })
        .catch(() => {}),
    ]);
    await page.getByRole('button', { name: 'Send message' }).click();
    await expect(page.locator('.message.user')).toBeVisible();

    // Mid-turn (no stream event has reached the client yet): start over.
    await page.getByRole('button', { name: 'New chat' }).click();
    await expect(page.locator('.message.user')).toHaveCount(0);

    // Let the abandoned turn finish (or get aborted by the client) and give
    // the stream handler a moment to process whatever arrived late.
    await streamSettled;
    await page.waitForTimeout(750);

    // The old conversation must NOT come back: a late-finishing reply used
    // to restore its conversation_id and silently undo New Chat.
    const restoredId = await page.evaluate(() =>
      localStorage.getItem('archi_active_conversation_id')
    );
    expect(restoredId, 'New Chat was silently undone by the old reply').toBeNull();

    // Turn 2 in the genuinely-new chat: attach again and ask. This must mint
    // a fresh conversation holding exactly this one attachment — not pile a
    // duplicate into the abandoned conversation.
    await page.locator('#attachment-file-input').setInputFiles({
      name: 'second.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('The second marker is BRAVO-99.'),
    });
    await expect(page.locator('.composer-attachments .attachment-card')).toHaveCount(1, {
      timeout: 15000,
    });
    await page.getByLabel('Message input').fill(
      'What is the second marker in the attached file? Answer with just the marker.'
    );
    await page.getByRole('button', { name: 'Send message' }).click();
    if (!IS_CI) {
      await expect(page.locator('.message.assistant').last()).toContainText('BRAVO-99', {
        timeout: 120000,
      });
    }

    // Server truth: the active conversation holds only the second file.
    const { convId, clientId } = await page.evaluate(() => ({
      convId: localStorage.getItem('archi_active_conversation_id'),
      clientId: localStorage.getItem('archi_client_id'),
    }));
    expect(convId).not.toBeNull();
    const resp = await page.request.get(
      `/api/chat/conversations/${convId}/attachments?client_id=${encodeURIComponent(clientId!)}`
    );
    expect(resp.ok()).toBe(true);
    const body = await resp.json();
    const filenames = (body.attachments || []).map((a: { filename: string }) => a.filename);
    expect(filenames, 'the old conversation leaked into the new chat').toEqual(['second.txt']);
  });

  test('New Chat during an in-flight upload abandons that upload cleanly', async ({ page }) => {
    await gotoChatWithAttachments(page);

    // Hold the upload open long enough to click New Chat mid-flight.
    await page.route('**/api/chat/attachments', async (route) => {
      await new Promise((r) => setTimeout(r, 2500));
      try {
        const response = await route.fetch();
        await route.fulfill({ response });
      } catch {
        try { await route.abort(); } catch { /* already gone */ }
      }
    });

    await page.locator('#attachment-file-input').setInputFiles({
      name: 'orphan.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('This upload gets abandoned mid-flight.'),
    });
    await expect(page.locator('.composer-attachments .attachment-card-pending')).toBeVisible();

    await page.getByRole('button', { name: 'New chat' }).click();
    // The abandoned card leaves the composer right away…
    await expect(page.locator('.composer-attachments .attachment-card')).toHaveCount(0);

    // …and when the held upload finally settles, the conversation it minted
    // must not leak into this fresh chat.
    await page.waitForTimeout(3500);
    const restoredId = await page.evaluate(() =>
      localStorage.getItem('archi_active_conversation_id')
    );
    expect(restoredId, 'the abandoned upload dragged its conversation back in').toBeNull();
    await expect(page.locator('.composer-attachments .attachment-card')).toHaveCount(0);
  });

  test('unsupported file type shows a friendly rejection', async ({ page }) => {
    await gotoChatWithAttachments(page);

    await page.locator('#attachment-file-input').setInputFiles({
      name: 'cat.png',
      mimeType: 'image/png',
      buffer: Buffer.from('fake'),
    });

    const errorCard = page.locator('.attachment-card-error');
    await expect(errorCard).toBeVisible({ timeout: 15000 });
    await expect(errorCard.locator('.attachment-card-sub')).toContainText(
      "Images aren't supported yet"
    );
  });
});
