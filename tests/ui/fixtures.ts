/**
 * Shared test fixtures and utilities for archi Chat UI tests
 */
import { test as base, expect, Page } from '@playwright/test';

// =============================================================================
// Mock Data
// =============================================================================

export const mockData = {
  configs: {
    options: [{ name: 'cms_simple' }, { name: 'test_config' }],
  },

  conversations: [
    {
      conversation_id: 1,
      title: 'Test Conversation',
      last_message_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
    },
    {
      conversation_id: 2,
      title: 'Another Chat',
      last_message_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
    },
  ],

  providers: {
    providers: [
      {
        type: 'openrouter',
        display_name: 'OpenRouter',
        enabled: true,
        models: [
          { id: 'openai/gpt-4o', name: 'GPT-4o', display_name: 'GPT-4o' },
          { id: 'anthropic/claude-3.5-sonnet', name: 'Claude 3.5 Sonnet', display_name: 'Claude 3.5 Sonnet' },
          { id: '__custom__', name: 'Custom Model', display_name: 'Custom Model...' },
        ],
        default_model: 'openai/gpt-4o',
      },
      {
        type: 'openai',
        display_name: 'OpenAI',
        enabled: false,
        models: [],
      },
    ],
  },

  pipelineDefault: {
    model_class: 'OpenRouterLLM',
    model_name: 'openai/gpt-5-nano',
    model_label: 'gpt-5-nano',
  },

  agentInfo: {
    config_name: 'cms_simple',
    agent_name: 'CMS CompOps Agent',
    description: 'A helpful assistant for CMS Computing Operations',
    pipeline: 'CMSCompOpsAgent',
    embedding_name: 'HuggingFaceEmbeddings',
    data_sources: ['web', 'local_files'],
  },

  providerKeys: {
    providers: [
      { provider: 'openrouter', display_name: 'OpenRouter', configured: true, has_session_key: false },
      { provider: 'openai', display_name: 'OpenAI', configured: false, has_session_key: false },
    ],
  },

  // A/B Testing mock data ---------------------------------------------------

  agentsList: {
    agents: [
      { name: 'CMS CompOps Agent', filename: 'cms-comp-ops.md', ab_only: false },
      { name: 'Reviewer Agent', filename: 'reviewer.md', ab_only: false },
    ],
    active_name: 'CMS CompOps Agent',
  },

  abAgentsList: {
    agents: [
      { name: 'Baseline AB Agent', filename: 'baseline-ab.md', ab_only: true },
      { name: 'Poet AB Agent', filename: 'poet-ab.md', ab_only: true },
      { name: 'Critic AB Agent', filename: 'critic-ab.md', ab_only: true },
    ],
    active_name: null,
  },

  abPoolAdmin: {
    enabled: true,
    is_admin: true,
    can_view: true,
    can_manage: true,
    can_view_metrics: true,
    can_participate: true,
    participant_eligible: true,
    participant_reason: 'eligible',
    participant_targeted: true,
    enabled_requested: true,
    champion: 'Baseline',
    variants: ['Baseline', 'Poet'],
    variant_details: [
      { label: 'Baseline', agent_spec: 'baseline-ab.md' },
      { label: 'Poet', agent_spec: 'poet-ab.md', provider: 'openrouter', model: 'anthropic/claude-3.5-sonnet' },
    ],
    comparison_rate: 1,
    default_comparison_rate: 1,
    variant_label_mode: 'post_vote_reveal',
    activity_panel_default_state: 'hidden',
    max_pending_comparisons_per_conversation: 1,
    defaults: {
      provider: 'openrouter',
      model: 'openai/gpt-4o',
      recursion_limit: 50,
      num_documents_to_retrieve: 5,
      ab_catalog_source: 'database',
    },
    warnings: [],
  },

  abPoolAdminInactive: {
    enabled: false,
    is_admin: true,
    can_view: true,
    can_manage: true,
    can_view_metrics: true,
    can_participate: true,
    participant_eligible: false,
    participant_reason: 'disabled',
    participant_targeted: false,
    enabled_requested: false,
    champion: '',
    variants: [],
    variant_details: [],
    comparison_rate: 1,
    default_comparison_rate: 1,
    variant_label_mode: 'post_vote_reveal',
    activity_panel_default_state: 'hidden',
    max_pending_comparisons_per_conversation: 1,
    defaults: {
      provider: 'openrouter',
      model: 'openai/gpt-4o',
      recursion_limit: 50,
      num_documents_to_retrieve: 5,
      ab_catalog_source: 'database',
    },
    warnings: ['A/B testing is inactive until at least two variants are configured.'],
  },

  abPoolNonAdmin: {
    enabled: true,
    is_admin: false,
    can_view: false,
    can_manage: false,
    can_view_metrics: false,
    can_participate: false,
    participant_eligible: false,
    participant_reason: 'not_participant',
    participant_targeted: false,
    default_comparison_rate: 1,
  },

  currentUser: {
    id: 'user-123',
    display_name: 'Test User',
    email: 'test@example.com',
    auth_provider: 'basic',
    theme: 'light',
    preferred_model: null,
    preferred_temperature: null,
    ab_participation_rate: null,
    has_openrouter_key: false,
    has_openai_key: false,
    has_anthropic_key: false,
  },

  abMetrics: {
    metrics: [
      {
        variant_name: 'Baseline',
        wins: 6,
        losses: 3,
        ties: 1,
        total_comparisons: 10,
      },
      {
        variant_name: 'Poet',
        wins: 3,
        losses: 6,
        ties: 1,
        total_comparisons: 10,
      },
    ],
  },
};

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

