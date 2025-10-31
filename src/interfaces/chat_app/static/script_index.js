const toggleSidebarButton = document.getElementById("toggle-sidebar-btn");
const mainContent = document.querySelector(".main-content");
const fileArea = document.getElementById("file-area");
const fileContainer = document.getElementById("file-container-iframe");
const fileHeader = document.getElementById("file-header");
const fileTitle = document.getElementById("file-title");
const fileUrl = document.getElementById("file-url");
const linkList = document.querySelectorAll(".link-list");
const newUploadButton = document.getElementById("new-upload-btn");

toggleSidebarButton.addEventListener("click", () => {
    const sidebar = document.querySelector(".sidebar");
    const icon = toggleSidebarButton.querySelector(".material-symbols-rounded");
    
    // On mobile (screen width <= 800px), toggle 'hidden' class
    // On desktop, toggle 'collapsed' class
    if (window.innerWidth <= 800) {
        sidebar.classList.toggle("hidden");
        icon.textContent = sidebar.classList.contains("hidden") ? "menu" : "close";
    } else {
        sidebar.classList.toggle("collapsed");
        icon.textContent = sidebar.classList.contains("collapsed") ? "menu" : "chevron_left";
    }
});

linkList.forEach((element) => {
    element.addEventListener("click", (event) => {
        const liElement = event.target.closest('li');
        if (liElement) {
            const linkName = liElement.id;
            loadDocument(linkName);
        }
    })
});

async function loadDocument(documentPath) {
    const API_URL = "/document_index/load_document/"+documentPath;
    
    try {
        const response = await fetch(API_URL);
        
        if (response.error) {
            console.error("Error loading document:", response.error);
            return;
        }

        const result = await response.json()
        const assetBasePath = result.original_url;
        const baseTag = `<base href="${assetBasePath}">`;
        const source_type = result.source_type;
        const title = result.title;
        
        var documentHtml = result.document;

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


        var finalHtml = documentHtml.replace(/<head\s*[^>]*>/i, `$&${additionalAttributes}`);
        if (finalHtml === documentHtml) {
            finalHtml = `<head>${additionalAttributes}</head>${documentHtml}`;
        }

        
        fileContainer.onload = function() {

            const iframeDoc = fileContainer.contentDocument || fileContainer.contentWindow.document;
            iframeDoc.open();
            iframeDoc.write(finalHtml);
            iframeDoc.close();
            fileContainer.onload = null;

        }


        // clear current content
        mainContent.innerHTML = '';
        fileContainer.style.display = 'block'; 
        fileArea.style.display = 'flex';
        fileArea.innerHTML = ''
        fileTitle.textContent = title;
        fileUrl.textContent = assetBasePath;
        
        fileHeader.appendChild(fileTitle);
        fileHeader.appendChild(fileUrl);

        fileArea.appendChild(fileHeader);
        fileArea.appendChild(fileContainer)
        mainContent.appendChild(fileArea);
        
    } catch (error) {
        console.error("Failed to execute loadDocument:", error);
    }
};

newUploadButton.addEventListener("click", uploadFile);

function uploadFile(){
    window.location.href = '/document_index/';
    fileContainer.innerHTML = '';
    fileContainer.style.display = 'none';
}