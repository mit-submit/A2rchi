/**
 * Workflow 21: A/B Testing (Pool-based)
 *
 * Tests for the dedicated A/B admin page, pool management, streaming
 * comparison, vote buttons, preference submission, and metrics.
 */
import {
  test,
  expect,
  setupBasicMocks,
  setupABAdminPageBootstrap,
  setupABAdminMocks,
  setupABAdminInactiveMocks,
  setupABDecisionMock,
  mockData,
  createABStreamResponse,
} from '../fixtures';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function openABAdminPage(page: import('@playwright/test').Page) {
  const poolLoaded = page.waitForResponse((response) =>
    response.url().includes('/api/ab/pool') && response.request().method() === 'GET',
  );
  const providersLoaded = page.waitForResponse((response) =>
    response.url().includes('/api/providers') && response.request().method() === 'GET',
  );
  const agentsLoaded = page.waitForResponse((response) =>
    response.url().includes('/api/ab/agents/list') && response.request().method() === 'GET',
  );
  const metricsLoaded = page.waitForResponse((response) =>
    response.url().includes('/api/ab/metrics') && response.request().method() === 'GET',
  );
  await page.goto('/admin/ab-testing');
  await poolLoaded;
  await providersLoaded;
  await agentsLoaded;
  await metricsLoaded;
  await expect(page.locator('#ab-admin-status')).toBeVisible();
}

async function openChatPage(page: import('@playwright/test').Page) {
  const abPoolLoaded = page.waitForResponse((response) =>
    response.url().includes('/api/ab/pool') && response.request().method() === 'GET',
  );
  await page.goto('/chat');
  await expect(page.getByLabel('Message input')).toBeVisible();
  await abPoolLoaded;
  await expect(page.locator('.conversation-item[data-id]').first()).toBeVisible();
}

async function waitForABPlaywrightHook(page: import('@playwright/test').Page) {
  await page.waitForFunction(() => typeof (window as any).__ARCHI_PLAYWRIGHT__?.ab !== 'undefined');
}

async function enableVisibleABTraceMode(page: import('@playwright/test').Page) {
  await waitForABPlaywrightHook(page);
  await page.evaluate(() => {
    (window as any).__ARCHI_PLAYWRIGHT__.ab.patchPoolState({
      enabled: true,
      activity_panel_default_state: 'collapsed',
    });
  });
}

async function loadConversationFromSidebar(page: import('@playwright/test').Page, conversationId: number) {
  const conversation = page.locator(`.conversation-item[data-id="${conversationId}"]`);
  await expect(conversation).toBeVisible();
  await conversation.click();
}

// =============================================================================
// Admin gating -- chat settings link visibility
// =============================================================================

test.describe('A/B Management Entry Point -- Admin Gating', () => {

  test('chat settings section stays hidden for non-admin users', async ({ page }) => {
    await setupBasicMocks(page);
    await page.goto('/chat');
    await page.waitForTimeout(500);
    const display = await page.locator('#ab-settings-section').evaluate(
      (el: HTMLElement) => el.style.display,
    );
    expect(display).toBe('none');
  });

  test('chat settings shows admin link for admin users', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openChatPage(page);
    await page.getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByRole('button', { name: 'A/B Testing' })).toBeVisible();
    await page.getByRole('button', { name: 'A/B Testing' }).click();
    await expect(page.locator('#ab-settings-section')).toBeVisible();
    await expect(page.locator('#ab-settings-section .settings-link-btn')).toHaveAttribute('href', '/admin/ab-testing');
  });

  test('participant-only users see sampling controls without admin link', async ({ page }) => {
    await setupBasicMocks(page);

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          success: true,
          enabled: true,
          enabled_requested: true,
          is_admin: false,
          can_view: false,
          can_manage: false,
          can_view_metrics: false,
          can_participate: true,
          participant_eligible: true,
          participant_reason: 'eligible',
          participant_targeted: true,
          comparison_rate: 0.7,
          default_comparison_rate: 0.5,
          variant_label_mode: 'post_vote_reveal',
          activity_panel_default_state: 'hidden',
          max_pending_comparisons_per_conversation: 1,
        },
      });
    });

    await page.goto('/chat');
    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByRole('button', { name: 'A/B Testing' }).click();

    await expect(page.locator('#ab-participation-group')).toBeVisible();
    await expect(page.locator('#ab-settings-section')).toBeHidden();
  });

  test('untargeted participants see an explanatory settings note', async ({ page }) => {
    await setupBasicMocks(page);

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          success: true,
          enabled: false,
          enabled_requested: true,
          is_admin: false,
          can_view: false,
          can_manage: false,
          can_view_metrics: false,
          can_participate: true,
          participant_eligible: false,
          participant_reason: 'not_targeted',
          participant_targeted: false,
          comparison_rate: 0.5,
          default_comparison_rate: 0.5,
        },
      });
    });

    await page.goto('/chat');
    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByRole('button', { name: 'A/B Testing' }).click();

    await expect(page.locator('#ab-participation-inactive')).toContainText('does not target your role or permissions');
  });

  test('dedicated admin page loads for admin users', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-status')).toHaveText('Active');
  });

  test('dedicated admin page shows Inactive when pool is disabled', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminInactiveMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-status')).toHaveText('Inactive');
  });

  test('data viewer A/B link is visible for read-only viewers and opens the same page', async ({ page }) => {
    await setupBasicMocks(page);

    await page.route('**/data', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: `<!DOCTYPE html><html><body><a href="/admin/ab-testing" class="ab-admin-nav-btn">A/B Testing</a></body></html>`,
      });
    });
    await page.route('**/admin/ab-testing', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: `<!DOCTYPE html><html><body data-can-manage-ab-testing="false" data-can-view-ab-metrics="true"><h1>A/B Testing</h1></body></html>`,
      });
    });

    await page.goto('/data');
    await page.getByRole('link', { name: 'A/B Testing' }).click();
    await expect(page.getByRole('heading', { name: 'A/B Testing' })).toBeVisible();
  });
});

