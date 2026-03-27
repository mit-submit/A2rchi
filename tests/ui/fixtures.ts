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

  agents: {
    agents: [
      { name: 'CMS Comp Ops', filename: 'cms-comp-ops.md' },
      { name: 'Test Agent', filename: 'test-agent.md' },
    ],
    active_name: 'CMS Comp Ops',
  },

  agentTemplate: {
    name: 'New Agent',
    tools: [
      { name: 'search_knowledge_base', description: 'Search the knowledge base' },
      { name: 'search_local_files', description: 'Search uploaded local files' },
      { name: 'search_metadata_index', description: 'Search the metadata index' },
    ],
    template: '---\nname: New Agent\ntools:\n  - search_knowledge_base\n  - search_local_files\n  - search_metadata_index\n---\n\nWrite your system prompt here.\n\n',
  },
};

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

  await page.route('**/api/new_conversation', async (route) => {
    await route.fulfill({ status: 200, json: { conversation_id: null } });
  });

  await page.route('**/api/agents/list', async (route) => {
    await route.fulfill({ status: 200, json: mockData.agents });
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

export async function enableABMode(page: Page) {
  // Dismiss warning for session
  await page.evaluate(() => {
    sessionStorage.setItem('archi_ab_warning_dismissed', 'true');
  });
  
  // Open settings and enable A/B
  await page.getByRole('button', { name: 'Settings' }).click();
  await page.locator('.settings-nav-item[data-section="advanced"]').click();
  await page.locator('#ab-checkbox').check();
  await page.getByRole('button', { name: 'Close settings' }).click();
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
