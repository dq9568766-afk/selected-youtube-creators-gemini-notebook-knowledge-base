// 1. Setup Context Menu
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "summarize-youtube",
    title: "Summarize with Gemini",
    contexts: ["link"],
    targetUrlPatterns: ["*://www.youtube.com/watch*"],
  });
});

// Helper: Save URL and Open Gemini
function triggerGemini(videoUrl) {
  // Save the URL to storage so the content script can read it
  chrome.storage.local.set({ pendingVideo: videoUrl }, () => {
    chrome.tabs.create({ url: "https://gemini.google.com/app" });
  });
}

// 2. Handle Right-Click
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "summarize-youtube" && info.linkUrl) {
    triggerGemini(info.linkUrl);
  }
});

// 3. Handle Keyboard Shortcut
chrome.commands.onCommand.addListener(async (command) => {
  if (command === "summarize-video") {
    const [tab] = await chrome.tabs.query({
      active: true,
      currentWindow: true,
    });
    if (tab && tab.url && tab.url.includes("youtube.com/watch")) {
      triggerGemini(tab.url);
    }
  }
});