// =============================================================================
// Dedicated page -- variant rendering
// =============================================================================

test.describe('A/B Admin Page -- Variant List', () => {

  test('renders existing variants and their parameters', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    const cards = page.locator('.ab-variant-card');
    await expect(cards).toHaveCount(mockData.abPoolAdmin.variant_details!.length);
    await expect(cards.first().locator('[data-field="label"]')).toHaveValue('Baseline');
    await expect(cards.first().locator('[data-field="agent_spec"]')).toHaveValue('baseline-ab.md');
  });

  test('champion select is pre-populated', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-champion')).toHaveValue(mockData.abPoolAdmin.champion!);
  });

  test('agent spec selector exposes available A/B catalog entries', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    const options = page.locator('.ab-variant-card').first().locator('[data-field="agent_spec"] option');
    await expect(options).toHaveCount(mockData.abAgentsList.agents.length + 2);
  });

  test('agent spec selector can create a new database-backed A/B agent inline', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    let createdPayload: any = null;
    await page.route('**/api/ab/agents', async (route) => {
      createdPayload = route.request().postDataJSON();
      await route.fallback();
    });
    await openABAdminPage(page);

    const secondCard = page.locator('.ab-variant-card').nth(1);
    await secondCard.locator('[data-field="agent_spec"]').selectOption('__create_new__');

    await expect(page.locator('#ab-agent-modal')).toBeVisible();
    await expect(page.locator('#ab-agent-tools-list')).toContainText('search_docs');
    await expect(page.locator('#ab-agent-tools-list')).toContainText('fetch_ticket');
    await page.locator('#ab-agent-name').fill('Fresh AB Agent');
    await page.locator('#ab-agent-prompt').fill('You are a freshly created A/B-only agent.');
    await page.locator('#ab-agent-save').click();

    await expect(page.locator('#ab-agent-modal')).toBeHidden();
    await expect(page.locator('.ab-variant-card').nth(1).locator('[data-field="agent_spec"]')).toHaveValue('fresh-ab-agent.md');
    expect(createdPayload).toMatchObject({
      name: 'Fresh AB Agent',
      prompt: 'You are a freshly created A/B-only agent.',
      tools: ['search_docs', 'fetch_ticket'],
    });
  });

  test('provider selector uses provider dropdown options', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    const providerOptions = page.locator('.ab-variant-card').nth(1).locator('[data-field="provider"] option');
    await expect(providerOptions).toHaveCount(mockData.providers.providers.length + 1);
  });
});

// =============================================================================
// Dedicated page -- save / disable interactions
// =============================================================================