function abAdminPageHtml() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>archi - A/B Testing</title>
  <link rel="stylesheet" href="/static/chat.css">
  <link rel="stylesheet" href="/static/data.css">
</head>
<body data-can-manage-ab-testing="true" data-can-view-ab-metrics="true">
  <div class="data-app">
    <header class="data-header">
      <div class="header-left"><a href="/data" class="back-link"><span>Data</span></a></div>
      <div class="header-center"><h1>A/B Testing</h1></div>
      <div class="header-right"><a href="/chat" class="icon-btn ab-admin-nav-btn"><span>Chat</span></a></div>
    </header>
    <main class="ab-admin-layout">
      <section class="ab-admin-panel">
        <div class="ab-admin-panel-header">
          <div>
            <h2>Experiment Settings</h2>
            <p>Manage participant comparison rate, variant label visibility, activity panel defaults, and the active champion/variant pool.</p>
          </div>
          <span class="ab-pool-status" id="ab-admin-status">Inactive</span>
        </div>
        <div class="ab-admin-settings-grid">
          <label class="ab-admin-field"><span>Comparison Rate</span><input type="number" id="ab-admin-sample-rate" min="0" max="1" step="0.05" value="1"></label>
          <label class="ab-admin-field"><span>Variant Label Mode</span><select id="ab-admin-disclosure-mode"><option value="post_vote_reveal">Post-Vote Reveal</option><option value="hidden">Hidden</option><option value="always_visible">Always Visible</option></select></label>
          <label class="ab-admin-field"><span>Activity Panel Default State</span><select id="ab-admin-trace-mode"><option value="hidden">Hidden</option><option value="collapsed">Collapsed</option><option value="expanded">Expanded</option></select></label>
          <label class="ab-admin-field"><span>Max Pending Comparisons Per Conversation</span><input type="number" id="ab-admin-max-pending" min="1" step="1" value="1"></label>
          <label class="ab-admin-field ab-admin-field-wide"><span>Champion</span><select id="ab-admin-champion"></select></label>
        </div>
        <div class="ab-admin-actions">
          <button class="ab-pool-btn ab-pool-btn-save" id="ab-admin-save">Save Configuration</button>
          <button class="ab-pool-btn ab-pool-btn-disable" id="ab-admin-disable">Disable</button>
        </div>
        <div class="ab-pool-message" id="ab-admin-message"></div>
        <div class="ab-admin-warning-list" id="ab-admin-warnings" style="display: none;"></div>
      </section>
      <section class="ab-admin-panel">
        <div class="ab-admin-panel-header">
          <div>
            <h2>Variants</h2>
            <p>Each variant must have a unique label and a concrete A/B agent spec from the database-backed experiment catalog.</p>
          </div>
          <div class="ab-admin-panel-actions">
            <button class="ab-pool-btn ab-pool-btn-save" id="ab-admin-variant-save" type="button">Save Variants</button>
            <button class="ab-pool-btn" id="ab-admin-add-variant" type="button">Add Variant</button>
          </div>
        </div>
        <div class="ab-pool-message" id="ab-admin-variant-message"></div>
        <div class="ab-admin-variant-list" id="ab-admin-variant-list"></div>
      </section>
    </main>
  </div>
  <div class="ab-agent-modal" id="ab-agent-modal" style="display: none;">
    <div class="ab-agent-modal-backdrop" data-close-modal="true"></div>
    <div class="ab-agent-modal-panel" role="dialog" aria-modal="true" aria-labelledby="ab-agent-modal-title">
      <div class="ab-agent-modal-header">
        <div><h2 id="ab-agent-modal-title">New A/B Agent</h2><p>Create an agent spec that is only available to A/B experiments.</p></div>
        <button class="ab-agent-modal-close" id="ab-agent-modal-close" type="button" aria-label="Close">&times;</button>
      </div>
      <label class="ab-admin-field"><span>Agent Name</span><input type="text" id="ab-agent-name" placeholder="A/B Candidate"></label>
      <div class="ab-agent-tools-section">
        <div class="ab-agent-tools-header"><span>Tools</span><span class="ab-agent-tools-note">Choose the tools available to this experiment-only agent.</span></div>
        <div class="ab-agent-tools-list" id="ab-agent-tools-list"></div>
      </div>
      <label class="ab-admin-field"><span>System Prompt</span><textarea id="ab-agent-prompt" class="ab-agent-prompt" rows="12" placeholder="Write the system prompt here."></textarea></label>
      <div class="ab-agent-modal-footer">
        <div class="ab-pool-message" id="ab-agent-message"></div>
        <div class="ab-agent-modal-actions">
          <button class="ab-pool-btn ab-pool-btn-disable" id="ab-agent-cancel" type="button">Cancel</button>
          <button class="ab-pool-btn ab-pool-btn-save" id="ab-agent-save" type="button">Create Agent</button>
        </div>
      </div>
    </div>
  </div>
  <script src="/static/modules/theme-init.js"></script>
  <script src="/static/modules/ab-admin.js"></script>
