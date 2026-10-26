// Check if there is a pending video to summarize
chrome.storage.local.get("pendingVideo", (data) => {
  if (data.pendingVideo) {
    // Clear storage so it doesn't happen again on refresh
    chrome.storage.local.remove("pendingVideo");

    // Start the automation
    automateGeminiPrompt(data.pendingVideo);
  }
});

function automateGeminiPrompt(videoUrl) {
  const promptText = `Please summarize this YouTube video: ${videoUrl}`;

  // We need a loop to wait for the Gemini input box to load
  const intervalId = setInterval(() => {
    // 1. Find the input area (Gemini uses a contenteditable div)
    const inputBox = document.querySelector('div[contenteditable="true"]');

    // 2. Find the Send button
    const sendButton = document.querySelector(
      'button[aria-label="Send message"]',
    );

    if (inputBox) {
      clearInterval(intervalId); // Stop looking

      // FOCUS the input box
      inputBox.focus();

      // INSERT the text.
      // Because it's a rich text editor, we execute a command to insert text cleanly.
      document.execCommand("insertText", false, promptText);

      // WAIT a split second for the button to become active, then CLICK
      setTimeout(() => {
        if (sendButton) {
          sendButton.click();
        } else {
          // Fallback: simpler click attempt if selector fails
          const iconButtons = document.querySelectorAll("button");
          // usually the send button is the last one or has a specific icon
          // This part relies on the aria-label working, which is standard.
        }
      }, 500);
    }
  }, 500); // Check every 500ms
}