test.describe('A/B Admin Page -- Save and Disable', () => {

  test('save button is enabled when champion + 2+ variants are configured', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-save')).toBeEnabled();
  });

  test('save button is disabled when fewer than 2 variants exist', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminInactiveMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-save')).toBeDisabled();
  });

  test('disable button visible when pool is active', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-disable')).toBeVisible();
  });

  test('disable button hidden when pool is inactive', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminInactiveMocks(page);
    await openABAdminPage(page);
    await expect(page.locator('#ab-admin-disable')).toBeHidden();
  });

  test('clicking save sends correct payload', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    let savedPayload: any = null;
    await page.route('**/api/ab/pool/settings/set', async (route) => {
      const body = route.request().postDataJSON();
      savedPayload = body;
      await route.fulfill({ status: 200, json: { success: true, ...mockData.abPoolAdmin } });
    });

    await openABAdminPage(page);
    await page.locator('#ab-admin-save').click();

    await page.waitForTimeout(300);
    expect(savedPayload).toBeTruthy();
    expect(savedPayload.champion).toBe(mockData.abPoolAdmin.champion);
    expect(savedPayload).not.toHaveProperty('variants');
    expect(savedPayload.comparison_rate).toBe(mockData.abPoolAdmin.comparison_rate);
  });

  test('settings save keeps backend-confirmed values visible without relying on a follow-up GET', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminPageBootstrap(page);

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({ status: 200, json: mockData.abPoolAdmin });
    });
    await page.route('**/api/ab/pool/settings/set', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          ...mockData.abPoolAdmin,
          comparison_rate: 0.4,
          variant_label_mode: 'hidden',
          activity_panel_default_state: 'expanded',
        },
      });
    });

    await openABAdminPage(page);
    await page.locator('#ab-admin-sample-rate').fill('0.4');
    await page.locator('#ab-admin-disclosure-mode').selectOption('hidden');
    await page.locator('#ab-admin-trace-mode').selectOption('expanded');
    await page.locator('#ab-admin-save').click();

    await expect(page.locator('#ab-admin-sample-rate')).toHaveValue('0.4');
    await expect(page.locator('#ab-admin-disclosure-mode')).toHaveValue('hidden');
    await expect(page.locator('#ab-admin-trace-mode')).toHaveValue('expanded');
  });

  test('clicking save variants sends only variant details', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    let savedPayload: any = null;
    await page.route('**/api/ab/pool/variants/set', async (route) => {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({ status: 200, json: { success: true, ...mockData.abPoolAdmin } });
    });

    await openABAdminPage(page);
    await page.locator('#ab-admin-variant-save').click();

    await page.waitForTimeout(300);
    expect(savedPayload).toBeTruthy();
    expect(savedPayload).toHaveProperty('variants');
    expect(savedPayload).not.toHaveProperty('comparison_rate');
    expect(savedPayload.variants).toEqual(expect.arrayContaining([
      expect.objectContaining({
        label: 'Baseline',
        agent_spec: 'baseline-ab.md',
        provider: null,
        model: null,
        recursion_limit: null,
        num_documents_to_retrieve: null,
      }),
      expect.objectContaining({
        label: 'Poet',
        agent_spec: 'poet-ab.md',
        provider: 'openrouter',
        model: 'anthropic/claude-3.5-sonnet',
        recursion_limit: null,
        num_documents_to_retrieve: null,
      }),
    ]));
  });

  test('variant save keeps backend-confirmed selections visible without relying on a follow-up GET', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminPageBootstrap(page);

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({ status: 200, json: mockData.abPoolAdmin });
    });
    await page.route('**/api/ab/pool/variants/set', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          ...mockData.abPoolAdmin,
          variant_details: [
            { label: 'Baseline', agent_spec: 'baseline-ab.md' },
            { label: 'Poet', agent_spec: 'poet-ab.md', provider: 'openrouter', model: 'anthropic/claude-3.5-sonnet' },
          ],
          variants: ['Baseline', 'Poet'],
        },
      });
    });

    await openABAdminPage(page);
    const secondCard = page.locator('.ab-variant-card').nth(1);
    await secondCard.locator('[data-field="label"]').fill('Poet');
    await secondCard.locator('[data-field="agent_spec"]').selectOption('poet-ab.md');
    await secondCard.locator('[data-field="provider"]').selectOption('openrouter');
    await secondCard.locator('[data-field="model_select"]').selectOption('anthropic/claude-3.5-sonnet');
    await page.locator('#ab-admin-variant-save').click();

    await expect(secondCard.locator('[data-field="label"]')).toHaveValue('Poet');
    await expect(secondCard.locator('[data-field="agent_spec"]')).toHaveValue('poet-ab.md');
    await expect(secondCard.locator('[data-field="provider"]')).toHaveValue('openrouter');
    await expect(secondCard.locator('[data-field="model_select"]')).toHaveValue('anthropic/claude-3.5-sonnet');
  });

  test('clicking disable calls endpoint and updates UI', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminPageBootstrap(page);
    let poolState = { ...mockData.abPoolAdmin };
    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({ status: 200, json: poolState });
    });

    let disableCalled = false;
    await page.route('**/api/ab/pool/disable', async (route) => {
      disableCalled = true;
      poolState = { ...mockData.abPoolAdmin, enabled: false, enabled_requested: false };
      await route.fulfill({ status: 200, json: { success: true, ...poolState } });
    });

    await openABAdminPage(page);
    await page.locator('#ab-admin-disable').click();

    await expect(page.locator('#ab-admin-status')).toHaveText('Inactive');
    expect(disableCalled).toBe(true);
  });

  test('validation message when fewer than 2 variants remain', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('.ab-variant-remove').nth(1).click();

    await expect(page.locator('#ab-admin-variant-message')).toContainText('Add at least 2 variants');
  });

  test('settings save ignores unsaved variant label edits', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('.ab-variant-card').first().locator('[data-field="label"]').fill('Renamed baseline');

    await expect(page.locator('#ab-admin-save')).toBeEnabled();
    await expect(page.locator('#ab-admin-champion')).toHaveValue('Baseline');
  });

  test('unsaved draft is restored after navigation', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminInactiveMocks(page);
    await openABAdminPage(page);

    await page.locator('#ab-admin-add-variant').click();
    await page.locator('.ab-variant-card').first().locator('[data-field="label"]').fill('Draft Variant');

    await page.goto('/chat');
    await openABAdminPage(page);

    await expect(page.locator('.ab-variant-card').first().locator('[data-field="label"]')).toHaveValue('Draft Variant');
  });
});

