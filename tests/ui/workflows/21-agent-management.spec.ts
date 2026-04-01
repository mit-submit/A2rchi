/**
 * Workflow 21: Agent Management Tests
 *
 * Tests for agent CRUD operations: listing, creating, editing,
 * activating, and deleting custom agents via the agent dropdown
 * and agent spec editor.
 */
import { test, expect, setupBasicMocks, mockData } from '../fixtures';

test.describe('Agent Dropdown', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('agent dropdown shows active agent name', async ({ page }) => {
    await page.goto('/chat');

    const label = page.locator('.agent-dropdown-label');
    await expect(label).toBeVisible();
    await expect(label).toContainText('CMS Comp Ops');
  });

  test('agent dropdown lists all agents with active checkmark', async ({ page }) => {
    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    const items = page.locator('.agent-dropdown-item');
    await expect(items).toHaveCount(2);

    // Active agent has .active class
    await expect(items.first()).toHaveClass(/active/);
  });

  test('clicking agent item activates it', async ({ page }) => {
    let activatePayload: any = null;

    await page.route('**/api/agents/active', async (route) => {
      activatePayload = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        json: { success: true, active_name: activatePayload.name },
      });
    });

    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    // Click on the second (non-active) agent
    await page.locator('.agent-dropdown-item').nth(1).locator('.agent-dropdown-name').click();

    // Verify API was called
    expect(activatePayload).not.toBeNull();
    expect(activatePayload.name).toBe('Test Agent');
  });

  test('dropdown shows edit and delete buttons per agent', async ({ page }) => {
    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    const firstItem = page.locator('.agent-dropdown-item').first();
    await expect(firstItem.locator('.agent-dropdown-edit')).toBeVisible();
    await expect(firstItem.locator('.agent-dropdown-delete')).toBeVisible();
  });

  test('delete shows confirmation and completes on confirm', async ({ page }) => {
    let deletePayload: any = null;

    await page.route('**/api/agents', async (route) => {
      if (route.request().method() === 'DELETE') {
        deletePayload = route.request().postDataJSON();
        await route.fulfill({ status: 200, json: { success: true, deleted: deletePayload.name } });
      } else {
        await route.continue();
      }
    });

    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    await expect(page.locator('.agent-dropdown-menu')).not.toHaveAttribute('hidden', '');

    // Click delete on the second agent — replaces row with confirmation prompt.
    // The dropdown may close on click propagation, so use dispatchEvent to
    // perform the confirmation entirely within the dropdown's click handler.
    await page.locator('.agent-dropdown-item').nth(1).locator('.agent-dropdown-delete').click();

    // Re-open the dropdown if it closed after the delete button click
    const menu = page.locator('.agent-dropdown-menu');
    if (await menu.getAttribute('hidden') !== null) {
      await page.locator('.agent-dropdown-btn').click();
    }

    // Click the "Delete" confirmation button
    await page.locator('.agent-dropdown-confirm-yes').click({ force: true });

    expect(deletePayload).not.toBeNull();
  });
});

test.describe('Agent Spec Editor — Create', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);

    await page.route('**/api/agents/template*', async (route) => {
      await route.fulfill({ status: 200, json: mockData.agentTemplate });
    });
  });

  test('add button opens agent spec editor in create mode', async ({ page }) => {
    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    await page.locator('.agent-dropdown-add').click();

    const modal = page.locator('.agent-spec-modal');
    await expect(modal).toBeVisible();

    // Title should say "New Agent"
    const title = page.locator('#agent-spec-title');
    await expect(title).toContainText('New Agent');
  });

  test('create mode loads template with tool palette', async ({ page }) => {
    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    await page.locator('.agent-dropdown-add').click();

    // Tool palette should list tools from template
    const toolsList = page.locator('.agent-spec-tools-list');
    await expect(toolsList).toBeVisible({ timeout: 3000 });
  });

  test('saving agent spec calls POST /api/agents', async ({ page }) => {
    let savePayload: any = null;

    await page.route('**/api/agents', async (route) => {
      if (route.request().method() === 'POST') {
        savePayload = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          json: { success: true, name: 'My Custom Agent', filename: 'my-custom-agent.md' },
        });
      } else {
        await route.continue();
      }
    });

    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    await page.locator('.agent-dropdown-add').click();

    // Fill in the name
    const nameInput = page.locator('#agent-spec-name');
    await nameInput.fill('My Custom Agent');

    // Fill in the prompt
    const promptInput = page.locator('#agent-spec-prompt');
    await promptInput.fill('You are a helpful assistant.');

    // Click Save
    await page.locator('.agent-spec-save').click();

    // Verify save was called
    expect(savePayload).not.toBeNull();
    expect(savePayload.content).toBeDefined();
  });

  test('close button closes the spec editor', async ({ page }) => {
    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    await page.locator('.agent-dropdown-add').click();

    await expect(page.locator('.agent-spec-modal')).toBeVisible();
    await page.locator('.agent-spec-close').click();
    await expect(page.locator('.agent-spec-modal')).not.toBeVisible();
  });

  test('Escape key closes the spec editor', async ({ page }) => {
    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    await page.locator('.agent-dropdown-add').click();

    await expect(page.locator('.agent-spec-modal')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.agent-spec-modal')).not.toBeVisible();
  });
});

