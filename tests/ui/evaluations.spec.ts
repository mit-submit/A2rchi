import { expect, test } from "@playwright/test";

test.describe("QA evaluation console", () => {
  test.describe.configure({ mode: "serial" });

  test("imports, reviews atoms, saves a child dataset, launches, and opens history", async ({ page }) => {
    await page.goto("/evaluations");
    await expect(page.getByRole("heading", { name: "Evidence, not dashboard theatre." })).toBeVisible();

    await page.getByRole("button", { name: /Datasets/ }).click();
    await page.getByLabel("Name").first().fill("Mock dataset");
    await page.getByLabel("Dataset file").setInputFiles({
      name: "mock-dataset.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify([
        {
          id: "mock-entry",
          question: "What value does the mock entry contain?",
          answer: "The mock entry contains 42.",
          time_sensitive: false,
          category: "test-fixture",
          answer_source: "mocked-entry",
        },
      ])),
    });
    await page.getByRole("button", { name: "Validate and import" }).click();
    await expect(page.getByText("Mock dataset", { exact: true }).first()).toBeVisible();
    const parentDataset = page.locator("#dataset-list [data-dataset-id]").filter({
      has: page.getByText("Mock dataset", { exact: true }),
    });
    const parentDatasetId = await parentDataset.getAttribute("data-dataset-id");
    expect(parentDatasetId).not.toBeNull();
    await parentDataset.click();

    await expect(page.getByLabel("Atom generation profile")).toBeVisible();
    await page.getByRole("button", { name: "Generate Atoms" }).click();
    await expect(page.getByRole("dialog")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Close" }).click();
    await page.reload();
    await page.getByRole("button", { name: /Datasets/ }).click();
    await page.locator(`[data-dataset-id="${parentDatasetId}"]`).click();
    await page.getByRole("button", { name: "Resume atom review" }).click();
    await expect(page.getByRole("dialog", { name: "Review atoms · Mock dataset" })).toBeVisible();
    const questionPanels = page.locator("#atom-editor details");
    await expect(questionPanels).toHaveCount(1);
    const firstPanel = questionPanels.first();
    await expect(firstPanel).toHaveAttribute("open", "");
    await expect(firstPanel.locator("summary")).toContainText("What value does the mock entry contain?");
    await expect(firstPanel.getByText("Expected answer", { exact: true })).toBeVisible();
    await expect(firstPanel.getByText("Atoms", { exact: true })).toBeVisible();
    await expect(firstPanel.getByText("1 atom · 1 required", { exact: true })).toBeVisible();
    const firstAtomText = firstPanel.getByLabel(/Atom text 1 for/);
    const requiredToggle = firstPanel.getByLabel(/Required for atom 1 in/);
    await expect(firstAtomText).toBeVisible();
    await expect(firstPanel.locator(".expected-answer")).toContainText("The mock entry contains 42.");
    await expect(firstAtomText).toHaveValue("The mock entry contains 42.");
    expect(await questionPanels.evaluateAll((panels) => panels.every((panel) => {
      const question = panel.querySelector(".atom-summary-copy strong")?.textContent?.trim();
      const answer = panel.querySelector(".expected-answer > div")?.textContent?.trim();
      const atoms = [...panel.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("[data-field=text]")];
      return Boolean(question && answer && atoms.length && atoms.every((atom) => atom.value.trim()));
    }))).toBe(true);
    expect(await firstPanel.evaluate((panel) => {
      const atom = panel.querySelector("[data-atom-row]");
      return atom !== null && atom.getBoundingClientRect().bottom <= panel.getBoundingClientRect().bottom;
    })).toBe(true);
    const reviewedDatasetName = page.getByLabel("New dataset name");
    await reviewedDatasetName.fill("   ");
    await page.getByRole("button", { name: "Save as new dataset" }).click();
    await expect(page.getByText("Enter a name for the reviewed dataset.", { exact: true })).toBeVisible();
    await expect(reviewedDatasetName).toBeFocused();
    await reviewedDatasetName.fill("Mock dataset · reviewed");
    await expect(page.getByText("Enter a name for the reviewed dataset.", { exact: true })).toHaveCount(0);
    const firstAtomId = firstPanel.getByLabel(/Atom ID 1 for/);
    await firstAtomId.fill("A1-reviewed");
    await firstAtomId.press("Enter");
    await expect(page.getByRole("dialog", { name: "Review atoms · Mock dataset" })).toBeVisible();
    await expect(firstAtomId).toHaveValue("A1-reviewed");
    await expect(requiredToggle).toBeChecked();
    await requiredToggle.uncheck();
    await expect(requiredToggle).not.toBeChecked();
    await expect(firstPanel.getByText("1 atom · 0 required", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Save as new dataset" }).click();
    await expect(firstPanel.getByRole("alert")).toHaveText("At least one atom must be required.");
    await expect(requiredToggle).toBeFocused();
    await requiredToggle.check();
    await expect(firstPanel.getByRole("alert")).toHaveCount(0);
    await expect(firstPanel.getByText("1 atom · 1 required", { exact: true })).toBeVisible();
    await firstPanel.getByRole("button", { name: /Add atom to/ }).click();
    const addedAtomText = firstPanel.getByLabel(/Atom text 2 for/);
    const addedAtomId = firstPanel.getByLabel(/Atom ID 2 for/);
    const addedRequired = firstPanel.getByLabel(/Required for atom 2 in/);
    await expect(addedAtomText).toBeFocused();
    await addedAtomId.fill("A1-reviewed");
    await requiredToggle.uncheck();
    await addedRequired.uncheck();
    await page.getByRole("button", { name: "Save as new dataset" }).click();
    await expect(firstPanel.getByRole("alert")).toContainText("Atom IDs must be unique within this question.");
    await expect(firstPanel.getByRole("alert")).toContainText("Every atom needs text.");
    await expect(firstPanel.getByRole("alert")).toContainText("At least one atom must be required.");
    await addedAtomText.fill("Optional supporting obligation");
    await expect(firstPanel.getByRole("alert")).not.toContainText("Every atom needs text.");
    await expect(firstPanel.getByRole("alert")).toContainText("Atom IDs must be unique within this question.");
    await expect(firstPanel.getByRole("alert")).toContainText("At least one atom must be required.");
    await firstPanel.getByRole("button", { name: /Remove atom 2 from/ }).click();
    await expect(firstPanel.getByRole("alert")).toHaveText("At least one atom must be required.");
    await requiredToggle.check();
    await expect(firstPanel.getByRole("alert")).toHaveCount(0);
    await firstPanel.getByRole("button", { name: /Add atom to/ }).click();
    await firstPanel.getByLabel(/Atom text 2 for/).fill("Optional supporting obligation");
    await firstAtomText.fill("Human-reviewed golden obligation");
    await page.getByRole("button", { name: "Save as new dataset" }).click();
    await expect(page.locator("#dataset-detail").getByRole("heading", {
      name: "Mock dataset · reviewed",
    })).toBeVisible();

    await page.locator(`[data-dataset-id="${parentDatasetId}"]`).click();
    await expect(page.getByRole("button", { name: "Resume atom review" })).toHaveCount(0);
    await page.locator("#dataset-list [data-dataset-id]").filter({
      has: page.getByText("Mock dataset · reviewed", { exact: true }),
    }).first().click();
    await expect(page.getByRole("button", { name: "Review Atoms" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Generate Atoms" })).toHaveCount(0);
    await expect(page.getByLabel("Atom generation profile")).toHaveCount(0);
    await page.getByRole("button", { name: "Evaluate" }).click();
    await page.getByLabel("Evaluation name").fill("Browser reviewed run");
    await page.getByRole("button", { name: "Start evaluation" }).click();
    await expect(page.getByRole("heading", { name: "Browser reviewed run" })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".evidence-card").getByText("100.0%", { exact: true }).first()).toBeVisible();

    await page.getByRole("button", { name: "← All runs" }).click();
    await expect(page.locator("#runs-body").getByText("Browser reviewed run", { exact: true })).toBeVisible();
  });

  test("reviews a partially atomized dataset without generating missing atoms", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (request) => requests.push(request.url()));
    await page.goto("/evaluations");
    await page.getByRole("button", { name: /Datasets/ }).click();
    await page.getByLabel("Name").first().fill("Partially atomized");
    await page.getByLabel("Dataset file").setInputFiles({
      name: "partial.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify([
        {
          id: "with-atom",
          question: "Which row already has an atom?",
          answer: "This one.",
          time_sensitive: false,
          expected_atoms: [
            { id: "A1", text: "This row already has an atom.", required: true },
          ],
        },
        {
          id: "without-atom",
          question: "Which row needs atoms?",
          answer: "This other one.",
          answer_source: "curated knowledge",
          time_sensitive: false,
        },
      ])),
    });
    await page.getByRole("button", { name: "Validate and import" }).click();
    await expect(page.getByText("Partially atomized", { exact: true }).first()).toBeVisible();
    await page.locator("#dataset-list [data-dataset-id]").filter({
      has: page.getByText("Partially atomized", { exact: true }),
    }).click();

    await expect(page.getByRole("button", { name: "Review Atoms" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Generate Atoms" })).toHaveCount(0);
    await expect(page.getByLabel("Atom generation profile")).toHaveCount(0);
    const reviewButton = page.getByRole("button", { name: "Review Atoms" });
    await reviewButton.click();

    const dialog = page.getByRole("dialog", { name: "Review atoms · Partially atomized" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(reviewButton).toBeFocused();
    await reviewButton.click();
    await expect(dialog).toBeVisible();
    const panels = dialog.locator("#atom-editor details");
    await expect(panels).toHaveCount(2);
    await expect(panels.first().getByLabel("Atom text 1 for with-atom")).toHaveValue(
      "This row already has an atom.",
    );
    await panels.nth(1).locator("summary").click();
    await expect(panels.nth(1).getByText("Source: curated knowledge")).toBeVisible();
    await expect(panels.nth(1).getByText("No atoms yet for this answer.")).toBeVisible();
    await expect(panels.nth(1).locator("[data-atom-row]")).toHaveCount(0);
    await panels.nth(1).getByRole("button", { name: "Add atom to without-atom" }).click();
    await expect(panels.nth(1).getByLabel("Atom text 1 for without-atom")).toHaveValue("");
    expect(requests.some((url) => url.endsWith("/generate-atoms"))).toBe(false);
    expect(requests.some((url) => url.endsWith("/review-atoms"))).toBe(true);
  });

  test("groups result evidence by question with readable scores, answers, and atom judgments", async ({ page }) => {
    const historyId = "human-readable-results";
    const question = "How does Archi preserve evidence across evaluation attempts?";
    const atoms = [
      { id: "A1", text: "The run stores immutable input snapshots.", required: true },
      { id: "A2", text: "Each attempt retains its full model answer.", required: true },
      { id: "A3", text: "Evaluator judgments remain inspectable per atom.", required: false },
    ];
    const results = [
      {
        item_id: "q-human",
        attempt_id: "q-human-attempt-1",
        ordinal: 1,
        status: "scored",
        passed: true,
        atom_score: 1,
        judgments: atoms.map((atom) => ({
          atom_id: atom.id,
          outcome: "entailed",
          rationale: "The first response contains this expected content.",
        })),
      },
      {
        item_id: "q-human",
        attempt_id: "q-human-attempt-2",
        ordinal: 2,
        status: "scored",
        passed: false,
        atom_score: 0.25,
        judgments: [
          {
            atom_id: "A1",
            outcome: "entailed",
            rationale: "The immutable snapshot is described.",
          },
          {
            atom_id: "A2",
            outcome: "not_mentioned",
            rationale: "The answer does not mention preserving the full model response.",
          },
          {
            atom_id: "A3",
            outcome: "contradicted",
            rationale: "The answer incorrectly says atom judgments are discarded.",
          },
        ],
      },
    ];

    await page.route(`**/api/evaluations/runs/${historyId}`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          run: {
            manifest: {
              run_id: "readable-run",
              schema_version: "qa-v1",
              status: "scored",
            },
            metadata: {
              name: "Readable evidence run",
              dataset_name: "Human review set",
              agent_spec: "qa-agent.md",
            },
            summary: {
              overall_attempt_pass_rate: 0.5,
              passed_attempts: 1,
              quality_accounted_attempts: 2,
              macro_mean_scored_attempt_required_atom_recall: 0.75,
              attempt_lifecycle_counts: {
                scored: 2,
                execution_failed: 0,
                evaluation_failed: 0,
              },
            },
            prepared_items: [{
              item_id: "q-human",
              question,
              gold_atoms: atoms,
            }],
            answers: [
              {
                attempt_id: "q-human-attempt-1",
                item_id: "q-human",
                ordinal: 1,
                duration_ms: 1200,
                tool_calls: [
                  { ordinal: 1, name: "search", status: "success", duration_ms: 200 },
                  { ordinal: 2, name: "lookup", status: "success", duration_ms: 100 },
                ],
                answer: "Archi stores immutable snapshots, full answers, and per-atom judgments.",
              },
              {
                attempt_id: "q-human-attempt-2",
                item_id: "q-human",
                ordinal: 2,
                duration_ms: 2800,
                tool_calls: [
                  { ordinal: 1, name: "search", status: "success", duration_ms: 1900 },
                  { ordinal: 2, name: "lookup", status: "error", duration_ms: 1400 },
                ],
                answer: "Archi stores snapshots but discards detailed atom judgments.",
              },
            ],
            evaluation_results: results,
            report_available: false,
          },
        }),
      });
    });
    await page.route("**/api/evaluations/runs", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          runs: [{
            id: historyId,
            valid: true,
            name: "Readable evidence run",
            status: "scored",
            attempts: 2,
            overall_attempt_pass_rate: 0.5,
          }],
        }),
      });
    });

    await page.goto("/evaluations");
    await page.locator(`[data-run-id="${historyId}"]`).click();
    await expect(page.getByRole("heading", { name: "Readable evidence run" })).toBeVisible();
    const latencyPanel = page.locator(".latency-panel");
    await expect(latencyPanel.getByRole("heading", { name: "Latency per question" })).toBeVisible();
    await expect(latencyPanel.getByRole("listitem")).toHaveCount(1);
    await expect(latencyPanel).toContainText(question);
    const latencyItem = latencyPanel.locator(".latency-item");
    const attemptSelector = latencyItem.getByLabel("Attempt for q-human");
    await expect(attemptSelector).toHaveValue("q-human-attempt-1");
    await expect(latencyItem.locator(".latency-total-value")).toHaveText("1.20 s total");
    await expect(latencyItem.locator(".latency-tool-value")).toHaveText("300 ms tools");
    await expect(latencyItem.locator(".latency-other-value")).toHaveText("900 ms other agent time");
    await expect(latencyItem).toContainText("2 tool calls");
    const latencyBar = latencyItem.locator(".latency-bar");
    await expect(latencyBar).toHaveAttribute("style", /height:\s*42\.86%/);
    await expect(latencyItem.locator(".latency-tool-segment")).toHaveAttribute(
      "style",
      /height:\s*25(\.00)?%/,
    );
    expect(await latencyBar.evaluate((bar) => getComputedStyle(bar).transitionProperty)).toContain(
      "height",
    );

    await attemptSelector.selectOption("q-human-attempt-2");
    await expect(latencyItem.locator(".latency-total-value")).toHaveText("2.80 s total");
    await expect(latencyItem.locator(".latency-tool-value")).toHaveText("3.30 s tools");
    await expect(latencyItem.locator(".latency-other-value")).toHaveText("0 ms other agent time");
    await expect(latencyBar).toHaveAttribute("style", /height:\s*100(\.00)?%/);
    await expect(latencyItem.locator(".latency-tool-segment")).toHaveAttribute(
      "style",
      /height:\s*100(\.00)?%/,
    );
    await expect(page.locator("#run-detail-content > :first-child")).toHaveClass(/latency-panel/);
    await expect(page.getByText("Atoms recall", { exact: true })).toBeVisible();
    await expect(page.getByText("Scored attempts", { exact: true })).toHaveCount(0);
    await expect(page.getByText("66.7%", { exact: true })).toBeVisible();
    const atomRecallInfo = page.getByRole("button", { name: "About atoms recall" });
    await atomRecallInfo.hover();
    const atomRecallHelp = page.getByRole("tooltip", { name: /all atoms marked as entailed/ });
    await expect(atomRecallHelp).toBeVisible();
    await expect(atomRecallHelp).toContainText("including required and optional atoms");
    await expect(atomRecallHelp).toContainText("contradictions also reduce the separate atom score");

    await expect(page.getByText("Required atoms recall", { exact: true })).toBeVisible();
    const recallInfo = page.getByRole("button", { name: "About required atoms recall" });
    await recallInfo.hover();
    const recallHelp = page.getByRole("tooltip", { name: /required atoms marked as entailed/ });
    await expect(recallHelp).toBeVisible();
    await expect(recallHelp).toContainText("required atoms marked as entailed ÷ all required atoms");
    await expect(recallHelp).toContainText("Aim for 100%");

    const questionGroup = page.locator(".question-result");
    await expect(questionGroup).toHaveCount(1);
    const questionSummary = questionGroup.locator(":scope > summary");
    await expect(questionSummary).toContainText("q-human");
    await expect(questionSummary).toContainText(question);
    await expect(questionSummary).toContainText("Average score");
    await expect(questionSummary).toContainText("62.5%");
    await expect(questionSummary).toContainText("Best A1 100.0% · Worst A2 25.0%");
    await expect(page.getByLabel("Average atom score for q-human")).toHaveAttribute(
      "value",
      "0.625",
    );

    await questionSummary.click();
    const userQuestion = questionGroup.locator(".user-question");
    await expect(userQuestion).toHaveAttribute("open", "");
    await expect(userQuestion.locator(".evidence-copy")).toHaveText(question);

    const attempts = questionGroup.locator(".attempt-result");
    await expect(attempts).toHaveCount(2);
    await expect(attempts.nth(0).locator(":scope > summary")).toContainText("Attempt 1");
    await expect(attempts.nth(0).locator(":scope > summary")).toContainText("100.0%");
    await expect(attempts.nth(1).locator(":scope > summary")).toContainText("Attempt 2");
    await expect(attempts.nth(1).locator(":scope > summary")).toContainText("25.0%");

    await attempts.nth(1).locator(":scope > summary").click();
    const answer = attempts.nth(1).locator(".model-answer");
    await expect(answer).toHaveAttribute("open", "");
    await expect(answer.locator(".evidence-copy")).toContainText(
      "discards detailed atom judgments",
    );

    const judgments = attempts.nth(1).locator(".atom-judgment");
    await expect(judgments).toHaveCount(3);
    await expect(judgments.nth(0).locator(".atom-outcome")).toHaveText("Passed");
    await expect(judgments.nth(0)).toHaveClass(/outcome-passed/);
    await expect(judgments.nth(1).locator(".atom-outcome")).toHaveText("Not mentioned");
    await expect(judgments.nth(1)).toHaveClass(/outcome-not-mentioned/);
    await expect(judgments.nth(2).locator(".atom-outcome")).toHaveText("Contradicted");
    await expect(judgments.nth(2)).toHaveClass(/outcome-contradicted/);
    await expect(judgments.nth(2).getByText("Expected content", { exact: true })).toBeVisible();
    await expect(judgments.nth(2).getByText("Evaluator judgment", { exact: true })).toBeVisible();

    await answer.locator(":scope > summary").click();
    await expect(answer).not.toHaveAttribute("open", "");
    await userQuestion.locator(":scope > summary").click();
    await expect(userQuestion).not.toHaveAttribute("open", "");

    await page.setViewportSize({ width: 390, height: 844 });
    await atomRecallInfo.focus();
    await expect(atomRecallHelp).toBeVisible();
    const tooltipBounds = await atomRecallHelp.boundingBox();
    expect(tooltipBounds).not.toBeNull();
    expect(tooltipBounds!.x).toBeGreaterThanOrEqual(0);
    expect(tooltipBounds!.x + tooltipBounds!.width).toBeLessThanOrEqual(390);
  });

  test("marks tool latency unavailable for a historical total-only attempt", async ({ page }) => {
    const historyId = "legacy-total-only";
    await page.route(`**/api/evaluations/runs/${historyId}`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          run: {
            manifest: {
              run_id: "legacy-total-run",
              schema_version: "qa-v1",
              status: "scored",
            },
            metadata: {
              name: "Legacy total-only run",
              dataset_name: "Historical dataset",
              agent_spec: "qa-agent.md",
            },
            summary: {
              overall_attempt_pass_rate: 1,
              passed_attempts: 1,
              quality_accounted_attempts: 1,
              macro_mean_scored_attempt_required_atom_recall: 1,
              attempt_lifecycle_counts: {
                scored: 1,
                execution_failed: 0,
                evaluation_failed: 0,
              },
            },
            prepared_items: [{
              item_id: "legacy-question",
              question: "How long did this historical attempt take?",
              gold_atoms: [{ id: "A1", text: "It took 1.50 seconds.", required: true }],
            }],
            answers: [{
              item_id: "legacy-question",
              attempt_id: "legacy-question-attempt-1",
              ordinal: 1,
              status: "answer_ready",
              duration_ms: 1500,
              answer: "It took 1.50 seconds.",
            }],
            evaluation_results: [{
              item_id: "legacy-question",
              attempt_id: "legacy-question-attempt-1",
              ordinal: 1,
              status: "scored",
              passed: true,
              atom_score: 1,
              judgments: [{
                atom_id: "A1",
                outcome: "entailed",
                rationale: "The answer provides the expected duration.",
              }],
            }],
            report_available: false,
          },
        }),
      });
    });
    await page.route("**/api/evaluations/runs", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          runs: [{
            id: historyId,
            valid: true,
            name: "Legacy total-only run",
            status: "scored",
            attempts: 1,
            overall_attempt_pass_rate: 1,
          }],
        }),
      });
    });

    await page.goto("/evaluations");
    await page.locator(`[data-run-id="${historyId}"]`).click();

    const latencyItem = page.locator(".latency-item");
    await expect(latencyItem.locator(".latency-total-value")).toHaveText("1.50 s total");
    await expect(latencyItem.locator(".latency-tool-value")).toHaveText(
      "Tool timing unavailable",
    );
    await expect(latencyItem.locator(".latency-other-value")).toHaveText(
      "Remaining time unavailable",
    );
    await expect(latencyItem.locator(".latency-unknown-segment")).toHaveAttribute(
      "style",
      /height:\s*100(\.00)?%/,
    );
  });

  test("does not synthesize latency for a legacy run without attempt timings", async ({ page }) => {
    const historyId = "legacy-without-latency";
    await page.route(`**/api/evaluations/runs/${historyId}`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          run: {
            manifest: {
              run_id: "legacy-run",
              schema_version: "qa-v1",
              status: "scored",
            },
            metadata: {
              name: "Legacy run",
              dataset_name: "Historical dataset",
              agent_spec: "qa-agent.md",
            },
            summary: {
              overall_attempt_pass_rate: 1,
              passed_attempts: 1,
              quality_accounted_attempts: 1,
              macro_mean_scored_attempt_required_atom_recall: 1,
              attempt_lifecycle_counts: {
                scored: 1,
                execution_failed: 0,
                evaluation_failed: 0,
              },
            },
            prepared_items: [{
              item_id: "legacy-question",
              question: "How was this historical answer produced?",
              gold_atoms: [{ id: "A1", text: "It predates timing capture.", required: true }],
            }],
            answers: [{
              item_id: "legacy-question",
              attempt_id: "legacy-question-attempt-1",
              ordinal: 1,
              status: "answer_ready",
              answer: "This answer predates timing capture.",
            }],
            evaluation_results: [{
              item_id: "legacy-question",
              attempt_id: "legacy-question-attempt-1",
              ordinal: 1,
              status: "scored",
              passed: true,
              atom_score: 1,
              judgments: [{
                atom_id: "A1",
                outcome: "entailed",
                rationale: "The answer provides the expected fact.",
              }],
            }],
            report_available: false,
          },
        }),
      });
    });
    await page.route("**/api/evaluations/runs", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          runs: [{
            id: historyId,
            valid: true,
            name: "Legacy run",
            status: "scored",
            attempts: 1,
            overall_attempt_pass_rate: 1,
          }],
        }),
      });
    });

    await page.goto("/evaluations");
    await page.locator(`[data-run-id="${historyId}"]`).click();

    const latencyPanel = page.locator(".latency-panel");
    await expect(latencyPanel).toContainText("Latency unavailable for this run.");
    await expect(latencyPanel).toContainText(
      "This historical run has no authoritative per-attempt timings.",
    );
    await expect(latencyPanel.getByRole("listitem")).toHaveCount(0);
  });

  test("retries only failed atoms and opens a complete evaluation successor", async ({ page }) => {
    await page.goto("/evaluations");
    await page.getByRole("button", { name: /Datasets/ }).click();
    await page.getByLabel("Name").first().fill("Failure retry dataset");
    await page.getByLabel("Dataset file").setInputFiles({
      name: "failure-retry.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify([
        {
          id: "retry-scored",
          question: "Keep this successful atom?",
          answer: "Keep this generated atom.",
          time_sensitive: false,
        },
        {
          id: "retry-execution",
          question: "Recover this atom?",
          answer: "Recover this generated atom.",
          time_sensitive: false,
        },
        {
          id: "retry-evaluation",
          question: "Score this answer again?",
          answer: "Reuse this terminal answer.",
          time_sensitive: false,
        },
      ])),
    });
    await page.getByRole("button", { name: "Validate and import" }).click();
    await page.locator("#dataset-list [data-dataset-id]").filter({
      has: page.getByText("Failure retry dataset", { exact: true }),
    }).click();
    await page.getByRole("button", { name: "Generate Atoms" }).click();

    const dialog = page.getByRole("dialog", {
      name: "Review atoms · Failure retry dataset",
    });
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("Generation failed:", { exact: false })).toHaveCount(1);
    const retryAtoms = dialog.getByRole("button", {
      name: "Retry failed atoms (1)",
    });
    await expect(retryAtoms).toBeVisible();
    const preservedPanel = dialog.locator("details").filter({
      hasText: "Keep this successful atom?",
    });
    if ((await preservedPanel.getAttribute("open")) === null) {
      await preservedPanel.locator("summary").click();
    }
    const preservedAtom = preservedPanel.getByLabel(
      "Atom text 1 for retry-scored",
    );
    await preservedAtom.fill("Unsaved reviewer edit remains visible.");

    await retryAtoms.click();
    await preservedAtom.fill("Edit made while retry was running.");

    await expect(dialog.getByText("Generation failed:", { exact: false })).toHaveCount(0, {
      timeout: 15_000,
    });
    await expect(
      dialog.getByRole("button", { name: /Retry failed atoms/ }),
    ).toHaveCount(0);
    await expect(preservedAtom).toHaveValue("Edit made while retry was running.");
    await dialog.getByLabel("New dataset name").fill(
      "Failure retry dataset · reviewed",
    );
    await dialog.getByRole("button", { name: "Save as new dataset" }).click();

    await expect(page.locator("#dataset-detail").getByRole("heading", {
      name: "Failure retry dataset · reviewed",
    })).toBeVisible();
    await page.getByRole("button", { name: "Evaluate" }).click();
    await page.getByLabel("Evaluation name").fill("Failure-only evaluation");
    await page.getByRole("button", { name: "Start evaluation" }).click();

    await expect(page.getByRole("heading", {
      name: "Failure-only evaluation",
    })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", {
      name: "Retry failed attempts (2)",
    })).toBeVisible();
    await expect(page.locator(".attempt-result .status")).toHaveText([
      "scored",
      "execution_failed",
      "evaluation_failed",
    ]);
    const executionFailure = page.locator('.question-result[data-question-id="retry-execution"]');
    await expect(executionFailure.locator(":scope > summary")).toContainText(
      "No scored attempts",
    );
    await expect(executionFailure.locator(":scope > summary")).toContainText(
      "1 attempt · awaiting scores",
    );
    await executionFailure.locator(":scope > summary").click();
    await expect(executionFailure.locator(".attempt-score")).toHaveText("Not scored");

    await page.getByRole("button", {
      name: "Retry failed attempts (2)",
    }).click();

    await expect(page.getByRole("heading", {
      name: "Failure-only evaluation · retry 1",
    })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".lineage-callout")).toContainText(
      "retried from Failure-only evaluation",
    );
    await expect(page.locator(".attempt-result .status")).toHaveText([
      "scored",
      "scored",
      "scored",
    ]);
    await expect(page.getByRole("button", {
      name: /Retry failed attempts/,
    })).toHaveCount(0);
    await expect(page.locator(".evidence-card").getByText(
      "100.0%",
      { exact: true },
    ).first()).toBeVisible();

    await page.getByRole("button", { name: "← All runs" }).click();
    await expect(page.locator("#runs-body").getByText(
      "Failure-only evaluation",
      { exact: true },
    )).toBeVisible();
    await expect(page.locator("#runs-body").getByText(
      "Failure-only evaluation · retry 1",
      { exact: true },
    )).toBeVisible();
  });
});