// =============================================================================
// Dedicated page -- champion selection
// =============================================================================

test.describe('A/B Admin Page -- Champion Selection', () => {

  test('changing champion select updates champion', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('#ab-admin-champion').selectOption('Poet');
    await expect(page.locator('#ab-admin-champion')).toHaveValue('Poet');
  });

  test('adding an unsaved variant does not change champion choices yet', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('#ab-admin-add-variant').click();
    await page.locator('.ab-variant-card').last().locator('[data-field="label"]').fill('Critic');

    const championOptions = page.locator('#ab-admin-champion option');
    await expect(championOptions).toHaveCount(2);
  });

  test('removing an unsaved champion variant does not change saved champion selection', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openABAdminPage(page);

    await page.locator('.ab-variant-remove').first().click();

    await expect(page.locator('#ab-admin-champion')).toHaveValue('Baseline');
  });
});

// =============================================================================
// A/B comparison streaming
// =============================================================================

test.describe('A/B Comparison Streaming', () => {

  test('sends A/B comparison and shows two arms', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse({
      armAContent: 'Champion says hello',
      armBContent: 'Challenger says hi',
    });

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Hello');
    await page.getByRole('button', { name: 'Send message' }).click();

    const comparison = page.locator('.ab-comparison').last();
    await expect(comparison).toBeVisible();

    const arms = comparison.locator('.ab-arm');
    await expect(arms).toHaveCount(2);

    await expect(comparison.locator('.ab-arm-label').first()).toHaveText('Response A');
    await expect(comparison.locator('.ab-arm-label').nth(1)).toHaveText('Response B');
  });

  test('A/B stream populates content in both arms', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse({
      armAContent: 'Alpha answer',
      armBContent: 'Beta answer',
    });

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Test AB');
    await page.getByRole('button', { name: 'Send message' }).click();

    const armA = page.locator('.ab-arm').first().locator('.message-content');
    const armB = page.locator('.ab-arm').nth(1).locator('.message-content');
    await expect(armA).toContainText('Alpha answer');
    await expect(armB).toContainText('Beta answer');
  });

  test('faster A/B arm finalizes before the slower arm finishes', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = [
      JSON.stringify({ type: 'meta', event: 'stream_started' }),
      JSON.stringify({ type: 'ab_arms', arm_a_name: 'Baseline', arm_b_name: 'Poet', variant_label_mode: 'post_vote_reveal' }),
      JSON.stringify({ arm: 'a', type: 'chunk', content: 'Fast arm chunk' }),
      JSON.stringify({ arm: 'b', type: 'chunk', content: 'Slow arm chunk' }),
      JSON.stringify({ arm: 'a', type: 'final', response: 'Fast arm done', model_used: 'openai/gpt-4o' }),
      JSON.stringify({ arm: 'b', type: 'chunk', content: 'Slow arm still streaming' }),
      JSON.stringify({ arm: 'b', type: 'final', response: 'Slow arm done', model_used: 'anthropic/claude-3.5-sonnet' }),
      JSON.stringify({
        type: 'ab_meta',
        comparison_id: 42,
        conversation_id: 1,
        arm_a_message_id: 101,
        arm_b_message_id: 102,
        arm_a_variant: 'Baseline',
        arm_b_variant: 'Poet',
        variant_label_mode: 'post_vote_reveal',
      }),
    ].join('\n') + '\n';

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.goto('/chat');
    await page.waitForFunction(() => typeof (window as any).UI !== 'undefined');
    await page.evaluate(() => {
      const events: string[] = [];
      (window as any).__abTraceEventOrder = events;
      const originalFinalizeTrace = (window as any).UI.finalizeTrace.bind((window as any).UI);
      const originalUpdateABResponse = (window as any).UI.updateABResponse.bind((window as any).UI);

      (window as any).UI.finalizeTrace = (messageId: string, trace: unknown, finalEvent: unknown) => {
        if (String(messageId).endsWith('-ab-a')) events.push('finalize:a');
        if (String(messageId).endsWith('-ab-b')) events.push('finalize:b');
        return originalFinalizeTrace(messageId, trace, finalEvent);
      };

      (window as any).UI.updateABResponse = (responseId: string, html: string, streaming = false) => {
        if (!streaming && String(responseId).endsWith('-ab-a')) events.push('response:a');
        if (!streaming && String(responseId).endsWith('-ab-b')) events.push('response:b');
        return originalUpdateABResponse(responseId, html, streaming);
      };
    });

    await page.getByLabel('Message input').fill('Test AB');
    await page.getByRole('button', { name: 'Send message' }).click();
    await expect(page.locator('.ab-vote-container')).toBeVisible();

    const eventOrder = await page.evaluate(() => (window as any).__abTraceEventOrder as string[]);
    expect(eventOrder.indexOf('finalize:a')).toBeGreaterThanOrEqual(0);
    expect(eventOrder.indexOf('response:b')).toBeGreaterThanOrEqual(0);
    expect(eventOrder.indexOf('finalize:a')).toBeLessThan(eventOrder.indexOf('response:b'));
  });

  test('completed A/B arm timer freezes while the slower arm is still streaming', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);
    await openChatPage(page);
    await enableVisibleABTraceMode(page);

    await waitForABPlaywrightHook(page);
    await page.evaluate(() => {
      const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
      (window as any).__ARCHI_PLAYWRIGHT__.ab.setStreamOverride(async function* () {
        yield { type: 'ab_arms', arm_a_name: 'Baseline', arm_b_name: 'Poet', variant_label_mode: 'post_vote_reveal' };
        yield { arm: 'a', type: 'chunk', content: 'Fast arm chunk' };
        yield { arm: 'b', type: 'chunk', content: 'Slow arm chunk' };
        await sleep(150);
        yield { arm: 'a', type: 'final', response: 'Fast arm done', model_used: 'openai/gpt-4o', duration_ms: 150 };
        await sleep(350);
        yield { arm: 'b', type: 'final', response: 'Slow arm done', model_used: 'anthropic/claude-3.5-sonnet', duration_ms: 500 };
        yield {
          type: 'ab_meta',
          comparison_id: 42,
          conversation_id: 1,
          arm_a_message_id: 101,
          arm_b_message_id: 102,
          arm_a_variant: 'Baseline',
          arm_b_variant: 'Poet',
          variant_label_mode: 'post_vote_reveal',
        };
      });
    });

    await page.getByLabel('Message input').fill('Test AB timers');
    await page.getByRole('button', { name: 'Send message' }).click();

    const armATimer = page.locator('.ab-comparison .trace-timer').first();
    const armBTimer = page.locator('.ab-comparison .trace-timer').nth(1);

    await expect.poll(async () => (await armATimer.textContent())?.trim()).toBe('150ms');
    const armBTimerBefore = (await armBTimer.textContent())?.trim();
    await page.waitForTimeout(200);
    await expect(armATimer).toHaveText('150ms');
    const armBTimerAfter = (await armBTimer.textContent())?.trim();
    expect(armBTimerAfter).not.toBe(armBTimerBefore);
    await expect(page.locator('.ab-vote-container')).toBeVisible();
  });

  test('A/B headers render cleanly with streaming disclosure and hidden trace mode', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABDecisionMock(page, {
      use_ab: true,
      reason: 'sampled',
      pending_count: 0,
      max_pending_comparisons_per_conversation: 1,
    });

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          enabled: true,
          is_admin: false,
          comparison_rate: 1,
          variant_label_mode: 'always_visible',
          activity_panel_default_state: 'hidden',
          max_pending_comparisons_per_conversation: 1,
        },
      });
    });

    const abStream = [
      JSON.stringify({ type: 'meta', event: 'stream_started' }),
      JSON.stringify({
        type: 'ab_arms',
        arm_a_name: 'Baseline',
        arm_b_name: 'Poet',
        variant_label_mode: 'always_visible',
      }),
      JSON.stringify({ arm: 'a', type: 'chunk', content: 'Champion says hello' }),
      JSON.stringify({ arm: 'b', type: 'chunk', content: 'Challenger says hi' }),
      JSON.stringify({
        type: 'ab_meta',
        comparison_id: 42,
        conversation_id: 1,
        arm_a_message_id: 101,
        arm_b_message_id: 102,
        arm_a_variant: 'Baseline',
        arm_b_variant: 'Poet',
        variant_label_mode: 'always_visible',
      }),
    ].join('\n') + '\n';

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await openChatPage(page);
    await page.getByLabel('Message input').fill('Hello');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-arm-title-row')).toHaveCount(2);
    await expect(page.locator('.ab-arm-variant-name').first()).toHaveText('Baseline');
    await expect(page.locator('.ab-arm-variant-name').nth(1)).toHaveText('Poet');
    await expect(page.locator('.ab-comparison .trace-container')).toHaveCount(0);
  });

  test('A/B trace headers match the standard agent activity presentation', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABDecisionMock(page, {
      use_ab: true,
      reason: 'sampled',
      pending_count: 0,
      max_pending_comparisons_per_conversation: 1,
    });

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          enabled: true,
          is_admin: false,
          comparison_rate: 1,
          variant_label_mode: 'hidden',
          activity_panel_default_state: 'collapsed',
          max_pending_comparisons_per_conversation: 1,
        },
      });
    });

    const abStream = [
      JSON.stringify({ type: 'meta', event: 'stream_started' }),
      JSON.stringify({ arm: 'a', type: 'chunk', content: 'Champion says hello' }),
      JSON.stringify({ arm: 'b', type: 'chunk', content: 'Challenger says hi' }),
      JSON.stringify({
        type: 'ab_meta',
        comparison_id: 42,
        conversation_id: 1,
        arm_a_message_id: 101,
        arm_b_message_id: 102,
        arm_a_variant: 'normal',
        arm_b_variant: 'mad',
        variant_label_mode: 'hidden',
      }),
    ].join('\n') + '\n';

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await openChatPage(page);
    await page.getByLabel('Message input').fill('Hello');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-comparison .trace-container')).toHaveCount(2);
    await expect(page.locator('.ab-comparison .trace-label').first()).toHaveText('Agent Activity');
    await expect(page.locator('.ab-comparison .trace-toggle')).toHaveCount(2);
  });

  test('vote buttons appear after A/B stream completes', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse();

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Test vote');
    await page.getByRole('button', { name: 'Send message' }).click();

    const voteContainer = page.locator('.ab-vote-container');
    await expect(voteContainer).toBeVisible();

    await expect(page.locator('.ab-vote-btn-a')).toBeVisible();
    await expect(page.locator('.ab-vote-btn-tie')).toBeVisible();
    await expect(page.locator('.ab-vote-btn-b')).toBeVisible();

    await expect(page.locator('.ab-vote-prompt')).toContainText('Which response do you prefer?');
  });

  test('input stays disabled until vote is submitted', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse();

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });

    await page.route('**/api/ab/preference', async (route) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Test disabled');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await expect(page.getByLabel('Message input')).toBeDisabled();

    await page.locator('.ab-vote-btn-a').click();

    await expect(page.getByLabel('Message input')).not.toBeDisabled();
  });

  test('restores a single pending comparison below the limit without locking input', async ({ page }) => {
    await setupBasicMocks(page);

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          enabled: true,
          is_admin: false,
          can_manage: false,
          comparison_rate: 1,
          variant_label_mode: 'post_vote_reveal',
          activity_panel_default_state: 'hidden',
          max_pending_comparisons_per_conversation: 2,
        },
      });
    });

    await page.route('**/api/load_conversation', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          conversation_id: 1,
          title: 'Pending queue',
          created_at: new Date().toISOString(),
          last_message_at: new Date().toISOString(),
          messages: [],
          pending_ab_comparisons: [
            {
              comparison_id: 2001,
              variant_a_name: 'Baseline',
              variant_b_name: 'Poet',
              variant_label_mode: 'post_vote_reveal',
              activity_panel_default_state: 'hidden',
              response_a: { message_id: 501, content: 'Pending A', model_used: 'openai/gpt-4o' },
              response_b: { message_id: 502, content: 'Pending B', model_used: 'anthropic/claude-3.5-sonnet' },
            },
          ],
          pending_ab_comparison: {
            comparison_id: 2001,
            variant_a_name: 'Baseline',
            variant_b_name: 'Poet',
            variant_label_mode: 'post_vote_reveal',
            activity_panel_default_state: 'hidden',
            response_a: { message_id: 501, content: 'Pending A', model_used: 'openai/gpt-4o' },
            response_b: { message_id: 502, content: 'Pending B', model_used: 'anthropic/claude-3.5-sonnet' },
          },
        },
      });
    });

    await setupABDecisionMock(page, {
      use_ab: true,
      reason: 'sampled',
      pending_count: 0,
      max_pending_comparisons_per_conversation: 2,
    });

    await openChatPage(page);
    await loadConversationFromSidebar(page, 1);

    await expect(page.locator('.ab-comparison')).toHaveCount(1);
    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await expect(page.getByLabel('Message input')).not.toBeDisabled();
  });

  test('restores multiple pending comparisons and locks input when the limit is reached', async ({ page }) => {
    await setupBasicMocks(page);

    await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          enabled: true,
          is_admin: false,
          can_manage: false,
          comparison_rate: 1,
          variant_label_mode: 'post_vote_reveal',
          activity_panel_default_state: 'hidden',
          max_pending_comparisons_per_conversation: 2,
        },
      });
    });

    await page.route('**/api/load_conversation', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          conversation_id: 1,
          title: 'Pending queue',
          created_at: new Date().toISOString(),
          last_message_at: new Date().toISOString(),
          messages: [],
          pending_ab_comparisons: [
            {
              comparison_id: 2001,
              variant_a_name: 'Baseline',
              variant_b_name: 'Poet',
              variant_label_mode: 'post_vote_reveal',
              activity_panel_default_state: 'hidden',
              response_a: { message_id: 501, content: 'Pending A1', model_used: 'openai/gpt-4o' },
              response_b: { message_id: 502, content: 'Pending B1', model_used: 'anthropic/claude-3.5-sonnet' },
            },
            {
              comparison_id: 2002,
              variant_a_name: 'Baseline',
              variant_b_name: 'Critic',
              variant_label_mode: 'post_vote_reveal',
              activity_panel_default_state: 'hidden',
              response_a: { message_id: 503, content: 'Pending A2', model_used: 'openai/gpt-4o' },
              response_b: { message_id: 504, content: 'Pending B2', model_used: 'anthropic/claude-3.5-sonnet' },
            },
          ],
          pending_ab_comparison: {
            comparison_id: 2002,
            variant_a_name: 'Baseline',
            variant_b_name: 'Critic',
            variant_label_mode: 'post_vote_reveal',
            activity_panel_default_state: 'hidden',
            response_a: { message_id: 503, content: 'Pending A2', model_used: 'openai/gpt-4o' },
            response_b: { message_id: 504, content: 'Pending B2', model_used: 'anthropic/claude-3.5-sonnet' },
          },
        },
      });
    });

    await setupABDecisionMock(page, {
      use_ab: true,
      reason: 'sampled',
      pending_count: 0,
      max_pending_comparisons_per_conversation: 2,
    });

    await openChatPage(page);
    await loadConversationFromSidebar(page, 1);

    await expect(page.locator('.ab-comparison')).toHaveCount(2);
    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await expect(page.getByLabel('Message input')).toBeDisabled();
  });
});