</body>
</html>`;
}

function adminDataPageHtml() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>archi - Data Sources</title>
  <link rel="stylesheet" href="/static/chat.css">
  <link rel="stylesheet" href="/static/data.css">
</head>
<body>
  <div class="data-app">
    <header class="data-header">
      <div class="header-left"><a href="/chat" class="back-link"><span>Chat</span></a></div>
      <div class="header-center"><h1>Data Sources</h1></div>
      <div class="header-right">
        <a href="/admin/ab-testing" class="icon-btn header-action-btn ab-admin-nav-btn" title="A/B Testing"><span>A/B Testing</span></a>
        <a href="/upload" class="icon-btn header-action-btn" title="Uploader"><span>Uploader</span></a>
        <a href="/admin/database" class="icon-btn header-action-btn" title="Postgres"><span>Postgres</span></a>
        <button class="icon-btn header-action-btn" id="refresh-btn" title="Refresh" type="button"><span>Refresh</span></button>
      </div>
    </header>
    <main class="data-content">
      <section class="documents-panel">
        <div class="empty-state"><h3>Mock Data View</h3><p>Playwright admin bootstrap shell.</p></div>
      </section>
    </main>
  </div>
</body>
</html>`;
}

// =============================================================================
// Stream Response Helpers
// =============================================================================

