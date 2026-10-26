# 本仓库的新工具 | The new tool in this repository

**中文：**本项目从下方原作者的浏览器扩展出发，扩展为指定 YouTube 博主的批量总结与 Gemini 网页笔记本归档工具。使用 yt-dlp 采集频道 `/videos`、`/shorts`、`/streams` 的公开清单，按频道和视频 ID 去重；首次连续分批总结历史视频，此后每周一、周四 08:30（Windows 本地时间）自动检查增量，周一核对全量清单。每轮所有频道合计最多 20 次新提交，新视频优先，频道轮转。每个频道一个笔记本，每条视频一个聊天；总结后自动命名为 `YYYYMMDD-主题词-完整原视频标题`，刷新并重新打开核对，便于在 Gemini 沉淀可查找的个人知识库。命令行管理采集、队列和断点续跑；实际网页操作使用已安装的 Codex Browser Automation / Computer Use。受 Gemini 真实额度与故障暂停限制，不使用 Gemini API，不下载视频，也不在本地保存摘要。

**English:** This repository extends the original browser-extension workflow below into a tool for batch summaries and Gemini web Notebook archiving for selected YouTube creators. yt-dlp inventories public `/videos`, `/shorts`, and `/streams` metadata and deduplicates by channel and video ID. The first run processes history in continuing batches; scheduled checks run on Mondays and Thursdays at 08:30 Windows local time, with a full inventory check on Mondays. A scheduled round permits at most 20 new submissions across all channels, prioritizes new videos, and rotates between channels. Each channel has one Notebook and each video one chat. After summarizing, the worker names the chat `YYYYMMDD-topic-full original video title`, refreshes the Notebook, and reopens the chat to verify it, building a searchable personal knowledge base in Gemini. A command-line queue supports collection and recovery, while the installed Codex Browser Automation / Computer Use worker handles the web UI. Progress depends on real Gemini limits and safe pauses. No Gemini API, video downloads, or local summary storage are used.

The new tool and its setup are documented in the [bilingual project README](../../README.md). The original upstream extension and its instructions are preserved below; the production queue does not install or run that extension. See [attribution and MIT license](../../NOTICE.md).

---

# Original upstream README / 原作者说明

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
