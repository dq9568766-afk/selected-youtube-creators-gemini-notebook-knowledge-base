// GENERATED from page.js by build_page_inject.py - do not edit directly.
// Classic script injected into the isolated world. All page actions are
// exposed through one structured wrapper so failures never become null.
// Executes only in the extension's isolated world. All inputs are task metadata.
// No fetch, model calls, application internals, response export or credential access.
globalThis.__dqImpl = async function (action, args) {
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => !!el && el.getClientRects().length > 0;
  // Gemini renders parts of conversation content inside open shadow roots.
  // querySelectorAll on document does not cross those boundaries, even though
  // the content is visible and exposed in the accessibility tree.
  const all = (selector, root = document, includeHidden = false) => {
    const found = new Set(), pending = [root], visited = new Set();
    while (pending.length) {
      const current = pending.pop();
      if (!current || visited.has(current)) continue;
      visited.add(current);
      if (current.shadowRoot) pending.push(current.shadowRoot);
      for (const el of current.querySelectorAll?.(selector) || []) found.add(el);
      for (const el of current.querySelectorAll?.('*') || []) if (el.shadowRoot) pending.push(el.shadowRoot);
    }
    return includeHidden ? [...found] : [...found].filter(visible);
  };
  const deepText = el => {
    if (!el) return '';
    const parts = [el.textContent || ''];
    const pending = el.shadowRoot ? [el.shadowRoot] : [];
    const visited = new Set();
    while (pending.length) {
      const root = pending.pop();
      if (!root || visited.has(root)) continue;
      visited.add(root);
      parts.push(root.textContent || '');
      for (const node of root.querySelectorAll?.('*') || []) {
        if (node.shadowRoot) pending.push(node.shadowRoot);
      }
    }
    return parts.join(' ').replace(/\s+/g,' ').trim();
  };
  const one = (items, reason = 'ui_changed') => {
    if (items.length !== 1) throw new Error(reason);
    return items[0];
  };
  const label = el => norm(el.getAttribute('aria-label') || deepText(el));
  const button = (names, root = document) => one(all('button,[role="button"],[role="menuitem"]',root).filter(el => names.includes(label(el))));
  const wait = async fn => {
    for (let i = 0; i < 40; i++) {
      const result = fn();
      if (result) return result;
      await new Promise(resolve => setTimeout(resolve,250));
    }
    throw new Error('ui_changed');
  };
  const account = () => {
    const labels = all('[aria-label]').map(el => el.getAttribute('aria-label')).filter(t => /Google (?:账号|帐号|帳戶|账户|Account)/i.test(t));
    const visibleEmailSources = [document.body?.innerText || '', deepText(document.body), ...all('[data-email],[href*="accounts.google.com"]')
      .map(el => `${el.getAttribute('data-email') || ''} ${el.getAttribute('href') || ''} ${deepText(el)}`)];
    const emails = [...new Set([...labels, ...visibleEmailSources]
      .flatMap(t => t.match(/[^\s()（）<>]+@[^\s()（）<>]+\.[^\s()（）<>]+/g) || []))];
    // Gemini may expose more than one account address in its account menu or
    // navigation links. The safety condition is that the registered account
    // is visibly present, not that unrelated addresses are absent.
    if (!emails.includes(args.account)) {
      throw new Error(`account_mismatch:expected=${args.account}:found=${emails.join(',') || 'none'}`);
    }
    return args.account;
  };
  const notebookPath = value => new URL(value).pathname.replace(/^\/u\/\d+/, '').replace(/\/+$/, '') || '/';
  // Gemini has changed the chat DOM more than once. Keep the adapter limited
  // to stable semantic markers and retain the old selectors for older chats.
  const union = selectors => [...new Set(selectors.flatMap(selector => all(selector)))];
  const queryNodes = () => [
    ...union(['.query-text', 'user-query', '[data-message-author-role="user"]']),
    ...all('h1,h2,h3').filter(el => /(?:你说|You said|You asked)/i.test(deepText(el))),
  ];
  const replyNodes = () => union([
    'message-content', 'model-response', '[data-message-author-role="model"]',
    '[class*="model-response"]',
  ]);
  const sameNotebook = expected => location.origin === new URL(expected).origin && notebookPath(location.href) === notebookPath(expected);
  // Only unambiguous anti-bot gates should abort. Generic dialogs/alerts on
  // the Notebook page used to trip the old "配额|使用上限" check and looked
  // like a null injection result instead of a real verification page.
  const pageText = (document.body?.innerText || '').slice(0, 30000);
  const blocked = location.pathname.startsWith('/sorry')
    || /异常流量|unusual traffic|captcha|verify you are human/i.test(pageText);
  if (blocked) throw new Error('rate_limited');
  if (action === 'collect-notebooklm-source') {
    if (location.hostname !== 'notebook.google.com') throw new Error('notebooklm_mismatch');
    const body = document.body?.innerText || '';
    const links = all('a[href]', document, true).map(a => ({
      href: a.href,
      text: norm(deepText(a) || a.getAttribute('aria-label') || '')
    }));
    const youtubePattern=/https?:\/\/(?:www\.)?youtube\.com\/watch\?v=[^A-Za-z0-9_-]*[A-Za-z0-9_-]{11}/i;
    const youtube = body.match(youtubePattern)?.[0] ||
      links.map(x => x.href.match(youtubePattern)?.[0]).find(Boolean) || '';
    const chat = links.find(x => /gemini\.google\.com\/app\/[A-Za-z0-9_-]+/i.test(x.href))?.href || '';
    const title = document.querySelector('#source-title')?.textContent?.trim() ||
      document.querySelector('h1,h2')?.textContent?.trim() || '';
    const videoId = youtube.match(/[?&]v=[^A-Za-z0-9_-]*([A-Za-z0-9_-]{11})/i)?.[1] || '';
    return {title, youtube, video_id:videoId, chat, text:body.slice(0,12000)};
  }
  if (action === 'list-notebooklm-sources') {
    if (location.hostname !== 'notebook.google.com') throw new Error('notebooklm_mismatch');
    // The complete Gemini source list is collapsed by default. Expand the
    // group before inspecting its individual source checkboxes.
    let group=null;
    for (let i=0;i<30 && !group;i++) {
      group=all('button,[role="button"]', document, true).find(button =>
        /Gemini 的对话|Gemini conversations/i.test(label(button)));
      if (!group) await new Promise(resolve => setTimeout(resolve, 500));
    }
    if (!group) throw new Error('notebooklm_source_group_not_found');
    const expanded=group.getAttribute('aria-expanded') === 'true' || /收起|collapse/i.test(group.getAttribute('aria-label')||'');
    if (!expanded) {
      group.click();
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
    // Wait for the virtualized source rows to be inserted after expansion.
    for (let i=0;i<20;i++) {
      if (all('input[type="checkbox"][aria-label^="选择“"]', document, true).length) break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    const sources = [];
    // NotebookLM exposes a stable accessible label for every source item.
    // This survives layout changes better than walking the source card's
    // parent DOM hierarchy.
    for (const checkbox of all('input[type="checkbox"][aria-label]', document, true)) {
      const label = norm(checkbox.getAttribute('aria-label') || '');
      const match = label.match(/^选择[“\"](.+?)[”\"]$/);
      if (match && match[1] && !/^所有来源|所有 Gemini 对话/.test(match[1])) {
        const title=match[1].trim();
        if (!sources.some(item => item.title===title && item.occurrence===sources.filter(x=>x.title===title).length)) {
          sources.push({title, occurrence:sources.filter(x=>x.title===title).length});
        }
      }
    }
    if (sources.length) return {sources};
    // Fallback for a locale/build that does not expose checkbox labels.
    for (const more of all('button[id^="source-item-more-button"]', document, true)) {
      let box = more.parentElement, button = null;
      for (let depth=0; box && depth<5 && !button; depth++, box=box.parentElement) {
        button = all('button:not([id^="source-item-more-button"])', box, true)
          .find(candidate => norm(candidate.textContent||''));
      }
      const title = norm(button?.textContent || '');
      if (title) sources.push({title, occurrence:sources.filter(x=>x.title===title).length});
    }
    if (!sources.length) throw new Error('notebooklm_sources_empty');
    return {sources};
  }
  if (action === 'open-notebooklm-source') {
    if (location.hostname !== 'notebook.google.com') throw new Error('notebooklm_mismatch');
    const wanted = norm(args.title);
    const matchesTitle = el => {
      const values = [
        el?.getAttribute?.('aria-label') || '',
        el?.getAttribute?.('title') || '',
        el?.getAttribute?.('data-title') || '',
        el?.textContent || '',
        deepText(el),
      ].map(norm).filter(Boolean);
      return values.some(text => text === wanted || text.includes(wanted) || wanted.includes(text));
    };
    const find = () => {
      const labeled = all('input[type="checkbox"][aria-label]', document, true)
        .filter(input => {
          const label=norm(input.getAttribute('aria-label')||'');
          return label === `选择“${wanted}”` || label === `选择\"${wanted}\"`;
        });
      const occurrence=Number.isInteger(args.occurrence) ? args.occurrence : 0;
      const checkbox=labeled[occurrence];
      if (checkbox) {
        const checkboxRect=checkbox.getBoundingClientRect?.();
        if (checkboxRect) {
          const geometric=all('button,[role="button"]', document, true)
            .filter(button => {
              if (!matchesTitle(button)) return false;
              const rect=button.getBoundingClientRect?.();
              return rect && rect.bottom >= checkboxRect.top && rect.top <= checkboxRect.bottom;
            })
            .sort((a,b) => Math.abs(a.getBoundingClientRect().top-checkboxRect.top)
              - Math.abs(b.getBoundingClientRect().top-checkboxRect.top));
          if (geometric[0]) return geometric[0];
        }
        let box=checkbox.parentElement;
        for (let depth=0; box && depth<12; depth++, box=box.parentElement) {
          if ((box.matches?.('button,[role="button"]') || false) && matchesTitle(box)) return box;
          const rowButtons=all('button,[role="button"]',box,true).filter(button => {
            return matchesTitle(button);
          });
          if (rowButtons[0]) return rowButtons[0];
        }
      }
      const buttons=all('button,[role="button"]', document, true).filter(button => {
        return matchesTitle(button);
      });
      const exact=buttons.filter(button => [label(button), norm(button.getAttribute('title')||''), deepText(button)].includes(wanted));
      const matches=exact.length ? exact : buttons;
      if (matches[occurrence]) return matches[occurrence];
      // Some NotebookLM builds expose the row as a generic element in the
      // isolated DOM even though accessibility maps it to a button. Find the
      // smallest visible element whose rendered text is exactly the title and
      // click it so the row's handler receives the event.
      const textNodes=all('*', document, true)
        .filter(el => !/^(INPUT|CHECKBOX|SVG|PATH)$/.test(el.tagName) && visible(el))
        .filter(el => deepText(el) === wanted)
        .sort((a,b) => (a.getClientRects()[0]?.width||1)*(a.getClientRects()[0]?.height||1)
          - (b.getClientRects()[0]?.width||1)*(b.getClientRects()[0]?.height||1));
      return textNodes[occurrence];
    };
    let target=find();
    if (!target) {
      // The list can be re-rendered between the enumeration action and the
      // first source open. Re-open the group once before treating the source
      // as missing; this is common after NotebookLM virtualizes its rows.
      const group=all('button,[role="button"]', document, true).find(button =>
        /Gemini 的对话|Gemini conversations/i.test(label(button)));
      if (group && group.getAttribute('aria-expanded') !== 'true') {
        group.click();
        await new Promise(resolve => setTimeout(resolve, 900));
        target=find();
      }
    }
    if (!target) {
      const scrollers=all('*', document, true).filter(el => {
        const style=getComputedStyle(el);
        return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight>el.clientHeight+20;
      }).slice(0,8);
      for (let round=0; round<40 && !target; round++) {
        for (const sc of scrollers) sc.scrollTop=Math.min(sc.scrollHeight,round*Math.max(240,sc.clientHeight*.8));
        await new Promise(resolve=>setTimeout(resolve,500));
        target=find();
      }
    }
    if (!target) throw new Error('notebooklm_source_not_found');
    target.click();
    for (let i=0;i<60;i++) {
      if (norm(document.querySelector('#source-title')?.textContent)===wanted) {
        const ids=new Set(all('element-list-renderer .paragraph').flatMap(el=>{
          const m=norm(el.textContent).match(/^User:\s*https?:\/\/(?:www\.)?youtube\.com\/watch\?v=\s*([\w-]{11})(?![\w-])/i);
          return m?[m[1]]:[];
        }));
        if (ids.size===1) return {video_id:[...ids][0]};
        if (ids.size>1) throw new Error('source_video_ambiguous');
      }
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    throw new Error('source_user_video_not_ready');
  }
  // One-time recovery helpers. These only read links that Gemini already
  // renders; they never send a prompt, export a response body or call a model.
  if (action === 'verify-notebook') {
    const expected = new URL(args.notebook);
    if (location.origin !== expected.origin || notebookPath(location.href) !== notebookPath(expected.href)) {
      throw new Error('notebook_mismatch');
    }
    const wanted = norm(args.channel_name || '');
    const path = notebookPath(location.href);
    // A hidden accessibility <h1> ("Gemini") can precede the Notebook title.
    // Picking the first heading used to fail channel verification even though
    // the correct notebook was open. Accept any heading, or the sidebar link
    // that points at this notebook, as channel evidence.
    const linkNames = all('a[href]', document, true).filter(a => {
      try { return notebookPath(new URL(a.getAttribute('href') || '', location.origin).href) === path; }
      catch { return false; }
    }).map(a => norm(a.getAttribute('aria-label') || deepText(a))).filter(Boolean);
    const headings = all('h1,h2,[role="heading"]', document, true)
      .map(el => norm(deepText(el))).filter(Boolean);
    const candidates = [...new Set([...headings, ...linkNames, norm(document.title)])].filter(Boolean);
    if (!candidates.length) throw new Error('notebook_heading_missing');
    const matched = wanted ? candidates.find(text => text.includes(wanted)) : candidates[0];
    if (wanted && !matched) {
      // Gemini sometimes renders only generic accessibility labels while the
      // exact registered Notebook URL is already verified. Do not reject that
      // valid Notebook merely because its visible heading is generic.
      const genericOnly = candidates.every(text => /^(?:Gemini|Gemini 对话|Gemini Notebook 按钮|Google Gemini|Google Gemini Notebook|你的学习笔记本|学生)$/i.test(text.replace(/^与\s*/,'')));
      if (!genericOnly) {
        throw new Error('channel_mismatch:v2:' + candidates.slice(0, 5).join(' / ').slice(0, 140));
      }
    }
    const accountLabels = all('[aria-label]', document, true)
      .map(el => el.getAttribute('aria-label') || '')
      .filter(text => /Google (?:账号|帐号|帳戶|账户|Account)/i.test(text));
    if (args.account && accountLabels.length && !accountLabels.some(text => text.includes(args.account))) {
      throw new Error('account_mismatch:v2:' + accountLabels.slice(0, 2).join(' / ').slice(0, 120));
    }
    const accountLabel = accountLabels.find(text => text.includes(args.account)) || accountLabels[0] || '';
    return {notebook: location.href, name: matched || candidates[0], account: accountLabel,
            headings: candidates.slice(0, 5)};
  }
  // Only enumerate conversations that belong to the currently open Notebook.
  // The global "Recent" list on the Gemini home page belongs to no channel.
  if (action === 'collect-chats') {
    const found = [];
    const seenNodes = new WeakSet();
    const grab = () => {
      for (const el of all('button,[role="button"],a[href]', document, true)) {
        const aria = norm(el.getAttribute('aria-label') || '');
        if (!/近期对话|recent (?:chat|conversation)/i.test(aria)) continue;
        const title = norm(deepText(el)) || aria;
        if (!title || seenNodes.has(el)) continue;
        seenNodes.add(el);
        // Tiles are buttons without hrefs. Ids scraped from data-* attributes
        // are unreliable: navigating to one can be redirected back to the
        // Notebook and end in a generic ui_changed timeout. Always report
        // chat:null so the panel opens the tile by clicking and then reads the
        // real /app/<id> URL from the tab.
        found.push({title, chat: null, occurrence: found.filter(x => x.title === title).length});
      }
    };
    grab();
    const scrollers = all('*', document, true).filter(el => {
      const style = getComputedStyle(el);
      return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 20;
    }).slice(0, 6);
    let stable = 0;
    for (let round = 0; round < 20 && scrollers.length; round++) {
      for (const sc of scrollers) sc.scrollTop = sc.scrollHeight;
      await new Promise(resolve => setTimeout(resolve, 1200));
      const before = found.length;
      grab();
      stable = found.length === before ? stable + 1 : 0;
      if (stable >= 3) break;
    }
    return {chats: found};
  }
  if (action === 'open-recent-chat') {
    const wanted = norm(args.title);
    const find = () => {
      const candidates = all('button,[role="button"],a[href]').filter(el =>
        /近期对话|recent (?:chat|conversation)/i.test(el.getAttribute('aria-label') || ''))
        .map(el => ({el, title:norm(deepText(el)) || norm(el.getAttribute('aria-label') || '')}))
        .filter(item => item.title && (item.title === wanted || item.title.includes(wanted) || wanted.includes(item.title)));
      const exact = candidates.filter(item => item.title === wanted);
      const matches = exact.length ? exact : candidates;
      return matches[Number.isInteger(args.occurrence) ? args.occurrence : 0]?.el;
    };
    let target = find();
    if (!target) {
      // Notebook recent-chat tiles are virtualized. Walk each scrollable
      // container so off-screen tiles are rendered before matching.
      const scrollers = all('*', document, true).filter(el => {
        const style = getComputedStyle(el);
        return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 20;
      }).slice(0, 8);
      for (let round=0; round<30 && !target; round++) {
        for (const sc of scrollers) {
          sc.scrollTop = Math.min(sc.scrollHeight, round * Math.max(240, sc.clientHeight * 0.8));
        }
        await new Promise(resolve => setTimeout(resolve, 500));
        target = find();
      }
    }
    if (!target) throw new Error('recent_chat_not_found');
    setTimeout(() => target.click(), 150);
    return {clicked: true};
  }
  if (action === 'read-video') {
    const pattern = /(?:youtube\.com\/watch[^\s"'<>]*[?&]v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/shorts\/)([A-Za-z0-9_-]{11})/i;
    const sources = [];
    for (const el of all('a[href],[aria-label],[title],[data-url],user-query,[data-message-author-role="user"]', document, true)) {
      sources.push(el.getAttribute('href') || '', el.getAttribute('aria-label') || '',
                   el.getAttribute('title') || '', el.getAttribute('data-url') || '', deepText(el));
    }
    sources.push(deepText(document.body));
    for (const text of sources) {
      const match = String(text || '').match(pattern);
      if (match) return {video_id: match[1]};
    }
    return {video_id: null};
  }
  if (action === 'identity') {
    // Notebook pages often render the account menu after the shell and
    // notebook title. Do not classify that short loading window as an
    // account mismatch.
    await wait(() => { try { return account(); } catch { return false; } });
    if (!sameNotebook(args.notebook)) throw new Error('notebook_mismatch');
    return {account:args.account, notebook:args.notebook};
  }
  // Notebook identity is checked on the Notebook page before the workflow
  // opens a chat. Current Gemini chat pages omit the account control, so only
  // require it here when the caller has not supplied that same-tab evidence.
  if (!args.identity_checked) account();
  await wait(() => queryNodes().length && replyNodes().some(el=>deepText(el).length>0));
  const chatEvidence = () => {
    if (!args.identity_checked) account();
    const actualChatPath=notebookPath(location.href), expectedChatPath=notebookPath(args.chat);
    if (location.origin !== 'https://gemini.google.com' || actualChatPath !== expectedChatPath) {
      throw new Error(`notebook_mismatch:actual=${actualChatPath}:expected=${expectedChatPath}`);
    }
    const queries = queryNodes();
    const queryMatch = queries.some(el => deepText(el).includes(args.video_id));
    if (!queryMatch) throw new Error('video_mismatch');
    const replies = replyNodes();
    // Semantic/content verification already happened at summary registration.
    // Here only check that the original response remains present and stopped.
    if (!replies.length || deepText(replies.at(-1)).length < 80 || all('button[aria-label]').some(el => /停止回答|停止生成|Stop response|Stop generating/i.test(el.getAttribute('aria-label')))) throw new Error('summary_unconfirmed');
    return {account:args.account, video_checked:true, summary_present:true};
  };
  if (action === 'chat-check') return chatEvidence();
  if (action === 'chat-back') {
    chatEvidence();
    const containers = all('[data-test-id="project-chat-back-button"],[data-test-id*="chat-back"]');
    const scoped = containers.flatMap(container => {
      const controls = all('button,[role="button"],[role="link"],a',container);
      return controls.length ? controls : [container];
    }).filter(el => el && el.getClientRects && el.getClientRects().length);
    if (scoped.length) scoped[0].click();
    else {
      // Gemini has used several accessible names for the same return control.
      // Restrict the fallback to explicit notebook/project semantics so an
      // unrelated page button is never selected by a generic "back" match.
      const back = all('button,[role="button"],[role="link"],a').filter(el => {
        const aria = norm(el.getAttribute('aria-label') || el.getAttribute('title') || '');
        const text = label(el);
        return /^(?:返回|Back)(?:到|至|to)?(?:笔记本|Notebook|项目|Project)?$/i.test(text)
          || /(?:返回.*(?:笔记本|项目)|back\s+to\s+(?:notebook|project))/i.test(aria);
      });
      if (!back.length) {
        // Some Gemini layouts do not render a product-owned return button.
        // chatEvidence() already verified the registered chat, so reopening
        // the registered Notebook is the safe equivalent and is re-verified
        // by the caller before any further mutation.
        if (!args.notebook) throw new Error('notebook_mismatch:chat-back-control-not-found');
        location.href = args.notebook;
      } else back[0].click();
    }
    return {clicked:true};
  }
  const renameFieldSelector = 'input[type="text"],input:not([type]),textarea,[contenteditable="true"]';
  const findRenameDialog = () => {
    const standard = all('[role="dialog"],[aria-modal="true"]')
      .find(el => all(renameFieldSelector,el).length === 1);
    if (standard) return standard;
    // Current Gemini build renders this modal without role=dialog or
    // aria-modal. Its stable visible marker is the heading “重命名此对话”;
    // walk upward to the smallest overlay containing the title field and
    // the Rename/重命名 action.
    const heading = all('*').filter(el => {
      const text=norm(deepText(el));
      return visible(el) && (text === '重命名此对话' || text === 'Rename this conversation');
    }).sort((a,b) => (a.getClientRects()[0]?.width||999999)*(a.getClientRects()[0]?.height||999999)
      - (b.getClientRects()[0]?.width||999999)*(b.getClientRects()[0]?.height||999999))[0];
    for (let box=heading; box; box=box.parentElement) {
      const fields=all(renameFieldSelector,box);
      const actions=all('button,[role="button"],button mat-button',box)
        .map(label).filter(text => ['重命名','Rename','取消','Cancel'].includes(text));
      if (fields.length === 1 && actions.length) return box;
    }
    return null;
  };
  const openChatDialog = async () => {
    // A failed previous attempt can leave the rename modal open.  Do not
    // click the conversation menu again in that state; reuse the visible
    // modal and let the caller retry the same item safely.
    const existing = findRenameDialog();
    if (existing) return existing;
    button(['打开对话操作菜单。','Open conversation actions menu.','Open conversation actions menu']).click();
    await wait(() => all('[role="menuitem"]').some(el => ['重命名','Rename'].includes(label(el))));
    button(['重命名','Rename']).click();
    return wait(findRenameDialog);
  };
  const dialogField = dialog => one(all(renameFieldSelector,dialog));
  const fieldValue = field => field?.isContentEditable ? norm(field.textContent) : norm(field?.value || '');
  if (action === 'chat-title') {
    chatEvidence();
    const dialog = await openChatDialog();
    const title = fieldValue(dialogField(dialog));
    // Batch mode immediately follows this read with chat-rename.  Keep the
    // same modal alive so Gemini does not re-render it between two actions.
    // Preview mode still closes it because no mutation follows.
    if (!args.keep_dialog) button(['取消','Cancel'],dialog).click();
    if (!title) throw new Error('ui_changed');
    return {title};
  }
  const setInput = (field,value) => {
    if (field.isContentEditable) {
      field.textContent=value;
    } else {
      const proto = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto,'value').set.call(field,value);
    }
    field.focus?.();
    const InputEventCtor = globalThis.InputEvent || Event;
    field.dispatchEvent(new InputEventCtor('input',{bubbles:true,composed:true,inputType:'insertText',data:value}));
    field.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
    field.blur?.();
  };
  if (action === 'chat-rename') {
    chatEvidence();
    const dialog = await openChatDialog();
    if (fieldValue(dialogField(dialog)) !== args.old_chat_title && fieldValue(dialogField(dialog)) !== args.title) throw new Error('save_unverified');
    const field = dialogField(dialog);
    setInput(field,args.title);
    if (fieldValue(field) !== args.title) throw new Error('save_unverified');
    const confirm = button(['重命名','Rename'],dialog);
    if (confirm.disabled || confirm.getAttribute('aria-disabled') === 'true') throw new Error('save_unverified');
    confirm.focus?.();
    confirm.click();
    // Gemini's dialog is a component-managed form. On some UI revisions a
    // programmatic click updates the button state but does not submit the
    // form; use Enter only if the same dialog is still visible.
    await new Promise(resolve => setTimeout(resolve,150));
    if (visible(dialog)) {
      // The current dialog is form-backed in some Gemini builds. Submitting
      // the owning form is more reliable than dispatching a click from the
      // extension isolated world.
      const form = field.form || dialog.querySelector?.('form');
      if (form?.requestSubmit) form.requestSubmit(confirm);
      else if (form?.submit) form.submit();
      await new Promise(resolve => setTimeout(resolve,150));
    }
    if (visible(dialog)) {
      // Some Gemini revisions listen to the composed pointer sequence rather
      // than a synthetic HTMLElement.click() from the isolated world.
      for (const type of ['mousedown','mouseup','click']) {
        confirm.dispatchEvent?.(new MouseEvent(type,{bubbles:true,composed:true,view:globalThis.window}));
      }
      await new Promise(resolve => setTimeout(resolve,150));
    }
    if (visible(dialog)) {
      field.focus?.();
      field.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,composed:true}));
      field.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',code:'Enter',bubbles:true,composed:true}));
    }
    // A few builds keep the modal mounted briefly after accepting the value.
    // Give the page one more render cycle before classifying it as a UI error.
    await wait(() => !visible(dialog));
    return {saved:true};
  }
  throw new Error('ui_changed');
};


globalThis.__dqPageAction = async function (action, args) {
  try {
    return {ok: true, data: await globalThis.__dqImpl(action, args)};
  } catch (error) {
    return {
      ok: false,
      error: String((error && error.message) || error),
      stack: String((error && error.stack) || '').slice(0, 400)
    };
  }
};
