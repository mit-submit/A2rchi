import { expect, test } from "@playwright/test";

test.describe("QA evaluation console", () => {
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
});
