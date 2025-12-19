const fileContainer = document.getElementById("file-container-iframe");
const sourcesList = document.getElementById("sources-list");
const modal = document.getElementById("preview-modal");
const modalClose = document.getElementById("modal-close");
const modalTitle = document.getElementById("modal-title");
const modalUrl = document.getElementById("modal-url");
const DEFAULT_MENU_STATUS = "Ready!";

const ACTION_CONFIG = {
    links_add: {
        key: "links_add",
        title: "Add link",
        submitLabel: "Add link",
        help: "Ingest a single URL into the catalog.",
        endpoint: "/document_index/upload_url",
        method: "POST",
        errorMessage: "Failed to add link.",
        successMessage: "Link submitted. Refreshing list...",
        fields: [
            {
                name: "url",
                label: "URL",
                type: "url",
                placeholder: "https://example.com",
                required: true,
            },
        ],
    },
    jira_add: {
        key: "jira_add",
        title: "Add JIRA project",
        submitLabel: "Add project",
        help: "Provide the JIRA project key to ingest tickets.",
        endpoint: "/document_index/add_jira_project",
        method: "POST",
        errorMessage: "Failed to add JIRA project.",
        successMessage: "Project submitted. Refreshing list...",
        fields: [
            {
                name: "project_key",
                label: "Project key",
                type: "text",
                placeholder: "CMSPROD",
                required: true,
            },
        ],
    },
    git_add: {
        key: "git_add",
        title: "Add Git repository",
        submitLabel: "Add repo",
        help: "Paste a Git repository URL to ingest.",
        endpoint: "/document_index/add_git_repo",
        method: "POST",
        errorMessage: "Failed to add git repo.",
        successMessage: "Repo submitted. Refreshing list...",
        fields: [
            {
                name: "repo_url",
                label: "Repository URL",
                type: "url",
                placeholder: "https://github.com/org/repo",
                required: true,
            },
        ],
    },
    git_remove: {
        key: "git_remove",
        title: "Remove Git repository",
        submitLabel: "Remove repo",
        help: "Enter the repo URL or name to remove all ingested files from that repo.",
        endpoint: "/document_index/remove_git_repo",
        method: "POST",
        errorMessage: "Failed to remove git repo.",
        successMessage: "Repo removed. Refreshing list...",
        fields: [
            {
                name: "repo",
                label: "Repository URL or name",
                type: "text",
                placeholder: "https://github.com/org/repo or repo name",
                required: true,
            },
        ],
    },
    local_files_upload: {
        key: "local_files_upload",
        title: "Upload file",
        submitLabel: "Upload file",
        help: "Upload a local file to ingest.",
        endpoint: "/document_index/upload",
        method: "POST",
        errorMessage: "Failed to upload file.",
        successMessage: "Upload submitted. Refreshing list...",
        fields: [
            {
                name: "file",
                label: "Choose file",
                type: "file",
                required: true,
            },
        ],
    },
    schedule_update: {
        key: "schedule_update",
        title: "Edit schedule",
        submitLabel: "Save schedule",
        help: "Set a cron schedule (e.g. */15 * * * *). Leave blank to disable.",
        endpoint: "/document_index/update_schedule",
        method: "POST",
        errorMessage: "Failed to update schedule.",
        successMessage: "Schedule updated. Refreshing list...",
        fields: [
            {
                name: "schedule",
                label: "Cron schedule",
                type: "text",
                placeholder: "0 * * * *",
            },
        ],
    },
};

if (sourcesList) {
    initializeActionMenus();
    sourcesList.addEventListener("click", (event) => {
        const actionBtn = event.target.closest(".source-action");
        if (actionBtn) {
            if (actionBtn.classList.contains("danger")) {
                return;
            }
            const actionKey = actionBtn.dataset.action || (actionBtn.dataset.source || "").toLowerCase();
            openActionMenu(actionBtn, actionKey);
            return;
        }

        if (event.target.closest(".delete-doc") || event.target.closest(".delete-source")) {
            return; // allow default navigation
        }

        const docItem = event.target.closest(".source-doc");
        if (docItem) {
            const hash = docItem.dataset.hash;
            if (hash) {
                loadDocument(hash);
            }
        }
    });
}

