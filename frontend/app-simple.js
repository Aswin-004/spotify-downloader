/**
 * Spotify Meta Downloader - Simplified Frontend
 * Direct, working implementation
 */

// State
let isDownloading = false;
let pollInterval = null;

// Attach button click handler
document.getElementById("downloadBtn").addEventListener("click", startDownload);

/**
 * Start download when button clicked
 */
function startDownload() {
    const url = document.querySelector("input").value;
    
    if (!url) {
        alert("Enter Spotify URL");
        return;
    }
    
    if (isDownloading) {
        alert("Download already in progress");
        return;
    }
    
    isDownloading = true;
    document.getElementById("downloadBtn").disabled = true;
    document.getElementById("progressContainer").classList.remove("hidden");
    
    // Send download request to backend
    fetch("/api/download", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ 
            url: url,
            spotify_url: url  // Support both field names
        })
    })
    .then(res => res.json())
    .then(data => {
        console.log("Download started:", data);
        // Start polling for status updates
        startPolling();
    })
    .catch(err => {
        console.error("Download error:", err);
        alert("Error starting download: " + err.message);
        isDownloading = false;
        document.getElementById("downloadBtn").disabled = false;
        document.getElementById("progressContainer").classList.add("hidden");
    });
}

/**
 * Start status polling
 */
function startPolling() {
    if (pollInterval) return;
    
    // Poll every 1 second for updates
    pollInterval = setInterval(fetchStatus, 1000);
}

/**
 * Stop status polling
 */
function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

/**
 * Fetch and display current download status
 */
function fetchStatus() {
    fetch("/api/status")
        .then(res => res.json())
        .then(data => {
            // Update progress bar
            const progressFill = document.getElementById("progressFill");
            const progressText = document.getElementById("progressText");
            
            progressFill.style.width = data.progress + "%";
            progressText.innerText = data.status.toUpperCase() + " - " + (data.current || "");
            
            // Stop polling when done
            if (data.status === "completed" || data.status === "failed") {
                stopPolling();
                isDownloading = false;
                document.getElementById("downloadBtn").disabled = false;
                
                if (data.status === "completed") {
                    setTimeout(() => {
                        alert("Download completed!");
                        document.getElementById("progressContainer").classList.add("hidden");
                    }, 500);
                } else {
                    setTimeout(() => {
                        alert("Download failed");
                        document.getElementById("progressContainer").classList.add("hidden");
                    }, 500);
                }
            }
        })
        .catch(err => {
            console.error("Status fetch error:", err);
        });
}

/**
 * Page initialization
 */
document.addEventListener("DOMContentLoaded", function() {
    console.log("App initialized");
    document.querySelector("input").focus();
});