// =============================================================================
// Vote submission
// =============================================================================

test.describe('A/B Vote Submission', () => {

  async function setupABWithVote(page: any) {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse({ comparisonId: 99 });

    await page.route('**/api/ab/compare', async (route: any) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });
  }

  test('voting A sends preference "a" to server', async ({ page }) => {
    await setupABWithVote(page);

    let submittedPreference: string | null = null;
    await page.route('**/api/ab/preference', async (route: any) => {
      const body = route.request().postDataJSON();
      submittedPreference = body.preference;
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Vote A');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-a').click();

    expect(submittedPreference).toBe('a');
  });

  test('voting B sends preference "b" to server', async ({ page }) => {
    await setupABWithVote(page);

    let submittedPreference: string | null = null;
    await page.route('**/api/ab/preference', async (route: any) => {
      const body = route.request().postDataJSON();
      submittedPreference = body.preference;
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Vote B');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-b').click();

    expect(submittedPreference).toBe('b');
  });

  test('voting Tie sends preference "tie" to server', async ({ page }) => {
    await setupABWithVote(page);

    let submittedPreference: string | null = null;
    await page.route('**/api/ab/preference', async (route: any) => {
      const body = route.request().postDataJSON();
      submittedPreference = body.preference;
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Vote Tie');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-tie').click();

    expect(submittedPreference).toBe('tie');
  });

  test('vote sends correct comparison_id', async ({ page }) => {
    await setupABWithVote(page);

    let sentComparisonId: number | null = null;
    await page.route('**/api/ab/preference', async (route: any) => {
      const body = route.request().postDataJSON();
      sentComparisonId = body.comparison_id;
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Check ID');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-a').click();

    expect(sentComparisonId).toBe(99);
  });

  test('vote buttons disappear after voting', async ({ page }) => {
    await setupABWithVote(page);

    await page.route('**/api/ab/preference', async (route: any) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Dismiss vote');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-a').click();

    await expect(page.locator('.ab-vote-container')).toHaveCount(0);
  });

  test('choosing A collapses comparison to single message', async ({ page }) => {
    await setupABWithVote(page);

    await page.route('**/api/ab/preference', async (route: any) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Collapse test');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-a').click();

    await expect(page.locator('.ab-comparison')).toHaveCount(0);
  });

  test('choosing A preserves response A timer after collapse', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse({
      comparisonId: 99,
      armADurationMs: 150,
      armBDurationMs: 320,
    });

    await page.route('**/api/ab/compare', async (route: any) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });
    await page.route('**/api/ab/preference', async (route: any) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await enableVisibleABTraceMode(page);
    await page.getByLabel('Message input').fill('Keep A timer');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await expect(page.locator('.ab-comparison .trace-timer').first()).toHaveText('150ms');
    await expect(page.locator('.ab-comparison .trace-timer').nth(1)).toHaveText('320ms');

    await page.locator('.ab-vote-btn-a').click();

    await expect(page.locator('.ab-comparison')).toHaveCount(0);
    await expect(page.locator('.message.assistant .trace-timer').last()).toHaveText('150ms');
  });

  test('choosing B preserves response B timer after collapse', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const abStream = createABStreamResponse({
      comparisonId: 99,
      armADurationMs: 180,
      armBDurationMs: 410,
    });

    await page.route('**/api/ab/compare', async (route: any) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: abStream });
    });
    await page.route('**/api/ab/preference', async (route: any) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await enableVisibleABTraceMode(page);
    await page.getByLabel('Message input').fill('Keep B timer');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await expect(page.locator('.ab-comparison .trace-timer').first()).toHaveText('180ms');
    await expect(page.locator('.ab-comparison .trace-timer').nth(1)).toHaveText('410ms');

    await page.locator('.ab-vote-btn-b').click();

    await expect(page.locator('.ab-comparison')).toHaveCount(0);
    await expect(page.locator('.message.assistant .trace-timer').last()).toHaveText('410ms');
  });

  test('post-vote winner keeps trace and tool disclosure interactive', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    await page.route('**/api/ab/preference', async (route: any) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await enableVisibleABTraceMode(page);
    await waitForABPlaywrightHook(page);
    await page.evaluate(() => {
      (window as any).__ARCHI_PLAYWRIGHT__.ab.setStreamOverride(async function* () {
        yield { type: 'ab_arms', arm_a_name: 'Baseline', arm_b_name: 'Poet', variant_label_mode: 'post_vote_reveal' };
        yield { arm: 'a', type: 'tool_start', tool_call_id: 'tool-a', tool_name: 'search', tool_args: { query: 'post vote trace' } };
        yield { arm: 'a', type: 'tool_output', tool_call_id: 'tool-a', output: 'tool output' };
        yield { arm: 'a', type: 'tool_end', tool_call_id: 'tool-a', status: 'success', duration_ms: 25 };
        yield { arm: 'a', type: 'final', response: 'Trace winner', model_used: 'openai/gpt-4o', duration_ms: 150 };
        yield { arm: 'b', type: 'final', response: 'Trace loser', model_used: 'anthropic/claude-3.5-sonnet', duration_ms: 280 };
        yield {
          type: 'ab_meta',
          comparison_id: 99,
          conversation_id: 1,
          arm_a_message_id: 101,
          arm_b_message_id: 102,
          arm_a_variant: 'Baseline',
          arm_b_variant: 'Poet',
          variant_label_mode: 'post_vote_reveal',
        };
      });
    });

    await page.getByLabel('Message input').fill('Keep trace interactive');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-a').click();

    await expect(page.locator('.ab-comparison')).toHaveCount(0);

    const traceContainer = page.locator('.message.assistant .trace-container').last();
    await expect(traceContainer).toHaveClass(/collapsed/);
    await traceContainer.locator('.trace-toggle').click();
    await expect(traceContainer).not.toHaveClass(/collapsed/);

    const toolStep = traceContainer.locator('.tool-step').first();
    await expect(toolStep).toBeVisible();
    await toolStep.locator('.step-header').click();
    await expect(toolStep.locator('.step-details')).toBeVisible();
  });

  test('choosing Tie keeps both arms with tie styling', async ({ page }) => {
    await setupABWithVote(page);

    await page.route('**/api/ab/preference', async (route: any) => {
      await route.fulfill({ status: 200, json: { success: true } });
    });

    await page.goto('/chat');
    await page.getByLabel('Message input').fill('Tie test');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-vote-container')).toBeVisible();
    await page.locator('.ab-vote-btn-tie').click();

    await expect(page.locator('.ab-arm-tie')).toHaveCount(2);
  });
});