test.describe('Agent Spec Editor — Edit', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);

    await page.route('**/api/agents/template*', async (route) => {
      await route.fulfill({ status: 200, json: mockData.agentTemplate });
    });

    await page.route('**/api/agents/spec*', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          name: 'CMS Comp Ops',
          filename: 'cms-comp-ops.md',
          content: '---\nname: CMS Comp Ops\ntools:\n  - search_knowledge_base\n  - search_local_files\n---\n\nYou are a CMS Computing Operations assistant.\n\n',
        },
      });
    });
  });

  test('edit button opens spec editor in edit mode', async ({ page }) => {
    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    // Click edit on the first agent
    await page.locator('.agent-dropdown-item').first().locator('.agent-dropdown-edit').click();

    const modal = page.locator('.agent-spec-modal');
    await expect(modal).toBeVisible();

    // Title should show edit mode
    const title = page.locator('#agent-spec-title');
    await expect(title).toContainText('Edit');
  });

  test('edit mode loads existing agent content', async ({ page }) => {
    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    await page.locator('.agent-dropdown-item').first().locator('.agent-dropdown-edit').click();

    // Name should be populated
    const nameInput = page.locator('#agent-spec-name');
    await expect(nameInput).toHaveValue('CMS Comp Ops', { timeout: 3000 });
  });

  test('save in edit mode sends existing_name', async ({ page }) => {
    let savePayload: any = null;

    await page.route('**/api/agents', async (route) => {
      if (route.request().method() === 'POST') {
        savePayload = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          json: { success: true, name: 'CMS Comp Ops', filename: 'cms-comp-ops.md' },
        });
      } else {
        await route.continue();
      }
    });

    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    await page.locator('.agent-dropdown-item').first().locator('.agent-dropdown-edit').click();

    // Wait for content to load
    await expect(page.locator('#agent-spec-name')).toHaveValue('CMS Comp Ops', { timeout: 3000 });

    // Modify prompt
    const promptInput = page.locator('#agent-spec-prompt');
    await promptInput.fill('Updated prompt for CMS Comp Ops.');

    // Save
    await page.locator('.agent-spec-save').click();

    expect(savePayload).not.toBeNull();
    expect(savePayload.mode).toBe('edit');
    expect(savePayload.existing_name).toBe('CMS Comp Ops');
  });
});

test.describe('Agent Error Handling', () => {
  test.beforeEach(async ({ page }) => {
    await setupBasicMocks(page);
  });

  test('duplicate agent name shows error', async ({ page }) => {
    await page.route('**/api/agents/template*', async (route) => {
      await route.fulfill({ status: 200, json: mockData.agentTemplate });
    });

    await page.route('**/api/agents', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 409,
          json: { error: "Agent name 'CMS Comp Ops' already exists" },
        });
      } else {
        await route.continue();
      }
    });

    await page.goto('/chat');

    await page.locator('.agent-dropdown-btn').click();
    await page.locator('.agent-dropdown-add').click();

    const nameInput = page.locator('#agent-spec-name');
    await nameInput.fill('CMS Comp Ops');

    const promptInput = page.locator('#agent-spec-prompt');
    await promptInput.fill('Duplicate test.');

    await page.locator('.agent-spec-save').click();

    // Should show an error status
    const status = page.locator('#agent-spec-status');
    await expect(status).toBeVisible({ timeout: 3000 });
    await expect(status).toContainText('already exists');
  });

  test('agent list handles API failure gracefully', async ({ page }) => {
    // Override the agents mock to return an error
    await page.route('**/api/agents/list', async (route) => {
      await route.fulfill({ status: 500, json: { error: 'Database error' } });
    });

    await page.goto('/chat');

    // Page should still load even if agents list fails
    await expect(page.getByLabel('Message input')).toBeVisible();
  });
});
