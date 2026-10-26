# YouTube Gemini Auto-Summarizer 🚀

A Chrome Extension that streamlines the workflow between **YouTube** and **Google Gemini**. It allows you to summarize videos instantly via right-click or keyboard shortcut, automatically handling the copy-pasting and submission process for you.

## Features

* **🖱️ Context Menu Integration:** Right-click on any YouTube video link (in your feed or suggestions) to summarize it without opening it.
* **⌨️ Keyboard Shortcut:** Press `Ctrl+Shift+Y` (Windows/Linux) or `Cmd+Shift+Y` (Mac) while watching a video to trigger the summary.
* **🤖 Full Automation:** The extension opens Gemini, focuses the input field, types the prompt, and clicks "Send" automatically. No manual pasting required.

## Prerequisites

1.  **Google Account:** You must be logged into [Google Gemini](https://gemini.google.com/).
2.  **Gemini YouTube Extension:** For the best results, ensure the **YouTube Extension** is enabled in your Gemini settings so the AI can access video captions/transcripts.
    * Go to: `gemini.google.com/extensions`
    * Toggle **YouTube** to **ON**.

## Installation

Since this extension is not yet on the Chrome Web Store, you need to install it in **Developer Mode**.

1.  **Clone or Download this Repository**
    ```bash
    git clone [https://github.com/YOUR_USERNAME/youtube-gemini-summarizer.git](https://github.com/YOUR_USERNAME/youtube-gemini-summarizer.git)
    ```
    *(Or download the ZIP and extract it to a folder)*

2.  **Open Chrome Extensions**
    * Navigate to `chrome://extensions/` in your browser.

3.  **Enable Developer Mode**
    * Toggle the switch in the top-right corner to **ON**.

4.  **Load the Extension**
    * Click the **Load unpacked** button in the top-left.
    * Select the folder where you saved the extension files (the folder containing `manifest.json`).

## How to Use

#### Method 1: The Feed (Right-Click)
1.  Go to the YouTube homepage or search results.
2.  **Right-click** on any video thumbnail or title.
3.  Select **"Summarize with Gemini"**.
4.  A new tab will open, and the summary will generate automatically.

#### Method 2: The Player (Shortcut)
1.  Open any YouTube video.
2.  Press the shortcut:
    * **Windows/Linux:** `Ctrl` + `Shift` + `Y`
    * **Mac:** `Cmd` + `Shift` + `Y`
3.  Gemini will open and start summarizing.

## Customization

**Change the Keyboard Shortcut:**
1.  Go to `chrome://extensions/shortcuts`.
2.  Find **Auto YouTube Summarizer**.
3.  Click the pencil icon and press your desired key combination.

## Technical Details

This extension uses `content.js` to perform **DOM Manipulation** on the Gemini web interface.
* It detects the content-editable input div.
* It uses `document.execCommand('insertText')` to simulate natural typing.
* It programmatically clicks the "Send" button.

> **Note:** If Google updates the CSS classes or structure of the Gemini website, the automation script (`content.js`) may need to be updated to target the new elements.

## License

This project is open source and available under the [MIT License](LICENSE).