// =============================================================================
// A/B error handling
// =============================================================================

test.describe('A/B Error Handling', () => {

  test('error in A/B stream shows error message', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    const errorStream = JSON.stringify({
      type: 'error',
      message: 'Both arms timed out',
    }) + '\n';

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/plain', body: errorStream });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Error test');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.locator('.ab-error-message')).toBeVisible();
    await expect(page.locator('.ab-error-message')).toContainText('Both arms timed out');
  });

  test('HTTP error from A/B compare re-enables input', async ({ page }) => {
    await setupBasicMocks(page);
    await setupABAdminMocks(page);

    await page.route('**/api/ab/compare', async (route) => {
      await route.fulfill({ status: 500, body: 'Internal Server Error' });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('500 error');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByLabel('Message input')).not.toBeDisabled();
    await expect(page.getByRole('button', { name: 'Send message' })).toBeVisible();
  });
});

// =============================================================================
// Normal mode -- A/B not engaged when pool is inactive
// =============================================================================

test.describe('A/B Inactive -- Normal Chat', () => {

  test('chat uses single stream when A/B pool is not enabled', async ({ page }) => {
    await setupBasicMocks(page);

    let abCompareCalled = false;
    await page.route('**/api/ab/compare', async (route) => {
      abCompareCalled = true;
      await route.fulfill({ status: 200, body: '' });
    });

    await page.route('**/api/get_chat_response_stream', async (route) => {
      const body = JSON.stringify({
        type: 'final',
        response: 'Normal response',
        message_id: 1,
        user_message_id: 1,
        conversation_id: 1,
      }) + '\n';
      await route.fulfill({ status: 200, contentType: 'text/plain', body });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Hello');
    await page.getByRole('button', { name: 'Send message' }).click();

    await page.waitForTimeout(500);
    expect(abCompareCalled).toBe(false);

    await expect(page.locator('.ab-comparison')).toHaveCount(0);
  });
});