export function createStreamResponse(content: string, options: {
  messageId?: number;
  conversationId?: number;
  includeChunks?: boolean;
} = {}) {
  const { messageId = 1, conversationId = 1, includeChunks = false } = options;
  
  if (includeChunks) {
    const chunks = content.split(' ');
    const events = chunks.map(chunk => 
      JSON.stringify({ type: 'chunk', content: chunk + ' ' })
    );
    events.push(JSON.stringify({
      type: 'final',
      response: content,
      message_id: messageId,
      user_message_id: messageId,
      conversation_id: conversationId,
    }));
    return events.join('\n');
  }
  
  return JSON.stringify({
    type: 'final',
    response: content,
    message_id: messageId,
    user_message_id: messageId,
    conversation_id: conversationId,
  }) + '\n';
}

export function createToolCallEvents(toolName: string, args: object, output: string, options: {
  toolCallId?: string;
  durationMs?: number;
  status?: 'success' | 'error';
} = {}) {
  const { toolCallId = 'tc_1', durationMs = 150, status = 'success' } = options;
  
  return [
    { type: 'tool_start', tool_call_id: toolCallId, tool_name: toolName, tool_args: args },
    { type: 'tool_output', tool_call_id: toolCallId, output },
    { type: 'tool_end', tool_call_id: toolCallId, status, duration_ms: durationMs },
  ];
}

// =============================================================================
// Page Setup Helpers
// =============================================================================

export async function setupBasicMocks(page: Page) {
  let currentUser = cloneJson(mockData.currentUser);

  await page.route('**/api/get_configs', async (route) => {
    await route.fulfill({ status: 200, json: mockData.configs });
  });

  await page.route('**/api/list_conversations*', async (route) => {
    await route.fulfill({ status: 200, json: { conversations: mockData.conversations } });
  });

  await page.route('**/api/providers', async (route) => {
    await route.fulfill({ status: 200, json: mockData.providers });
  });

  await page.route('**/api/pipeline/default_model', async (route) => {
    await route.fulfill({ status: 200, json: mockData.pipelineDefault });
  });

  await page.route('**/api/agent/info*', async (route) => {
    await route.fulfill({ status: 200, json: mockData.agentInfo });
  });

  await page.route('**/api/providers/keys', async (route) => {
    await route.fulfill({ status: 200, json: mockData.providerKeys });
  });

  await page.route('**/api/ab/metrics*', async (route) => {
    await route.fulfill({ status: 200, json: mockData.abMetrics });
  });

  await page.route('**/api/users/me/preferences', async (route) => {
    if (route.request().method() !== 'PATCH') {
      await route.fallback();
      return;
    }
    const body = route.request().postDataJSON();
    currentUser = {
      ...currentUser,
      ...body,
    };
    await route.fulfill({ status: 200, json: currentUser });
  });

  await page.route('**/api/users/me', async (route) => {
    await route.fulfill({ status: 200, json: currentUser });
  });

  await page.route('**/api/new_conversation', async (route) => {
    await route.fulfill({ status: 200, json: { conversation_id: null } });
  });

  // Default agent list
  await page.route('**/api/agents/list*', async (route) => {
    await route.fulfill({ status: 200, json: mockData.agentsList });
  });

  await page.route('**/api/ab/agents/list*', async (route) => {
    await route.fulfill({ status: 200, json: mockData.abAgentsList });
  });

  await page.route('**/api/ab/agents/template*', async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        name: 'New A/B Agent',
        prompt: 'You are an A/B-only agent.',
        tools: [
          { name: 'search_docs', description: 'Search indexed documents' },
          { name: 'fetch_ticket', description: 'Fetch ticket details' },
        ],
        template: '---\nname: New A/B Agent\nab_only: true\ntools:\n  - search_docs\n  - fetch_ticket\n---\n\nYou are an A/B-only agent.',
      },
    });
  });

  await page.route('**/api/ab/agents', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    const body = route.request().postDataJSON();
    const name = String(body.name || 'New A/B Agent').trim() || 'New A/B Agent';
    const filename = `${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'new-ab-agent'}.md`;
    await route.fulfill({
      status: 200,
      json: { success: true, name, filename, scope: 'ab' },
    });
  });

  await page.route('**/api/agents', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    const body = route.request().postDataJSON();
    const content = String(body.content || '');
    const match = content.match(/^name:\s*(.+)$/m);
    const name = match ? match[1].trim() : 'New A/B Agent';
    const filename = `${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'new-ab-agent'}.md`;
    await route.fulfill({
      status: 200,
      json: { success: true, name, filename, scope: body.scope || 'default' },
    });
  });

  // Default A/B pool: non-admin, disabled.
  // Tests that need admin behavior should call setupABAdminMocks AFTER this.
  await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        success: true,
        enabled: false,
        enabled_requested: false,
        is_admin: false,
        can_view: false,
        can_manage: false,
        can_view_metrics: false,
        can_participate: false,
        participant_eligible: false,
        participant_reason: 'not_participant',
        participant_targeted: false,
        comparison_rate: 1,
        default_comparison_rate: 1,
      },
    });
  });

  await page.route('**/api/ab/decision*', async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        success: true,
        enabled: false,
        use_ab: false,
        reason: 'disabled',
        pending_count: 0,
        max_pending_comparisons_per_conversation: 1,
      },
    });
  });
}

