/**
 * Workflow 22: Copilot SDK Streaming Edge Cases
 *
 * Tests for edge cases in the Copilot SDK agent streaming pipeline:
 * multiple tool calls, thinking + tools combos, error events,
 * orphan tool completions, and usage accumulation.
 */
import { test, expect, setupBasicMocks, createToolCallEvents } from '../fixtures';

test.describe('Multiple Tool Calls', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('response with two sequential tool calls displays both', async ({ page }) => {
    const tool1 = createToolCallEvents('search_knowledge_base', { query: 'Rucio' }, 'KB results', {
      toolCallId: 'tc_1', durationMs: 200,
    });
    const tool2 = createToolCallEvents('search_local_files', { query: 'logs' }, 'File results', {
      toolCallId: 'tc_2', durationMs: 350,
    });

    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        ...tool1.map(e => JSON.stringify(e)),
        ...tool2.map(e => JSON.stringify(e)),
        '{"type":"final","response":"Based on both searches, here is the answer.","message_id":1,"user_message_id":1,"conversation_id":1}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Search everything');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByText('Based on both searches')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Agent Activity')).toBeVisible();

    // Expand trace to verify both tools are listed
    await page.locator('.trace-toggle').click();
    await expect(page.locator('.trace-container:not(.collapsed)')).toBeVisible({ timeout: 2000 });

    const toolSteps = page.locator('.tool-step');
    await expect(toolSteps).toHaveCount(2);
  });

  test('interleaved thinking and tool calls render correctly', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"thinking_start","step_id":"think_1"}',
        '{"type":"thinking_end","step_id":"think_1","duration_ms":300,"thinking_content":"Let me search the knowledge base first."}',
        '{"type":"tool_start","tool_call_id":"tc_1","tool_name":"search_knowledge_base","tool_args":{"query":"Rucio transfers"}}',
        '{"type":"tool_output","tool_call_id":"tc_1","output":"Found 3 documents about Rucio."}',
        '{"type":"tool_end","tool_call_id":"tc_1","status":"success","duration_ms":187}',
        '{"type":"thinking_start","step_id":"think_2"}',
        '{"type":"thinking_end","step_id":"think_2","duration_ms":150,"thinking_content":"Now let me check the local files too."}',
        '{"type":"tool_start","tool_call_id":"tc_2","tool_name":"search_local_files","tool_args":{"query":"Rucio errors"}}',
        '{"type":"tool_output","tool_call_id":"tc_2","output":"Found log entries."}',
        '{"type":"tool_end","tool_call_id":"tc_2","status":"success","duration_ms":443}',
        '{"type":"final","response":"Here is the combined answer.","message_id":1,"user_message_id":1,"conversation_id":1}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Search Rucio');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByText('Here is the combined answer')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Agent Activity')).toBeVisible();

    // Expand trace
    await page.locator('.trace-toggle').click();
    await expect(page.locator('.trace-container:not(.collapsed)')).toBeVisible({ timeout: 2000 });

    // Should have 2 thinking steps and 2 tool steps
    const thinkingSteps = page.locator('.thinking-step');
    const toolSteps = page.locator('.tool-step');
    await expect(toolSteps).toHaveCount(2);
    await expect(thinkingSteps).toHaveCount(2);
  });
});

test.describe('Tool Call Edge Cases', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('tool_end without tool_start still renders response', async ({ page }) => {
    // Orphan tool completion — the backend logs a warning but still
    // emits the events. The UI should not crash.
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"tool_output","tool_call_id":"orphan_1","output":"Orphan result"}',
        '{"type":"tool_end","tool_call_id":"orphan_1","status":"success","duration_ms":null}',
        '{"type":"final","response":"Answer despite orphan tool event.","message_id":1,"user_message_id":1,"conversation_id":1}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Test orphan');
    await page.getByRole('button', { name: 'Send message' }).click();

    // Should still show the final response without crashing
    await expect(page.getByText('Answer despite orphan tool event')).toBeVisible({ timeout: 5000 });
  });

  test('tool call with error status displays correctly', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"tool_start","tool_call_id":"tc_err","tool_name":"search_metadata_index","tool_args":{"query":"bad"}}',
        '{"type":"tool_output","tool_call_id":"tc_err","output":"Error: connection timeout"}',
        '{"type":"tool_end","tool_call_id":"tc_err","status":"error","duration_ms":5000}',
        '{"type":"final","response":"The search encountered an error.","message_id":1,"user_message_id":1,"conversation_id":1}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Search metadata');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByText('The search encountered an error')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Agent Activity')).toBeVisible();
  });

  test('tool with empty output does not break rendering', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"tool_start","tool_call_id":"tc_empty","tool_name":"list_metadata_schema","tool_args":{}}',
        '{"type":"tool_output","tool_call_id":"tc_empty","output":""}',
        '{"type":"tool_end","tool_call_id":"tc_empty","status":"success","duration_ms":50}',
        '{"type":"final","response":"Schema is empty.","message_id":1,"user_message_id":1,"conversation_id":1}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('List schema');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByText('Schema is empty')).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Streaming Error Events', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('error event mid-stream does not freeze the UI', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      // Error event replaces any prior chunk content and returns immediately
      const events = [
        '{"type":"chunk","content":"Starting to answer..."}',
        '{"type":"error","message":"Model rate limit exceeded"}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Test error');
    await page.getByRole('button', { name: 'Send message' }).click();

    // Error message should be displayed
    await expect(page.getByText('Model rate limit exceeded')).toBeVisible({ timeout: 5000 });

    // Input should be re-enabled (not frozen)
    await expect(page.getByLabel('Message input')).not.toBeDisabled({ timeout: 5000 });
  });

  test('HTTP 500 during stream shows error state', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      await route.fulfill({ status: 500, body: 'Internal Server Error' });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Break me');
    await page.getByRole('button', { name: 'Send message' }).click();

    // Input should be re-enabled after error
    await expect(page.getByLabel('Message input')).not.toBeDisabled({ timeout: 5000 });
  });

  test('network timeout shows error and re-enables input', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      await route.abort('timedout');
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Timeout test');
    await page.getByRole('button', { name: 'Send message' }).click();

    // Input should be re-enabled after timeout
    await expect(page.getByLabel('Message input')).not.toBeDisabled({ timeout: 10000 });
  });
});