async function loadDocument(documentPath) {
    const API_URL = "/document_index/load_document/"+documentPath;
    
    try {
        console.debug("Fetching document", { API_URL });
        const response = await fetch(API_URL);
        
        // Check for 401 Unauthorized response
        if (response.status === 401) {
            console.log("Unauthorized - redirecting to landing page");
            window.location.href = '/';
            return;
        }
        
        if (!response.ok) {
            const text = await response.text();
            throw new Error(`Failed to load document (status ${response.status}): ${text}`);
        }

        const result = await response.json().catch(() => ({}));
        if (!result || typeof result !== "object") {
            throw new Error("Invalid response while loading document.");
        }

        const assetBasePath = result.original_url;
        const baseTag = `<base href="${assetBasePath}">`;
        const source_type = result.source_type;
        const title = result.title;
        var documentHtml = result.document || "";

        const customCSS = `
                    <style>
                        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&display=swap');
                        body {
                            /* Set the base font and color for the entire document body */
                            font-family:  "Poppins", sans-serif;
                            color: #343541; /* Specific text color */
                        }
                        /* Ensure all elements (headings, paragraphs) use the same font */
                        body * {
                            font-family: inherit !important;
                        }
                    </style>
                `;

        var additionalAttributes;
        if (source_type === "git"){
            documentHtml = marked.parse(documentHtml);
            additionalAttributes = customCSS
        } else if (source_type ==="jira"){
            additionalAttributes = customCSS
        } else {
            additionalAttributes = baseTag
        }


        const isPdfInline = documentHtml.startsWith("__PDF_INLINE__::");
        let finalHtml;
        if (isPdfInline) {
            const pdfUrl = documentHtml.replace("__PDF_INLINE__::", "");
            finalHtml = `
                <html>
                  <head>${additionalAttributes || ""}</head>
                  <body style="margin:0;padding:0;display:flex;flex-direction:column;height:100vh;">
                    <div style="padding:8px;background:#f5f5f5;border-bottom:1px solid #ddd;font-family:sans-serif;">
                      <a href="${pdfUrl}" target="_blank" rel="noopener">Open PDF in new tab</a>
                    </div>
                    <embed src="${pdfUrl}" type="application/pdf" style="flex:1;width:100%;border:0;" />
                  </body>
                </html>
            `;
        } else {
            finalHtml = buildPreviewHtml(documentHtml, additionalAttributes || "", source_type);
        }
        console.debug("Preview payload", {
            source_type,
            title,
            base: assetBasePath,
            hasContent: Boolean(documentHtml),
            length: documentHtml.length,
            hasHead: /<head[^>]*>/i.test(documentHtml || ""),
        });

        if (fileContainer) {
            fileContainer.srcdoc = finalHtml;
            const iframeDoc = fileContainer.contentDocument || fileContainer.contentWindow?.document;
            if (iframeDoc) {
                try {
                    iframeDoc.open();
                    iframeDoc.write(finalHtml);
                    iframeDoc.close();
                    console.debug("Iframe write success");
                } catch (err) {
                    console.error("Iframe write failed", err);
                }
            }
        }


        openModal(title, assetBasePath, finalHtml.length);
        
    } catch (error) {
        console.error("Failed to execute loadDocument:", error);
    }
};

function initializeActionMenus() {
    const menus = document.querySelectorAll(".action-menu");
    menus.forEach((menu) => {
        const closeBtn = menu.querySelector(".action-menu-close");
        const cancelBtn = menu.querySelector(".action-menu-cancel");
        const form = menu.querySelector(".action-menu-form");

        if (closeBtn) {
            closeBtn.addEventListener("click", () => hideActionMenu(menu));
        }
        if (cancelBtn) {
            cancelBtn.addEventListener("click", () => hideActionMenu(menu));
        }
        if (form) {
            form.addEventListener("submit", (event) => {
                event.preventDefault();
                submitActionMenu(menu);
            });
        }
    });
}

function openActionMenu(actionBtn, actionKey) {
    const key = (actionKey || "").toLowerCase();
    const config = ACTION_CONFIG[key];
    if (!config) {
        console.debug("Unhandled action menu", { actionKey });
        return;
    }

    const card = actionBtn.closest(".source-card");
    const menu = card?.querySelector(".action-menu");
    if (!menu) {
        return;
    }

    if (!menu.classList.contains("hidden") && menu.dataset.action === config.key) {
        hideActionMenu(menu);
        return;
    }

    closeOtherActionMenus(menu);
    buildActionMenu(menu, config, actionBtn.dataset.label);
    menu.classList.remove("hidden");
    menu.dataset.source = actionBtn.dataset.source || "";

    if (config.key === "schedule_update") {
        const scheduleInput = menu.querySelector('[name="schedule"]');
        if (scheduleInput) {
            scheduleInput.value = actionBtn.dataset.schedule || "";
        }
    }

    const firstInput = menu.querySelector("input, textarea, select");
    if (firstInput) {
        firstInput.focus();
    }
}

function closeOtherActionMenus(activeMenu) {
    const menus = document.querySelectorAll(".action-menu");
    menus.forEach((menu) => {
        if (menu !== activeMenu) {
            hideActionMenu(menu);
        }
    });
}

function hideActionMenu(menu) {
    menu.classList.add("hidden");
    menu.classList.remove("is-loading");
    menu.dataset.action = "";
    menu.dataset.source = "";
    setMenuStatus(menu, DEFAULT_MENU_STATUS);
}