export async function setupABDecisionMock(page: Page, overrides: Record<string, any> = {}) {
  await page.route('**/api/ab/decision*', async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        success: true,
        enabled: false,
        use_ab: false,
        reason: 'disabled',
        pending_count: 0,
        max_pending_comparisons_per_conversation: 1,
        ...overrides,
      },
    });
  });
}

export async function setupABAdminPageBootstrap(page: Page, options: {
  abAgentsList?: typeof mockData.abAgentsList;
} = {}) {
  const seededABAgents = cloneJson(options.abAgentsList?.agents || mockData.abAgentsList.agents);
  let abAgents = seededABAgents;

  await page.route('**/data', async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/html', body: adminDataPageHtml() });
  });

  await page.route('**/admin/ab-testing', async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/html', body: abAdminPageHtml() });
  });

  await page.route('**/api/agents/list*', async (route) => {
    await route.fulfill({ status: 200, json: mockData.agentsList });
  });

  await page.route('**/api/ab/agents/list*', async (route) => {
    await route.fulfill({
      status: 200,
      json: { agents: abAgents, active_name: null, scope: 'ab' },
    });
  });

  await page.route('**/api/ab/agents/template*', async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        name: 'New A/B Agent',
        prompt: 'You are an A/B-only agent.',
        tools: [
          { name: 'search_docs', description: 'Search indexed documents' },
          { name: 'fetch_ticket', description: 'Fetch ticket details' },
        ],
        scope: 'ab',
      },
    });
  });

  await page.route('**/api/ab/agents', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    const body = route.request().postDataJSON();
    const name = String(body.name || '').trim() || 'New A/B Agent';
    const filename = `${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'new-ab-agent'}.md`;

    abAgents = abAgents.filter((agent) => agent.filename !== filename);
    abAgents.push({ name, filename, ab_only: true });

    await route.fulfill({
      status: 200,
      json: { success: true, name, filename, scope: 'ab' },
    });
  });
}

export async function setupStreamMock(page: Page, response: string, delay = 0) {
  await page.route('**/api/get_chat_response_stream', async (route) => {
    if (delay > 0) {
      await new Promise(resolve => setTimeout(resolve, delay));
    }
    await route.fulfill({ status: 200, contentType: 'text/plain', body: response });
  });
}

/**
 * Set up route mocks that make the page behave as an admin with an active A/B pool.
 * Must be called BEFORE page.goto('/chat') so the init API calls get intercepted.
 */
