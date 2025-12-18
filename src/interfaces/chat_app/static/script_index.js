const fileContainer = document.getElementById("file-container-iframe");
const sourcesList = document.getElementById("sources-list");
const modal = document.getElementById("preview-modal");
const modalClose = document.getElementById("modal-close");
const modalTitle = document.getElementById("modal-title");
const modalUrl = document.getElementById("modal-url");

if (sourcesList) {
    sourcesList.addEventListener("click", (event) => {
        const actionBtn = event.target.closest(".source-action");
        if (actionBtn) {
            const sourceType = actionBtn.dataset.source || "source";
            const label = actionBtn.dataset.label || "Add item";
            handleSourceAction(sourceType, label);
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

function handleSourceAction(sourceType, label) {
    const lower = sourceType.toLowerCase();
    if (lower === "links") {
        const url = prompt("Enter a URL to ingest:");
        if (!url) {
            return;
        }
        const formData = new FormData();
        formData.append("url", url);
        fetch("/document_index/upload_url", {
            method: "POST",
            body: formData,
        })
            .then((res) => {
                if (res.ok) {
                    return res.json().catch(() => ({}));
                }
                return res.json().catch(() => ({})).then((data) => {
                    throw new Error(data.detail || data.error || "Failed to add link.");
                });
            })
            .then(() => window.location.reload())
            .catch((err) => alert(err.message || "Failed to add link."));
    } else if (lower === "jira") {
        const projectKey = prompt("Enter a JIRA project key (e.g., CMSPROD):");
        if (!projectKey) {
            return;
        }
        const formData = new FormData();
        formData.append("project_key", projectKey);
        fetch("/document_index/add_jira_project", {
            method: "POST",
            body: formData,
        })
            .then((res) => {
                if (res.ok) {
                    window.location.reload();
                } else {
                    return res.json().catch(() => ({})).then((data) => {
                        throw new Error(data.detail || "Failed to add JIRA project.");
                    });
                }
            })
            .catch((err) => alert(err.message || "Failed to add JIRA project."));
    } else if (lower === "git") {
        const repoUrl = prompt("Enter a Git repository URL to ingest (GitHub/GitLab with mkdocs):");
        if (!repoUrl) {
            return;
        }
        const formData = new FormData();
        formData.append("repo_url", repoUrl);
        fetch("/document_index/add_git_repo", {
            method: "POST",
            body: formData,
        })
            .then((res) => {
                if (res.ok) {
                    window.location.reload();
                } else {
                    return res.json().catch(() => ({})).then((data) => {
                        throw new Error(data.detail || "Failed to add git repo.");
                    });
                }
            })
            .catch((err) => alert(err.message || "Failed to add git repo."));
    } else if (lower === "local_files") {
        const fileInput = document.createElement("input");
        fileInput.type = "file";
        fileInput.onchange = () => {
            const file = fileInput.files?.[0];
            if (!file) {
                return;
            }
            const formData = new FormData();
            formData.append("file", file);
            fetch("/document_index/upload", {
                method: "POST",
                body: formData,
            })
                .then((res) => {
                    if (res.ok) {
                        return res.json().catch(() => ({}));
                    }
                    return res.json().catch(() => ({})).then((data) => {
                        throw new Error(data.detail || data.error || "Failed to upload file.");
                    });
                })
                .then(() => window.location.reload())
                .catch((err) => alert(err.message || "Failed to upload file."));
        };
        fileInput.click();
    } else {
        console.debug("Unhandled action", { sourceType, label });
    }
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