function buildActionMenu(menu, config, labelOverride) {
    menu.dataset.action = config.key;
    const titleEl = menu.querySelector(".action-menu-title");
    const fieldsEl = menu.querySelector(".action-menu-fields");
    const helpEl = menu.querySelector(".action-menu-help");
    const submitBtn = menu.querySelector(".action-menu-submit");

    if (titleEl) {
        titleEl.textContent = labelOverride || config.title || "Action";
    }
    if (submitBtn) {
        submitBtn.textContent = config.submitLabel || "Run";
    }
    if (helpEl) {
        helpEl.textContent = config.help || "";
    }
    setMenuStatus(menu, DEFAULT_MENU_STATUS);

    if (fieldsEl) {
        fieldsEl.innerHTML = "";
        config.fields.forEach((field) => {
            const wrapper = document.createElement("label");
            wrapper.className = "action-menu-field";

            const label = document.createElement("span");
            label.textContent = field.label || field.name;
            wrapper.appendChild(label);

            let input;
            if (field.type === "textarea") {
                input = document.createElement("textarea");
            } else {
                input = document.createElement("input");
                input.type = field.type || "text";
            }
            input.name = field.name;
            input.className = "action-menu-input";
            input.required = Boolean(field.required);
            if (field.placeholder) {
                input.placeholder = field.placeholder;
            }
            if (field.type === "file" && field.accept) {
                input.accept = field.accept;
            }
            wrapper.appendChild(input);
            fieldsEl.appendChild(wrapper);
        });
    }
}

function setMenuStatus(menu, message, isError = false) {
    const statusEl = menu.querySelector(".action-menu-status");
    if (!statusEl) {
        return;
    }
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-error", Boolean(isError));
}

function setMenuLoading(menu, isLoading) {
    menu.classList.toggle("is-loading", Boolean(isLoading));
}

async function submitActionMenu(menu) {
    const actionKey = menu.dataset.action;
    const config = ACTION_CONFIG[actionKey];
    if (!config) {
        return;
    }

    const form = menu.querySelector(".action-menu-form");
    if (!form) {
        return;
    }

    const formData = new FormData(form);
    if (config.key === "schedule_update") {
        const source = (menu.dataset.source || "").trim();
        if (!source) {
            setMenuStatus(menu, "Missing source. Refresh and try again.", true);
            return;
        }
        formData.set("source", source);
    }
    const missingField = config.fields.find((field) => {
        if (!field.required) {
            return false;
        }
        const value = formData.get(field.name);
        if (!value) {
            return true;
        }
        if (typeof File !== "undefined" && value instanceof File) {
            return !value.name;
        }
        return false;
    });
    if (missingField) {
        setMenuStatus(menu, `${missingField.label || "Field"} is required.`, true);
        return;
    }

    setMenuLoading(menu, true);
    setMenuStatus(menu, "Running...");

    try {
        const response = await fetch(config.endpoint, {
            method: config.method || "POST",
            body: formData,
        });

        if (response.status === 401) {
            window.location.href = "/login";
            return;
        }

        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || payload.error || config.errorMessage || "Request failed.");
        }

        setMenuStatus(menu, config.successMessage || "Submitted.");
        setTimeout(() => {
            hideActionMenu(menu);
            window.location.reload();
        }, 700);
    } catch (err) {
        setMenuStatus(menu, err.message || config.errorMessage || "Request failed.", true);
    } finally {
        setMenuLoading(menu, false);
    }
}

function buildPreviewHtml(content, headExtras, sourceType) {
    const safeContent = content || "";
    const type = (sourceType || "").toLowerCase();

    const looksLikeHtml =
        /<\/?(html|body|p|div|span|h[1-6]|!doctype)/i.test(safeContent) ||
        safeContent.trim().startsWith("<") ||
        type === "links";

    if (looksLikeHtml) {
        let html = safeContent;
        if (!/<head[^>]*>/i.test(html)) {
            html = `<head>${headExtras}</head>${html}`;
        } else {
            html = html.replace(/<head\s*[^>]*>/i, `$&${headExtras}`);
        }

        if (!/<body[^>]*>/i.test(html)) {
            html = html.replace(/<head[^>]*>.*?<\/head>/is, (match) => match + `<body>${safeContent}</body>`);
        }
        if (!/<html[^>]*>/i.test(html)) {
            html = `<html>${html}</html>`;
        }
        return html;
    }

    const escaped = safeContent
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    return `<html><head>${headExtras}</head><body><pre style="white-space: pre-wrap; font-family: monospace; color: #111; background: #fff; padding: 12px;">${escaped}</pre></body></html>`;
}

function openModal(title, url, contentLength) {
    if (!modal) return;
    modalTitle.textContent = title || "Document preview";
    modalUrl.textContent = url || "";
    console.debug("Opening preview modal", { title, url, contentLength });
    modal.classList.remove("hidden");
    if (fileContainer) {
        fileContainer.focus();
    }
}

if (modalClose) {
    modalClose.addEventListener("click", () => {
        modal.classList.add("hidden");
        if (fileContainer) {
            const iframeDoc = fileContainer.contentDocument || fileContainer.contentWindow.document;
            iframeDoc.open();
            iframeDoc.write("");
            iframeDoc.close();
        }
    });
}

window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal && !modal.classList.contains("hidden")) {
        modal.classList.add("hidden");
    }
});
