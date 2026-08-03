(() => {
  "use strict";

  const state = { datasets: [], profiles: [], agents: [], runs: [], jobs: [], selectedDataset: null, draft: null, openRunId: null, pollingJobId: null, reviewValidationActive: false };
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
  const shortHash = (value) => value ? `${value.slice(0, 8)}…${value.slice(-5)}` : "built-in";
  const percent = (value) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
  const formatDuration = (value) => {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "—";
    if (value < 1000) return `${Math.round(value)} ms`;
    return `${(value / 1000).toFixed(value < 10000 ? 2 : 1)} s`;
  };
  const when = (value) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
  const scoreValue = (value) => typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
  const outcomePresentation = {
    entailed: { label: "Passed", className: "passed" },
    not_mentioned: { label: "Not mentioned", className: "not-mentioned" },
    contradicted: { label: "Contradicted", className: "contradicted" }
  };

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("json") ? await response.json() : await response.text();
    if (!response.ok) throw new Error(payload.error || payload || `Request failed (${response.status})`);
    return payload;
  }

  function toast(title, message) {
    $("#toast-title").textContent = title;
    $("#toast-message").textContent = message;
    $("#toast").classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $("#toast").classList.remove("show"), 3200);
  }

  function showView(name) {
    $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function activeJob() {
    return state.jobs.find((job) => ["queued", "running"].includes(job.status));
  }

  function renderRuntime() {
    const job = activeJob();
    const progress = job?.progress?.status;
    $("#runtime-label").textContent = job ? `${job.kind.replace("_", " ")} · ${progress || job.status}` : "Ready";
    $("#metric-job").textContent = job ? "Yes" : "No";
    $("#job-banner").hidden = !job;
    if (job) $("#job-banner").textContent = `${job.kind.replace("_", " ")} is ${progress || job.status}. This page can be closed; state is persisted.`;
  }

  function renderLaunchOptions() {
    $("#launch-dataset").innerHTML = state.datasets.length
      ? state.datasets.map((item) => `<option value="${esc(item.id)}">${esc(item.name)} · ${item.item_count} items${item.parent_dataset_id ? " · reviewed child" : ""}</option>`).join("")
      : `<option value="">Import a dataset first</option>`;
    $("#launch-profile").innerHTML = state.profiles.map((item) => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join("");
    $("#launch-agent").innerHTML = state.agents.length
      ? state.agents.map((item) => `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(item.id)}</option>`).join("")
      : `<option value="">No agent specs found</option>`;
  }

  function renderProfiles() {
    $("#profile-count").textContent = state.profiles.length;
    $("#profile-list").innerHTML = state.profiles.map((profile) => {
      const extractor = profile.components.atoms_extractor;
      const evaluator = profile.components.evaluator;
      return `<article class="profile-card ${profile.built_in ? "builtin" : ""}">
        <p class="eyebrow">${profile.built_in ? "Workspace default" : "Imported profile"}</p>
        <h2>${esc(profile.name)}</h2>
        <small class="muted">${esc(shortHash(profile.sha256))}</small>
        <div class="model-line"><span>Atom extractor</span><code>${esc(extractor.provider)} / ${esc(extractor.model)}</code></div>
        <div class="model-line"><span>Comparator</span><code>${esc(evaluator.provider)} / ${esc(evaluator.model)}</code></div>
      </article>`;
    }).join("");
  }

  function filteredDatasets() {
    const query = ($("#dataset-search").value || "").trim().toLowerCase();
    return state.datasets.filter((item) => !query || [item.name, item.source_filename, ...(item.categories || []), ...(item.answer_sources || [])].join(" ").toLowerCase().includes(query));
  }

  function renderDatasets() {
    const datasets = filteredDatasets();
    $("#dataset-count").textContent = state.datasets.length;
    $("#metric-datasets").textContent = state.datasets.length;
    $("#datasets-empty").hidden = datasets.length > 0;
    $("#dataset-list").innerHTML = datasets.map((item) => `<button class="catalog-item ${state.selectedDataset?.id === item.id ? "selected" : ""}" data-dataset-id="${esc(item.id)}">
      <span><strong>${esc(item.name)}</strong><small>${item.item_count} items · ${item.supplied_atom_item_count} with supplied atoms${item.parent_dataset_id ? " · reviewed child" : ""}</small></span>
      <code>${esc(shortHash(item.sha256))}</code>
    </button>`).join("");
    $$("[data-dataset-id]").forEach((button) => button.addEventListener("click", () => selectDataset(button.dataset.datasetId)));
  }

  function selectDataset(id) {
    state.selectedDataset = state.datasets.find((item) => item.id === id);
    renderDatasets();
    const item = state.selectedDataset;
    if (!item) return;
    const hasAtoms = item.atom_count > 0;
    const resumableJob = state.jobs.find((job) =>
      job.kind === "generate_atoms" &&
      job.status === "completed" &&
      job.context?.dataset_id === item.id &&
      job.result?.draft_id &&
      job.result?.draft_status === "open"
    );
    $("#dataset-detail").innerHTML = `
      <p class="eyebrow">${item.parent_dataset_id ? "Reviewed child dataset" : "Imported dataset"}</p>
      <h2>${esc(item.name)}</h2>
      <p class="muted">${esc(item.source_filename)} · ${esc(shortHash(item.sha256))}</p>
      <div class="definition-list">
        <div><span>Total items</span><strong>${item.item_count}</strong></div>
        <div><span>Eligible / time-sensitive</span><strong>${item.eligible_item_count} / ${item.time_sensitive_item_count}</strong></div>
        <div><span>Items with atoms</span><strong>${item.supplied_atom_item_count}</strong></div>
        <div><span>Imported</span><strong>${esc(when(item.created_at))}</strong></div>
      </div>
      <p class="eyebrow">Categories</p><div class="chips">${(item.categories || []).map((value) => `<span class="chip">${esc(value)}</span>`).join("") || `<span class="muted">None supplied</span>`}</div>
      <p class="eyebrow">Answer sources</p><div class="chips">${(item.answer_sources || []).map((value) => `<span class="chip">${esc(value)}</span>`).join("") || `<span class="muted">None supplied</span>`}</div>
      <div class="detail-actions">
        ${hasAtoms ? "" : `<label>Atom generation profile<select id="atom-profile">${state.profiles.map((profile) => `<option value="${esc(profile.id)}">${esc(profile.name)}</option>`).join("")}</select></label>`}
        <button class="button primary" id="${hasAtoms ? "review-atoms" : "generate-atoms"}">${hasAtoms ? "Review Atoms" : "Generate Atoms"}</button>
        ${resumableJob ? `<button class="button quiet" id="resume-atoms" data-draft-id="${esc(resumableJob.result.draft_id)}">Resume atom review</button>` : ""}
        <button class="button quiet" id="evaluate-dataset">Evaluate</button>
      </div>`;
    $("#generate-atoms")?.addEventListener("click", generateAtoms);
    $("#review-atoms")?.addEventListener("click", reviewAtoms);
    $("#resume-atoms")?.addEventListener("click", (event) => resumeAtomDraft(event.currentTarget.dataset.draftId));
    $("#evaluate-dataset").addEventListener("click", () => {
      showView("new");
      $("#launch-dataset").value = item.id;
    });
  }

  function renderRuns() {
    const valid = state.runs.filter((run) => run.valid);
    $("#metric-runs").textContent = valid.length;
    $("#metric-pass").textContent = valid.length ? percent(valid[0].overall_attempt_pass_rate) : "—";
    $("#runs-empty").hidden = state.runs.length > 0;
    $("#runs-body").innerHTML = state.runs.map((run) => `<tr data-run-id="${esc(run.id)}">
      <td><strong>${esc(run.name || run.run_id || "Unnamed run")}</strong><small>${esc(when(run.created_at))}</small></td>
      <td>${esc(run.dataset_name || "CLI snapshot")}</td>
      <td><code>${esc(run.agent_spec || "resolved artifact")}</code></td>
      <td>${run.attempts ?? "—"}</td>
      <td>${percent(run.overall_attempt_pass_rate)}</td>
      <td><span class="status ${run.valid ? (run.status === "scored" ? "good" : "run") : "bad"}">${esc(run.status)}</span></td>
    </tr>`).join("");
    $$("[data-run-id]").forEach((row) => row.addEventListener("click", () => openRun(row.dataset.runId)));
  }

  async function loadCatalog() {
    const selectedDatasetId = state.selectedDataset?.id;
    const payload = await api("/api/evaluations/catalog");
    Object.assign(state, payload);
    renderRuntime();
    renderProfiles();
    renderLaunchOptions();
    const nextDatasetId = state.datasets.some((item) => item.id === selectedDatasetId)
      ? selectedDatasetId
      : state.datasets[0]?.id;
    if (nextDatasetId) selectDataset(nextDatasetId); else {
      state.selectedDataset = null;
      renderDatasets();
    }
    const job = activeJob();
    if (job && state.pollingJobId !== job.id) monitorExistingJob(job.id);
  }

  async function loadRuns() {
    const payload = await api("/api/evaluations/runs");
    state.runs = payload.runs;
    renderRuns();
  }

  async function pollJob(jobId) {
    for (;;) {
      const { job } = await api(`/api/evaluations/jobs/${encodeURIComponent(jobId)}`);
      const existing = state.jobs.findIndex((item) => item.id === job.id);
      if (existing >= 0) state.jobs[existing] = job; else state.jobs.unshift(job);
      renderRuntime();
      if (["completed", "failed", "interrupted"].includes(job.status)) {
        if (job.status !== "completed") throw new Error(job.error || `Job ${job.status}`);
        return job.result || {};
      }
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
  }

  async function monitorExistingJob(jobId) {
    state.pollingJobId = jobId;
    try {
      const result = await pollJob(jobId);
      toast("Background job completed", result.draft_id ? "The atom draft is ready to resume." : "The evaluation is available in history.");
      await Promise.all([loadCatalog(), loadRuns()]);
    } catch (error) {
      toast("Background job stopped", error.message);
    } finally {
      state.pollingJobId = null;
    }
  }

  async function resumeAtomDraft(draftId) {
    try {
      const payload = await api(`/api/evaluations/atom-drafts/${encodeURIComponent(draftId)}`);
      if (payload.draft.status !== "open") throw new Error("This atom draft was already saved.");
      state.draft = payload.draft;
      openAtomEditor();
    } catch (error) { toast("Could not resume atom review", error.message); }
  }

  async function generateAtoms() {
    if (!state.selectedDataset) return;
    try {
      const payload = await api(`/api/evaluations/datasets/${encodeURIComponent(state.selectedDataset.id)}/generate-atoms`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: $("#atom-profile").value })
      });
      state.jobs.unshift(payload.job);
      renderRuntime();
      toast("Atom generation started", "The provider job is running in the background.");
      const result = await pollJob(payload.job.id);
      const draftPayload = await api(`/api/evaluations/atom-drafts/${encodeURIComponent(result.draft_id)}`);
      state.draft = draftPayload.draft;
      openAtomEditor();
    } catch (error) { toast("Could not generate atoms", error.message); }
  }

  async function reviewAtoms() {
    if (!state.selectedDataset) return;
    try {
      const payload = await api(`/api/evaluations/datasets/${encodeURIComponent(state.selectedDataset.id)}/review-atoms`, {
        method: "POST"
      });
      state.draft = payload.draft;
      openAtomEditor();
    } catch (error) { toast("Could not review atoms", error.message); }
  }

  function openAtomEditor() {
    const eligible = state.draft.items.filter((item) => !item.time_sensitive);
    const failed = eligible.filter((item) => item.status === "preparation_failed");
    const isNewDraft = $("#atom-editor").dataset.draftId !== state.draft.id;
    $("#atom-dialog-title").textContent = `Review atoms · ${state.draft.dataset_name}`;
    $("#retry-failed-atoms").hidden = failed.length === 0;
    $("#retry-failed-atoms").disabled = false;
    $("#retry-failed-atoms").textContent = `Retry failed atoms (${failed.length})`;
    if (isNewDraft) {
      state.reviewValidationActive = false;
      $("#reviewed-dataset-name").value = `${state.draft.dataset_name} · reviewed`;
      eligible.forEach((item, itemIndex) => { item.review_open = itemIndex === 0; });
    }
    $("#atom-editor").dataset.draftId = state.draft.id;
    $("#atom-editor").innerHTML = eligible.map((item, itemIndex) => {
      const atoms = item.atoms || [];
      const requiredCount = atoms.filter((atom) => atom.required).length;
      return `<details class="atom-item" data-item-index="${itemIndex}" ${item.review_open ? "open" : ""}>
      <summary>
        <span class="atom-summary-copy"><code>${esc(item.item_id)} · ${esc(item.status.replaceAll("_", " "))}</code><strong>${esc(item.question)}</strong></span>
        <span class="atom-summary-count" data-atom-summary-count>${atoms.length} atom${atoms.length === 1 ? "" : "s"} · ${requiredCount} required</span>
      </summary>
      <div class="atom-item-body">
        <section class="expected-answer" aria-labelledby="expected-answer-${itemIndex}">
          <p class="field-label" id="expected-answer-${itemIndex}">Expected answer</p>
          <div>${esc(item.answer)}</div>
          ${item.answer_source ? `<small class="expected-answer-source">Source: ${esc(item.answer_source)}</small>` : ""}
        </section>
        ${item.error ? `<div class="generation-error"><strong>Generation failed:</strong> ${esc(item.error)} — add atoms manually.</div>` : ""}
        <section class="atoms" aria-labelledby="atoms-title-${itemIndex}">
          <div class="atoms-heading"><div><p class="field-label" id="atoms-title-${itemIndex}">Atoms</p><span>Edit each obligation and choose whether it is required.</span></div></div>
          ${atoms.map((atom, atomIndex) => atomRow(itemIndex, atomIndex, atom, item.item_id)).join("")}
        ${atoms.length ? "" : `<div class="atoms-empty">No atoms yet for this answer.</div>`}
        <button class="add-atom" type="button" data-add-atom="${itemIndex}" aria-label="Add atom to ${esc(item.item_id)}">+ Add atom</button>
        </section>
      </div>
    </details>`;
    }).join("");
    bindAtomEditor();
    if (state.reviewValidationActive) validateReviewedItems(false);
    if (!$("#atom-dialog").open) $("#atom-dialog").showModal();
  }

  async function retryFailedAtoms() {
    if (!state.draft) return;
    syncAtomsFromDom();
    const retryIds = new Set(
      state.draft.items
        .filter((item) => item.status === "preparation_failed")
        .map((item) => item.item_id)
    );
    if (!retryIds.size) return;
    const localById = new Map(state.draft.items.map((item) => [
      item.item_id,
      {
        atoms: (item.atoms || []).map((atom) => ({ ...atom })),
        review_open: item.review_open
      }
    ]));
    const button = $("#retry-failed-atoms");
    button.disabled = true;
    button.textContent = `Retrying ${retryIds.size} failed atom${retryIds.size === 1 ? "" : "s"}…`;
    try {
      const payload = await api(`/api/evaluations/atom-drafts/${encodeURIComponent(state.draft.id)}/retry-failed`, {
        method: "POST"
      });
      state.jobs.unshift(payload.job);
      renderRuntime();
      const result = await pollJob(payload.job.id);
      syncAtomsFromDom();
      state.draft.items.forEach((item) => {
        localById.set(item.item_id, {
          atoms: (item.atoms || []).map((atom) => ({ ...atom })),
          review_open: item.review_open
        });
      });
      const draftPayload = await api(`/api/evaluations/atom-drafts/${encodeURIComponent(result.draft_id)}`);
      draftPayload.draft.items = draftPayload.draft.items.map((item) => {
        const local = localById.get(item.item_id);
        if (!local) return item;
        if (retryIds.has(item.item_id)) return { ...item, review_open: local.review_open };
        return { ...item, atoms: local.atoms, review_open: local.review_open };
      });
      state.draft = draftPayload.draft;
      openAtomEditor();
      const remaining = state.draft.items.filter((item) => item.status === "preparation_failed").length;
      toast(
        remaining ? "Atom retry completed with failures" : "Failed atoms recovered",
        remaining ? `${remaining} item${remaining === 1 ? "" : "s"} still need attention.` : "Generated candidates are ready for review."
      );
    } catch (error) {
      button.disabled = false;
      button.textContent = `Retry failed atoms (${retryIds.size})`;
      toast("Could not retry failed atoms", error.message);
    }
  }

  function atomRow(itemIndex, atomIndex, atom, itemId) {
    const ordinal = atomIndex + 1;
    return `<div class="atom-row" data-atom-row="${itemIndex}:${atomIndex}">
      <label class="atom-field atom-id"><span>Atom ID</span><input name="atom-${itemIndex}-${atomIndex}-id" data-field="id" value="${esc(atom.id)}" aria-label="Atom ID ${ordinal} for ${esc(itemId)}"></label>
      <label class="atom-field atom-text"><span>Atom text</span><textarea name="atom-${itemIndex}-${atomIndex}-text" data-field="text" rows="3" aria-label="Atom text ${ordinal} for ${esc(itemId)}">${esc(atom.text)}</textarea></label>
      <label class="required-toggle"><input name="atom-${itemIndex}-${atomIndex}-required" type="checkbox" data-field="required" ${atom.required ? "checked" : ""} aria-label="Required for atom ${ordinal} in ${esc(itemId)}"><span>Required</span></label>
      <button type="button" data-remove-atom="${itemIndex}:${atomIndex}" aria-label="Remove atom ${ordinal} from ${esc(itemId)}">Remove</button>
    </div>`;
  }

  function eligibleDraftItems() { return state.draft.items.filter((item) => !item.time_sensitive); }

  function syncAtomsFromDom() {
    eligibleDraftItems().forEach((item, itemIndex) => {
      const panel = $(`[data-item-index="${itemIndex}"]`);
      if (panel) item.review_open = panel.open;
      item.atoms = $$(`[data-atom-row^="${itemIndex}:"]`).map((row) => ({
        id: row.querySelector('[data-field="id"]').value,
        text: row.querySelector('[data-field="text"]').value,
        required: row.querySelector('[data-field="required"]').checked
      }));
    });
  }

  function clearReviewError(panel) {
    panel.querySelector(".review-validation-error")?.remove();
    panel.querySelectorAll("[aria-invalid]").forEach((field) => {
      field.removeAttribute("aria-invalid");
      field.removeAttribute("aria-describedby");
    });
  }

  function validateReviewedItems(focusInvalid = true) {
    let firstInvalid = null;
    eligibleDraftItems().forEach((item, itemIndex) => {
      const panel = $(`[data-item-index="${itemIndex}"]`);
      clearReviewError(panel);
      const rows = [...panel.querySelectorAll("[data-atom-row]")];
      const errors = [];
      const errorId = `atom-review-error-${itemIndex}`;
      const invalidFields = [];
      const markInvalid = (field) => {
        field.setAttribute("aria-invalid", "true");
        field.setAttribute("aria-describedby", errorId);
        invalidFields.push(field);
      };

      if (!rows.length) errors.push("Add at least one atom.");
      const ids = new Map();
      rows.forEach((row) => {
        const idField = row.querySelector('[data-field="id"]');
        const textField = row.querySelector('[data-field="text"]');
        const id = idField.value.trim();
        if (!id) {
          if (!errors.includes("Every atom needs an ID.")) errors.push("Every atom needs an ID.");
          markInvalid(idField);
        } else if (ids.has(id)) {
          if (!errors.includes("Atom IDs must be unique within this question.")) errors.push("Atom IDs must be unique within this question.");
          markInvalid(ids.get(id));
          markInvalid(idField);
        } else {
          ids.set(id, idField);
        }
        if (!textField.value.trim()) {
          if (!errors.includes("Every atom needs text.")) errors.push("Every atom needs text.");
          markInvalid(textField);
        }
      });
      if (rows.length && !rows.some((row) => row.querySelector('[data-field="required"]').checked)) {
        errors.push("At least one atom must be required.");
        rows.forEach((row) => markInvalid(row.querySelector('[data-field="required"]')));
      }
      if (!errors.length) return;

      item.review_open = true;
      panel.open = true;
      const error = document.createElement("div");
      error.id = errorId;
      error.className = "review-validation-error";
      error.setAttribute("role", "alert");
      error.textContent = errors.join(" ");
      panel.querySelector(".atom-item-body").prepend(error);
      firstInvalid ||= invalidFields[0] || panel.querySelector("[data-add-atom]");
    });
    state.reviewValidationActive = firstInvalid !== null;
    if (firstInvalid && focusInvalid) {
      firstInvalid.closest("[data-item-index]").scrollIntoView({ block: "nearest" });
      firstInvalid.focus();
    }
    return firstInvalid === null;
  }

  function validateReviewedDatasetName(focusInvalid = true) {
    const field = $("#reviewed-dataset-name");
    const errorId = "reviewed-dataset-name-error";
    $(`#${errorId}`)?.remove();
    field.removeAttribute("aria-invalid");
    field.removeAttribute("aria-describedby");
    if (field.value.trim()) return true;
    const error = document.createElement("span");
    error.id = errorId;
    error.className = "field-validation-error";
    error.setAttribute("role", "alert");
    error.textContent = "Enter a name for the reviewed dataset.";
    field.setAttribute("aria-invalid", "true");
    field.setAttribute("aria-describedby", errorId);
    field.after(error);
    if (focusInvalid) field.focus();
    return false;
  }

  function bindAtomEditor() {
    $$("[data-item-index]").forEach((panel) => panel.addEventListener("toggle", () => {
      eligibleDraftItems()[Number(panel.dataset.itemIndex)].review_open = panel.open;
    }));
    $$('[data-field="required"]').forEach((checkbox) => checkbox.addEventListener("change", () => {
      const panel = checkbox.closest("[data-item-index]");
      const shouldRevalidate = Boolean(panel.querySelector(".review-validation-error"));
      const atoms = panel.querySelectorAll("[data-atom-row]").length;
      const required = panel.querySelectorAll('[data-field="required"]:checked').length;
      panel.querySelector("[data-atom-summary-count]").textContent = `${atoms} atom${atoms === 1 ? "" : "s"} · ${required} required`;
      if (shouldRevalidate) validateReviewedItems(false);
    }));
    $$('[data-field="id"], [data-field="text"]').forEach((field) => field.addEventListener("input", () => {
      const panel = field.closest("[data-item-index]");
      if (panel.querySelector(".review-validation-error")) validateReviewedItems(false);
    }));
    $$("[data-add-atom]").forEach((button) => button.addEventListener("click", () => {
      syncAtomsFromDom();
      const item = eligibleDraftItems()[Number(button.dataset.addAtom)];
      const ids = new Set((item.atoms || []).map((atom) => atom.id));
      let ordinal = 1; while (ids.has(`A${ordinal}`)) ordinal += 1;
      item.atoms = [...(item.atoms || []), { id: `A${ordinal}`, text: "", required: true }];
      item.review_open = true;
      openAtomEditor();
      $(`[data-atom-row="${button.dataset.addAtom}:${item.atoms.length - 1}"] [data-field="text"]`).focus();
    }));
    $$("[data-remove-atom]").forEach((button) => button.addEventListener("click", () => {
      syncAtomsFromDom();
      const [itemIndex, atomIndex] = button.dataset.removeAtom.split(":").map(Number);
      const item = eligibleDraftItems()[itemIndex];
      item.atoms.splice(atomIndex, 1);
      item.review_open = true;
      openAtomEditor();
      const nextAtomIndex = Math.min(atomIndex, item.atoms.length - 1);
      const focusTarget = nextAtomIndex >= 0
        ? $(`[data-atom-row="${itemIndex}:${nextAtomIndex}"] [data-field="text"]`)
        : $(`[data-add-atom="${itemIndex}"]`);
      focusTarget.focus();
    }));
  }

  async function saveReviewedDataset() {
    try {
      syncAtomsFromDom();
      const nameValid = validateReviewedDatasetName(false);
      const atomsValid = validateReviewedItems();
      if (!nameValid || !atomsValid) {
        if (atomsValid) $("#reviewed-dataset-name").focus();
        return;
      }
      $("#reviewed-dataset-name").value = $("#reviewed-dataset-name").value.trim();
      const reviewed_items = eligibleDraftItems().map((item) => ({ item_id: item.item_id, atoms: item.atoms || [] }));
      const payload = await api(`/api/evaluations/atom-drafts/${encodeURIComponent(state.draft.id)}/save`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: $("#reviewed-dataset-name").value, reviewed_items })
      });
      $("#atom-dialog").close();
      toast("Reviewed dataset saved", `${payload.dataset.name} is ready to evaluate.`);
      await loadCatalog();
      selectDataset(payload.dataset.id);
    } catch (error) { toast("Could not save reviewed dataset", error.message); }
  }

  async function submitImport(form, endpoint, noun) {
    try {
      const payload = await api(endpoint, { method: "POST", body: new FormData(form) });
      toast(`${noun} ${payload.created ? "imported" : "already exists"}`, payload[noun.toLowerCase()].name);
      form.reset();
      await loadCatalog();
    } catch (error) { toast(`Could not import ${noun.toLowerCase()}`, error.message); }
  }

  async function launchEvaluation(event) {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.currentTarget));
    body.attempts = Number(body.attempts);
    body.run_workers = Number(body.run_workers);
    body.score_workers = Number(body.score_workers);
    try {
      const payload = await api("/api/evaluations/runs", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
      });
      state.jobs.unshift(payload.job); renderRuntime();
      toast("Evaluation queued", "The run continues even if you leave this page.");
      const result = await pollJob(payload.job.id);
      await loadRuns();
      await openRun(result.history_id);
    } catch (error) { toast("Could not run evaluation", error.message); }
  }

  function groupQuestionResults(preparedItems, evaluationResults) {
    const groups = [];
    const byId = new Map();
    (preparedItems || []).forEach((prepared) => {
      const group = { itemId: prepared.item_id, prepared, attempts: [] };
      groups.push(group);
      byId.set(prepared.item_id, group);
    });
    (evaluationResults || []).forEach((result) => {
      let group = byId.get(result.item_id);
      if (!group) {
        group = { itemId: result.item_id, prepared: {}, attempts: [] };
        groups.push(group);
        byId.set(result.item_id, group);
      }
      group.attempts.push(result);
    });
    groups.forEach((group) => group.attempts.sort((left, right) => (left.ordinal || 0) - (right.ordinal || 0)));
    return groups;
  }

  function questionScoreSummary(attempts) {
    const scored = attempts
      .map((attempt) => ({ attempt, score: scoreValue(attempt.atom_score) }))
      .filter((entry) => entry.score !== null);
    if (!scored.length) return { average: null, best: null, worst: null };
    return {
      average: scored.reduce((total, entry) => total + entry.score, 0) / scored.length,
      best: scored.reduce((best, entry) => entry.score > best.score ? entry : best),
      worst: scored.reduce((worst, entry) => entry.score < worst.score ? entry : worst)
    };
  }

  function macroMeanScoredAttemptAtomRecall(evaluationResults) {
    const recalls = (evaluationResults || [])
      .filter((result) => result.status === "scored" && Array.isArray(result.judgments) && result.judgments.length)
      .map((result) => result.judgments.filter((judgment) => judgment.outcome === "entailed").length / result.judgments.length);
    if (!recalls.length) return null;
    return recalls.reduce((total, recall) => total + recall, 0) / recalls.length;
  }

  function questionLatencyAttempts(preparedItems, answers) {
    const groups = new Map((preparedItems || []).map((item) => [
      item.item_id,
      { itemId: item.item_id, question: item.question || "Question text unavailable", attempts: [] }
    ]));
    (answers || []).forEach((answer) => {
      if (typeof answer.duration_ms !== "number" || !Number.isFinite(answer.duration_ms) || answer.duration_ms < 0) return;
      if (!groups.has(answer.item_id)) {
        groups.set(answer.item_id, {
          itemId: answer.item_id,
          question: "Question text unavailable",
          attempts: []
        });
      }
      const toolCalls = Array.isArray(answer.tool_calls)
        ? answer.tool_calls.filter((call) => typeof call.duration_ms === "number" && Number.isFinite(call.duration_ms) && call.duration_ms >= 0)
        : null;
      groups.get(answer.item_id).attempts.push({
        attemptId: answer.attempt_id,
        ordinal: answer.ordinal,
        total: answer.duration_ms,
        toolTotal: toolCalls === null ? null : toolCalls.reduce((total, call) => total + call.duration_ms, 0),
        toolCount: toolCalls === null ? null : toolCalls.length
      });
    });
    return [...groups.values()]
      .filter((group) => group.attempts.length)
      .map((group) => {
        group.attempts.sort((left, right) => (left.ordinal || 0) - (right.ordinal || 0));
        return group;
      });
  }

  function renderLatencyChart(preparedItems, answers) {
    const rows = questionLatencyAttempts(preparedItems, answers);
    const maximumDuration = rows.length
      ? Math.max(...rows.flatMap((row) => row.attempts.map((attempt) => attempt.total)))
      : 0;
    return `<section class="panel latency-panel" aria-labelledby="latency-chart-title" data-maximum-duration="${maximumDuration}">
      <div class="latency-head">
        <div><p class="eyebrow">Execution timing</p><h2 id="latency-chart-title">Latency per question</h2></div>
        <div class="latency-legend" aria-label="Latency legend"><span><i class="tool"></i>Tool calls</span><span><i class="other"></i>Other agent time</span></div>
      </div>
      ${rows.length ? `<div class="latency-histogram" role="list">
        ${rows.map((row) => {
          const first = row.attempts[0];
          return `<article class="latency-item" role="listitem" data-question-id="${esc(row.itemId)}">
            <div class="latency-question"><code>${esc(row.itemId)}</code><span title="${esc(row.question)}">${esc(row.question)}</span></div>
            <label class="latency-attempt"><span>Attempt</span><select aria-label="Attempt for ${esc(row.itemId)}">
              ${row.attempts.map((attempt) => `<option value="${esc(attempt.attemptId)}" data-total="${attempt.total}" data-tool="${attempt.toolTotal === null ? "" : attempt.toolTotal}" data-tool-count="${attempt.toolCount === null ? "" : attempt.toolCount}">Attempt ${esc(attempt.ordinal ?? "—")}</option>`).join("")}
            </select></label>
            <div class="latency-plot">
              <div class="latency-bar" role="img" aria-label="Latency for ${esc(row.itemId)}, attempt ${esc(first.ordinal ?? "—")}">
                <span class="latency-other-segment"></span>
                <span class="latency-tool-segment"></span>
                <span class="latency-unknown-segment"></span>
              </div>
            </div>
            <div class="latency-values">
              <strong class="latency-total-value"></strong>
              <span class="latency-tool-value"></span>
              <span class="latency-other-value"></span>
              <small class="latency-tool-count"></small>
            </div>
          </article>`;
        }).join("")}
      </div>` : `<div class="latency-unavailable"><strong>Latency unavailable for this run.</strong><span>This historical run has no authoritative per-attempt timings.</span></div>`}
    </section>`;
  }

  function bindLatencyChart() {
    const panel = $(".latency-panel");
    if (!panel) return;
    const maximumDuration = Number(panel.dataset.maximumDuration);
    $$(".latency-item").forEach((item) => {
      const select = item.querySelector("select");
      const bar = item.querySelector(".latency-bar");
      const toolSegment = item.querySelector(".latency-tool-segment");
      const otherSegment = item.querySelector(".latency-other-segment");
      const unknownSegment = item.querySelector(".latency-unknown-segment");
      const update = () => {
        const option = select.selectedOptions[0];
        const total = Number(option.dataset.total);
        const toolAvailable = option.dataset.tool !== "";
        const toolTotal = toolAvailable ? Number(option.dataset.tool) : null;
        const representedTool = toolAvailable ? Math.min(toolTotal, total) : 0;
        const other = toolAvailable ? Math.max(0, total - representedTool) : null;
        const barHeight = maximumDuration > 0 ? (total / maximumDuration) * 100 : 0;
        const toolHeight = total > 0 ? (representedTool / total) * 100 : 0;
        const otherHeight = toolAvailable ? 100 - toolHeight : 0;

        bar.style.height = `${barHeight.toFixed(2)}%`;
        toolSegment.style.height = `${toolHeight.toFixed(2)}%`;
        otherSegment.style.height = `${otherHeight.toFixed(2)}%`;
        unknownSegment.style.height = toolAvailable ? "0.00%" : "100.00%";
        item.querySelector(".latency-total-value").textContent = `${formatDuration(total)} total`;
        item.querySelector(".latency-tool-value").textContent = toolAvailable
          ? `${formatDuration(toolTotal)} tools`
          : "Tool timing unavailable";
        item.querySelector(".latency-other-value").textContent = toolAvailable
          ? `${formatDuration(other)} other agent time`
          : "Remaining time unavailable";
        item.querySelector(".latency-tool-count").textContent = toolAvailable
          ? `${option.dataset.toolCount} tool call${option.dataset.toolCount === "1" ? "" : "s"}`
          : "";
        bar.setAttribute(
          "aria-label",
          `${item.dataset.questionId}, ${option.textContent}: ${formatDuration(total)} total; ${toolAvailable ? `${formatDuration(toolTotal)} in tools and ${formatDuration(other)} in other agent time` : "tool timing unavailable"}`
        );
      };
      select.addEventListener("change", update);
      requestAnimationFrame(update);
    });
  }

  function readableError(error) {
    if (!error) return "";
    if (typeof error === "string") return error;
    if (typeof error.message === "string") return error.message;
    return JSON.stringify(error);
  }

  function renderAtomJudgment(atom, judgment, atomIndex) {
    const outcome = outcomePresentation[judgment?.outcome] || {
      label: judgment?.outcome ? judgment.outcome.replaceAll("_", " ") : "Not judged",
      className: "unscored"
    };
    return `<details class="atom-judgment outcome-${esc(outcome.className)}" open>
      <summary>
        <span class="atom-judgment-title"><code>${esc(atom.id || `Atom ${atomIndex + 1}`)}</code>${atom.required ? `<span class="required-chip">Required</span>` : ""}</span>
        <span class="atom-outcome ${esc(outcome.className)}" aria-label="Evaluator outcome: ${esc(judgment?.outcome || "not judged")}">${esc(outcome.label)}</span>
      </summary>
      <div class="atom-judgment-body">
        <section>
          <p class="field-label">Expected content</p>
          <div class="evidence-copy">${esc(atom.text || "Expected atom content is unavailable.")}</div>
        </section>
        <section class="judgment-copy">
          <p class="field-label">Evaluator judgment</p>
          <p>${esc(judgment?.rationale || "No evaluator judgment is available for this atom.")}</p>
        </section>
      </div>
    </details>`;
  }

  function renderAttempt(result, prepared, answer) {
    const score = scoreValue(result.atom_score);
    const statusClass = result.status === "scored" ? (result.passed ? "good" : "bad") : "bad";
    const atoms = prepared.gold_atoms || prepared.expected_atoms || [];
    const judgmentsByAtom = new Map((result.judgments || []).map((judgment) => [judgment.atom_id, judgment]));
    const displayedAtoms = atoms.length
      ? atoms
      : (result.judgments || []).map((judgment) => ({ id: judgment.atom_id, text: "", required: false }));
    const modelAnswer = answer.answer || result.answer;
    return `<details class="attempt-result" data-attempt-id="${esc(result.attempt_id || "")}">
      <summary>
        <span class="attempt-title">
          <strong>Attempt ${esc(result.ordinal ?? "—")}</strong>
          <span class="status ${statusClass}">${esc(result.status || "unknown")}</span>
        </span>
        <span class="attempt-score ${score === null ? "unscored" : (result.passed ? "passed" : "failed")}">${score === null ? "Not scored" : percent(score)}</span>
      </summary>
      <div class="attempt-body">
        ${modelAnswer ? `<details class="readable-disclosure model-answer" open>
          <summary><span>Model answer</span><small>Full response</small></summary>
          <div class="evidence-copy">${esc(modelAnswer)}</div>
        </details>` : ""}
        ${result.error ? `<section class="attempt-error"><p class="field-label">Attempt error</p><p>${esc(readableError(result.error))}</p></section>` : ""}
        ${displayedAtoms.length ? `<section class="atom-evidence" aria-label="Atom judgments">
          <div class="atom-evidence-head"><div><p class="eyebrow">Atom evidence</p><h3>Expected content and evaluator judgment</h3></div><span>${displayedAtoms.length} atom${displayedAtoms.length === 1 ? "" : "s"}</span></div>
          <div class="atom-judgment-list">${displayedAtoms.map((atom, index) => renderAtomJudgment(atom, judgmentsByAtom.get(atom.id), index)).join("")}</div>
        </section>` : `<div class="empty compact"><strong>No atom judgments are available for this attempt.</strong></div>`}
      </div>
    </details>`;
  }

  function renderQuestionGroup(group, answersByAttempt) {
    const question = group.prepared.question || "Question text unavailable";
    const score = questionScoreSummary(group.attempts);
    const scoreLabel = score.average === null ? "No scored attempts" : percent(score.average);
    const bestWorst = score.average === null
      ? `${group.attempts.length} attempt${group.attempts.length === 1 ? "" : "s"} · awaiting scores`
      : `Best A${score.best.attempt.ordinal} ${percent(score.best.score)} · Worst A${score.worst.attempt.ordinal} ${percent(score.worst.score)}`;
    return `<details class="question-result" data-question-id="${esc(group.itemId)}">
      <summary>
        <span class="question-summary-copy"><code>${esc(group.itemId)}</code><strong>${esc(question)}</strong></span>
        <span class="question-summary-score">
          <span><small>Average score</small><strong>${scoreLabel}</strong></span>
          <meter min="0" max="1" value="${score.average ?? 0}" aria-label="Average atom score for ${esc(group.itemId)}" aria-valuetext="${esc(scoreLabel)}">${scoreLabel}</meter>
          <small>${esc(bestWorst)}</small>
        </span>
      </summary>
      <div class="question-result-body">
        <details class="readable-disclosure user-question" open>
          <summary><span>User question</span><small>Full prompt</small></summary>
          <div class="evidence-copy">${esc(question)}</div>
        </details>
        <div class="attempt-list">
          ${group.attempts.map((result) => renderAttempt(result, group.prepared, answersByAttempt[result.attempt_id] || {})).join("") || `<div class="empty compact"><strong>No attempts are available for this question.</strong></div>`}
        </div>
      </div>
    </details>`;
  }

  async function openRun(id) {
    try {
      const { run } = await api(`/api/evaluations/runs/${encodeURIComponent(id)}`);
      const meta = run.metadata || {}, manifest = run.manifest || {}, summary = run.summary || {};
      state.openRunId = id;
      $("#run-title").textContent = meta.name || manifest.run_id || "Evaluation run";
      $("#run-subtitle").textContent = `${meta.dataset_name || "CLI dataset snapshot"} · ${meta.agent_spec || manifest.agent?.agent_class || "resolved agent"}`;
      $("#report-link").href = `/api/evaluations/runs/${encodeURIComponent(id)}/report`;
      $("#report-link").hidden = !run.report_available;
      const counts = summary.attempt_lifecycle_counts || {};
      const retryableCount = (counts.execution_failed || 0) + (counts.evaluation_failed || 0);
      const retryButton = $("#retry-failed-evaluation");
      retryButton.hidden = retryableCount === 0;
      retryButton.disabled = false;
      retryButton.textContent = `Retry failed attempts (${retryableCount})`;
      const parent = state.runs.find((item) => item.id === meta.retry_of_history_id);
      const answersByAttempt = Object.fromEntries((run.answers || []).map((item) => [item.attempt_id, item]));
      const questionGroups = groupQuestionResults(run.prepared_items || [], run.evaluation_results || []);
      const atomRecall = macroMeanScoredAttemptAtomRecall(run.evaluation_results);
      $("#run-detail-content").innerHTML = `
        ${meta.retry_of_history_id ? `<div class="lineage-callout"><strong>Successor run ${esc(meta.retry_number || "")}</strong> · retried from ${esc(parent?.name || meta.retry_of_history_id)}. Scored attempts were carried forward unchanged.</div>` : ""}
        ${renderLatencyChart(run.prepared_items || [], run.answers || [])}
        <div class="evidence-grid">
          <article class="evidence-card"><span>Overall pass rate</span><strong>${percent(summary.overall_attempt_pass_rate)}</strong><small>${summary.passed_attempts ?? 0} / ${summary.quality_accounted_attempts ?? 0} accounted</small></article>
          <article class="evidence-card">
            <div class="metric-label">
              <span>Atoms recall</span>
              <button class="metric-info" type="button" aria-label="About atoms recall" aria-describedby="atoms-recall-help">i</button>
              <span class="metric-tooltip" id="atoms-recall-help" role="tooltip">
                For each scored attempt: all atoms marked as entailed ÷ all atoms, including required and optional atoms. This card shows the unweighted average across scored attempts; technical failures are excluded. 100% means every expected fact was covered, and 90% or more is strong overall. Unlike required atoms recall, missing an optional atom lowers this metric but does not by itself fail the attempt. Not-mentioned and contradicted atoms both count as not recalled; contradictions also reduce the separate atom score.
              </span>
            </div>
            <strong>${percent(atomRecall)}</strong>
            <small>macro mean, scored only</small>
          </article>
          <article class="evidence-card">
            <div class="metric-label">
              <span>Required atoms recall</span>
              <button class="metric-info" type="button" aria-label="About required atoms recall" aria-describedby="required-atoms-recall-help">i</button>
              <span class="metric-tooltip" id="required-atoms-recall-help" role="tooltip">
                For each scored attempt: required atoms marked as entailed ÷ all required atoms. This card shows the unweighted average across scored attempts; optional atoms and technical failures are excluded. Aim for 100% because every required atom is a pass condition. 90% or more is strong overall, while anything below 100% means at least one required fact was missed—inspect the attempt evidence to see whether misses are isolated or recurring.
              </span>
            </div>
            <strong>${percent(summary.macro_mean_scored_attempt_required_atom_recall)}</strong>
            <small>macro mean, scored only</small>
          </article>
          <article class="evidence-card"><span>Artifact schema</span><strong>${esc(manifest.schema_version || "—")}</strong><small>${esc(manifest.status || "unknown")}</small></article>
        </div>
        <section class="panel"><div class="panel-head"><div><p class="eyebrow">Attempt evidence</p><h2>Answers and judgments</h2></div></div>
          <div class="result-list">${questionGroups.map((group) => renderQuestionGroup(group, answersByAttempt)).join("") || `<div class="empty"><strong>No question results are available.</strong></div>`}</div>
        </section>`;
      bindLatencyChart();
      showView("run-detail");
    } catch (error) { toast("Could not open run", error.message); }
  }

  async function retryFailedEvaluation() {
    if (!state.openRunId) return;
    const button = $("#retry-failed-evaluation");
    const idleLabel = button.textContent;
    button.disabled = true;
    button.textContent = "Retrying failed attempts…";
    try {
      const payload = await api(`/api/evaluations/runs/${encodeURIComponent(state.openRunId)}/retry-failed`, {
        method: "POST"
      });
      state.jobs.unshift(payload.job);
      renderRuntime();
      toast("Evaluation retry queued", "Only technically failed attempts will invoke providers.");
      const result = await pollJob(payload.job.id);
      await loadRuns();
      await openRun(result.history_id);
    } catch (error) {
      button.disabled = false;
      button.textContent = idleLabel;
      toast("Could not retry evaluation", error.message);
    }
  }

  $$(".nav-item").forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
  $$("[data-go]").forEach((item) => item.addEventListener("click", () => showView(item.dataset.go)));
  $("#dataset-search").addEventListener("input", renderDatasets);
  $("#refresh-runs").addEventListener("click", () => loadRuns().catch((error) => toast("Refresh failed", error.message)));
  $("#dataset-import-form").addEventListener("submit", (event) => { event.preventDefault(); submitImport(event.currentTarget, "/api/evaluations/datasets", "Dataset"); });
  $("#profile-import-form").addEventListener("submit", (event) => { event.preventDefault(); submitImport(event.currentTarget, "/api/evaluations/profiles", "Profile"); });
  $("#evaluation-form").addEventListener("submit", launchEvaluation);
  $("#retry-failed-atoms").addEventListener("click", retryFailedAtoms);
  $("#retry-failed-evaluation").addEventListener("click", retryFailedEvaluation);
  $("#save-reviewed-dataset").addEventListener("click", saveReviewedDataset);
  $("#reviewed-dataset-name").addEventListener("input", () => {
    if ($("#reviewed-dataset-name-error")) validateReviewedDatasetName(false);
  });
  $$("[data-close-atom-dialog]").forEach((button) => button.addEventListener("click", () => $("#atom-dialog").close("cancel")));

  Promise.all([loadCatalog(), loadRuns()]).catch((error) => toast("Console could not load", error.message));
})();