test.describe('Usage & Metadata', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('accumulated usage from multiple API calls displays correctly', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"thinking_start","step_id":"t1"}',
        '{"type":"thinking_end","step_id":"t1","duration_ms":100,"thinking_content":"Planning..."}',
        '{"type":"tool_start","tool_call_id":"tc_1","tool_name":"search_knowledge_base","tool_args":{"query":"test"}}',
        '{"type":"tool_output","tool_call_id":"tc_1","output":"Results"}',
        '{"type":"tool_end","tool_call_id":"tc_1","status":"success","duration_ms":200}',
        '{"type":"final","response":"Done.","message_id":1,"user_message_id":1,"conversation_id":1,"usage":{"prompt_tokens":500,"completion_tokens":200,"total_tokens":700}}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Test');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByText('Agent Activity')).toBeVisible({ timeout: 5000 });

    // Expand trace to see the context meter
    await page.locator('.trace-toggle').click();
    await expect(page.locator('.trace-container:not(.collapsed)')).toBeVisible({ timeout: 2000 });

    // Meter label should show accumulated token usage
    await expect(page.locator('.meter-label')).toBeVisible();
  });

  test('response without usage data still displays', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"final","response":"No usage data.","message_id":1,"user_message_id":1,"conversation_id":1}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Hello');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByText('No usage data')).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Thinking Edge Cases', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('thinking without tool calls still shows activity', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"thinking_start","step_id":"t1"}',
        '{"type":"thinking_end","step_id":"t1","duration_ms":800,"thinking_content":"Let me think about this carefully..."}',
        '{"type":"final","response":"Here is my thoughtful answer.","message_id":1,"user_message_id":1,"conversation_id":1}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Think deeply');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByText('Here is my thoughtful answer')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Agent Activity')).toBeVisible();
  });

  test('multiple thinking phases render as separate steps', async ({ page }) => {
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"thinking_start","step_id":"t1"}',
        '{"type":"thinking_end","step_id":"t1","duration_ms":200,"thinking_content":"First thought..."}',
        '{"type":"thinking_start","step_id":"t2"}',
        '{"type":"thinking_end","step_id":"t2","duration_ms":300,"thinking_content":"Second thought..."}',
        '{"type":"thinking_start","step_id":"t3"}',
        '{"type":"thinking_end","step_id":"t3","duration_ms":100,"thinking_content":"Final thought."}',
        '{"type":"final","response":"After much deliberation.","message_id":1,"user_message_id":1,"conversation_id":1}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Think three times');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByText('After much deliberation')).toBeVisible({ timeout: 5000 });

    // Expand trace
    await page.locator('.trace-toggle').click();
    await expect(page.locator('.trace-container:not(.collapsed)')).toBeVisible({ timeout: 2000 });

    // Should have 3 thinking steps
    const thinkingSteps = page.locator('.thinking-step');
    await expect(thinkingSteps).toHaveCount(3);
  });

  test('thinking_end without thinking_start does not crash', async ({ page }) => {
    // Edge case: orphan thinking_end event
    await page.route('**/api/get_chat_response_stream', async (route) => {
      const events = [
        '{"type":"thinking_end","step_id":"orphan","duration_ms":0,"thinking_content":""}',
        '{"type":"final","response":"Normal response.","message_id":1,"user_message_id":1,"conversation_id":1}',
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: events.join('\n') + '\n',
      });
    });

    await page.goto('/chat');

    await page.getByLabel('Message input').fill('Edge case');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect(page.getByText('Normal response')).toBeVisible({ timeout: 5000 });
  });
});
