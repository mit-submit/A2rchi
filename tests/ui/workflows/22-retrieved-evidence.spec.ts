/**
 * Workflow 22: Retrieved Context Evidence Tests
 *
 * Tests the retrieved evidence drawer rendered under assistant messages.
 */
import { test, expect, Page } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { setupBasicMocks } from '../fixtures';

const repoRoot = path.resolve(__dirname, '../../..');
const chatTemplatePath = path.join(repoRoot, 'src/interfaces/chat_app/templates/index.html');
const staticRoot = path.join(repoRoot, 'src/interfaces/chat_app/static');

async function setupChatPageShell(page: Page) {
  await page.route('**/chat', async (route) => {
    let body = fs.readFileSync(chatTemplatePath, 'utf8');
    body = body
      .replace("{% include '_alert_banner.html' %}", '')
      .replaceAll("{{ url_for('static', filename='chat.css') }}", '/static/chat.css')
      .replaceAll("{{ url_for('static', filename='chat.js') }}", '/static/chat.js')
      .replaceAll("{{ url_for('static', filename='modules/theme-init.js') }}", '/static/modules/theme-init.js')
      .replaceAll("{{ url_for('static', filename='images/archi_notext.png') }}", '/static/images/archi_notext.png')
      .replaceAll("{{ url_for('static', filename='images/archi-logo.png') }}", '/static/images/archi-logo.png');
    await route.fulfill({ status: 200, contentType: 'text/html', body });
  });

  await page.route('**/static/**', async (route) => {
    const url = new URL(route.request().url());
    const staticPath = path.normalize(path.join(staticRoot, url.pathname.replace(/^\/static\//, '')));
    if (!staticPath.startsWith(staticRoot) || !fs.existsSync(staticPath)) {
      await route.fulfill({ status: 404, body: '' });
      return;
    }
    await route.fulfill({ path: staticPath });
  });
}

function evidencePayload() {
  return {
    items: [
      {
        id: 'ev_1',
        kind: 'text',
        source: {
          document_id: 7,
          display_name: 'runbook.pdf',
          mime_type: 'application/pdf',
        },
        page: { page_number: 3 },
        retrieved_unit: { chunk_index: 0 },
        excerpt: 'Restart the worker process before retrying ingestion.',
        score: 0.9876,
        rank: 1,
        preview: { type: 'text', available: true },
        actions: {
          preview_url: '/api/evidence/ev_1/preview',
          download_url: '/api/evidence/ev_1/download',
        },
      },
      {
        id: 'ev_2',
        kind: 'text',
        source: {
          document_id: 7,
          display_name: 'runbook.pdf',
          mime_type: 'application/pdf',
        },
        page: { page_number: 4 },
        retrieved_unit: { chunk_index: 1 },
        excerpt: 'Check the data manager logs after the restart completes.',
        score: 0.8765,
        rank: 2,
        preview: { type: 'text', available: true },
        actions: {
          preview_url: '/api/evidence/ev_2/preview',
          download_url: '/api/evidence/ev_2/download',
        },
      },
    ],
    groups: [
      {
        resource_hash: 'doc-hash',
        display_name: 'runbook.pdf',
        items: [
          {
            id: 'ev_1',
            kind: 'text',
            source: {
              document_id: 7,
              display_name: 'runbook.pdf',
              mime_type: 'application/pdf',
            },
            page: { page_number: 3 },
            retrieved_unit: { chunk_index: 0 },
            excerpt: 'Restart the worker process before retrying ingestion.',
            score: 0.9876,
            rank: 1,
            preview: { type: 'text', available: true },
            actions: {
              preview_url: '/api/evidence/ev_1/preview',
              download_url: '/api/evidence/ev_1/download',
            },
          },
          {
            id: 'ev_2',
            kind: 'text',
            source: {
              document_id: 7,
              display_name: 'runbook.pdf',
              mime_type: 'application/pdf',
            },
            page: { page_number: 4 },
            retrieved_unit: { chunk_index: 1 },
            excerpt: 'Check the data manager logs after the restart completes.',
            score: 0.8765,
            rank: 2,
            preview: { type: 'text', available: true },
            actions: {
              preview_url: '/api/evidence/ev_2/preview',
              download_url: '/api/evidence/ev_2/download',
            },
          },
        ],
      },
    ],
  };
}

function visualEvidencePayload() {
  const item = {
    id: 'ev_image',
    kind: 'image',
    source: {
      document_id: 8,
      display_name: 'very-long-unbroken-filename-that-should-wrap-in-the-evidence-panel.png',
      mime_type: 'image/png',
    },
    page: { page_number: 1 },
    retrieved_unit: { id: 'image' },
    excerpt: 'A caption that should not be rendered beside the visual preview.',
    score: 0.9123,
    preview: { type: 'image', available: true },
    actions: {
      preview_url: '/api/evidence/ev_image/preview',
      download_url: '/api/evidence/ev_image/download',
    },
  };
  return {
    items: [item],
    groups: [
      {
        resource_hash: 'image-hash',
        display_name: item.source.display_name,
        items: [item],
      },
    ],
  };
}

async function mockPreviewEndpoint(page: Page, options: { unavailable?: boolean } = {}) {
  let previewRequested = false;
  await page.route('**/api/evidence/*/preview**', async (route) => {
    previewRequested = true;
    if (options.unavailable) {
      await route.fulfill({
        status: 200,
        json: { type: 'unavailable', reason: 'Preview unavailable for this file type.' },
      });
      return;
    }
    await route.fulfill({
      status: 200,
      json: {
        type: 'text',
        excerpt: 'Restart the worker process before retrying ingestion.',
      },
    });
  });
  return () => previewRequested;
}

async function sendMessageWithEvidence(
  page: Page,
  evidence = evidencePayload(),
  response = 'Use the runbook restart sequence.'
) {
  await page.route('**/api/get_chat_response_stream', async (route) => {
    const body = [
      JSON.stringify({
        type: 'final',
        response,
        message_id: 11,
        user_message_id: 10,
        conversation_id: 1,
        trace_id: 'trace-123',
        evidence,
      }),
    ].join('\n') + '\n';
    await route.fulfill({ status: 200, contentType: 'text/plain', body });
  });

  await page.goto('/chat');
  await page.getByLabel('Message input').fill('How do I recover ingestion?');
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByText('Use the runbook restart sequence.')).toBeVisible({ timeout: 5000 });
}

test.describe('Retrieved Context Evidence', () => {
  test.beforeEach(async ({ page }) => {
    await setupChatPageShell(page);
    await setupBasicMocks(page);
  });

  test('streamed answer renders a collapsed grouped evidence drawer', async ({ page }) => {
    await mockPreviewEndpoint(page);
    await sendMessageWithEvidence(page);

    const drawer = page.locator('.retrieved-evidence');
    await expect(drawer).toBeVisible();
    await expect(drawer).not.toHaveAttribute('open', /.+/);
    await expect(drawer.locator('summary')).toContainText('Retrieved documents (1)');

    await drawer.locator('summary').click();
    await expect(drawer.locator('.evidence-group-title')).toContainText('runbook.pdf');
    await expect(drawer.locator('.evidence-item')).toHaveCount(2);
    await expect(drawer.locator('.evidence-item').first()).toContainText('Page 3');
    await expect(drawer.locator('.evidence-item').first()).toContainText('0.988');
  });

  test('text evidence rows do not fall back to the source filename as a chunk label', async ({ page }) => {
    const payload = evidencePayload();
    delete payload.groups[0].items[1].retrieved_unit.chunk_index;
    payload.groups[0].items[1].page = null;
    delete payload.items[1].retrieved_unit.chunk_index;
    payload.items[1].page = null;
    await mockPreviewEndpoint(page);
    await sendMessageWithEvidence(page, payload);

    const drawer = page.locator('.retrieved-evidence');
    await drawer.locator('summary').click();

    const labels = await drawer.locator('.evidence-item > span').allTextContents();
    expect(labels).toEqual(['Page 3', 'Retrieved chunk 2']);
  });

  test('preview is fetched only after a retrieved item is selected', async ({ page }) => {
    const previewRequested = await mockPreviewEndpoint(page);
    await sendMessageWithEvidence(page);

    const drawer = page.locator('.retrieved-evidence');
    await drawer.locator('summary').click();
    expect(previewRequested()).toBe(false);

    await drawer.locator('.evidence-item').first().click();
    await expect(drawer.locator('.evidence-preview-text')).toContainText('Restart the worker process');
    expect(previewRequested()).toBe(true);
  });

  test('visual previews do not render text excerpts beside the image or PDF page', async ({ page }) => {
    let previewRequested = false;
    await page.route('**/api/evidence/*/preview**', async (route) => {
      previewRequested = true;
      await route.fulfill({
        status: 200,
        body: Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
          'base64'
        ),
        contentType: 'image/png',
      });
    });
    await sendMessageWithEvidence(page, visualEvidencePayload());

    const drawer = page.locator('.retrieved-evidence');
    await drawer.locator('summary').click();
    await drawer.locator('.evidence-item').first().click();

    await expect(drawer.locator('.evidence-preview-image')).toBeVisible();
    await expect(drawer.locator('.evidence-preview-text')).toHaveCount(0);
    const titleFits = await drawer.locator('.evidence-group-title').evaluate((el) => el.scrollWidth <= el.clientWidth);
    expect(titleFits).toBe(true);
    expect(previewRequested).toBe(true);
  });

  test('structured evidence replaces the legacy retrieved documents dropdown', async ({ page }) => {
    await mockPreviewEndpoint(page);
    await sendMessageWithEvidence(
      page,
      evidencePayload(),
      [
        'Use the runbook restart sequence.',
        '',
        '---',
        '*These are the knowledge-base documents retrieved for this answer.*',
        '',
        '<details><summary><strong>Retrieved documents (1)</strong></summary>',
        '',
        '- legacy source',
        '',
        '</details>',
      ].join('\n')
    );

    await expect(page.locator('.message.assistant details')).toHaveCount(1);
    await expect(page.locator('.retrieved-evidence summary')).toContainText('Retrieved documents (1)');
    await expect(page.getByText('legacy source')).toHaveCount(0);
  });

  test('unavailable text previews show a clear status and retained excerpt', async ({ page }) => {
    await mockPreviewEndpoint(page, { unavailable: true });
    await sendMessageWithEvidence(page);

    const drawer = page.locator('.retrieved-evidence');
    await drawer.locator('summary').click();
    await drawer.locator('.evidence-item').first().click();

    await expect(drawer.locator('.evidence-preview-unavailable')).toContainText('Preview unavailable');
    await expect(drawer.locator('.evidence-preview-text')).toContainText('Restart the worker process');
  });

  test('historical conversation renders evidence from trace events', async ({ page }) => {
    await mockPreviewEndpoint(page);
    await page.route('**/api/load_conversation', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          messages: [
            { message_id: 1, sender: 'User', content: 'How do I recover ingestion?' },
            {
              message_id: 2,
              sender: 'archi',
              content: 'Use the runbook restart sequence.',
              model_used: 'test-model',
              trace: {
                trace_id: 'trace-historical',
                events: [
                  {
                    type: 'retrieved_evidence',
                    trace_id: 'trace-historical',
                    evidence: evidencePayload(),
                  },
                ],
              },
            },
          ],
          pending_ab_comparisons: [],
        },
      });
    });

    await page.goto('/chat');
    await page.locator('.conversation-item').first().click();

    const drawer = page.locator('.retrieved-evidence');
    await expect(drawer).toBeVisible({ timeout: 5000 });
    await expect(drawer.locator('summary')).toContainText('Retrieved documents (1)');
    await drawer.locator('summary').click();
    await expect(drawer.locator('.evidence-group-title')).toContainText('runbook.pdf');
  });
});