export async function setupABAdminMocks(page: Page) {
  await setupABAdminPageBootstrap(page);

  await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
    await route.fulfill({ status: 200, json: mockData.abPoolAdmin });
  });

  await setupABDecisionMock(page, {
    use_ab: true,
    reason: 'sampled',
    pending_count: 0,
    max_pending_comparisons_per_conversation: mockData.abPoolAdmin.max_pending_comparisons_per_conversation,
  });
}

/**
 * Set up route mocks for an admin who has NOT yet enabled a pool.
 */
export async function setupABAdminInactiveMocks(page: Page) {
  await setupABAdminPageBootstrap(page);

  await page.route(/\/api\/ab\/pool(\?|$)/, async (route) => {
    await route.fulfill({ status: 200, json: mockData.abPoolAdminInactive });
  });

  await setupABDecisionMock(page, {
    use_ab: false,
    reason: 'disabled',
    pending_count: 0,
    max_pending_comparisons_per_conversation: mockData.abPoolAdminInactive.max_pending_comparisons_per_conversation,
  });
}

/**
 * Build an NDJSON body for a mock A/B comparison stream.
 */
export function createABStreamResponse(options: {
  armAContent?: string;
  armBContent?: string;
  comparisonId?: number;
  conversationId?: number;
  armAVariant?: string;
  armBVariant?: string;
  armAMessageId?: number;
  armBMessageId?: number;
  disclosureMode?: string;
  armADurationMs?: number;
  armBDurationMs?: number;
} = {}) {
  const {
    armAContent = 'Response from arm A',
    armBContent = 'Response from arm B',
    comparisonId = 42,
    conversationId = 1,
    armAVariant = 'Baseline',
    armBVariant = 'Poet',
    armAMessageId = 101,
    armBMessageId = 102,
    disclosureMode = 'post_vote_reveal',
    armADurationMs = 150,
    armBDurationMs = 300,
  } = options;

  const events = [
    { type: 'meta', event: 'stream_started' },
    {
      type: 'ab_arms',
      arm_a_name: armAVariant,
      arm_b_name: armBVariant,
      variant_label_mode: disclosureMode,
    },
    { arm: 'a', type: 'chunk', content: armAContent },
    { arm: 'b', type: 'chunk', content: armBContent },
    { arm: 'a', type: 'final', response: armAContent, model: 'gpt-4o', model_used: 'openai/gpt-4o', duration_ms: armADurationMs },
    { arm: 'b', type: 'final', response: armBContent, model: 'claude-3.5-sonnet', model_used: 'anthropic/claude-3.5-sonnet', duration_ms: armBDurationMs },
    {
      type: 'ab_meta',
      comparison_id: comparisonId,
      conversation_id: conversationId,
      arm_a_message_id: armAMessageId,
      arm_b_message_id: armBMessageId,
      arm_a_variant: armAVariant,
      arm_b_variant: armBVariant,
      variant_label_mode: disclosureMode,
    },
  ];

  return events.map(e => JSON.stringify(e)).join('\n') + '\n';
}

export async function enableABMode(page: Page) {
  await page.waitForFunction(() => typeof (window as any).__ARCHI_PLAYWRIGHT__?.ab !== 'undefined');
  await page.evaluate(() => {
    (window as any).__ARCHI_PLAYWRIGHT__.ab.patchPoolState({
      enabled: true,
      champion: 'Baseline',
      variants: ['Baseline', 'Poet'],
      max_pending_comparisons_per_conversation: 1,
    });
  });
}

export async function clearStorage(page: Page) {
  // Note: This must be called AFTER page.goto() - the page needs to be at a URL first
  await page.evaluate(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
}

// =============================================================================
// Custom Test Fixture
// =============================================================================

type ChatFixtures = {
  chatPage: Page;
};

export const test = base.extend<ChatFixtures>({
  chatPage: async ({ page }, use) => {
    await setupBasicMocks(page);
    await page.goto('/chat');
    await use(page);
  },
});

export { expect };
