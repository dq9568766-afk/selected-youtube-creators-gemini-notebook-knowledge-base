// Executes only in the extension's isolated world. All inputs are task metadata.
// No fetch, model calls, application internals, response export or credential access.
export async function pageAction(action, args) {
  const norm = value => (value || '').replace(/\s+/g, ' ').trim();
  const visible = el => !!el && el.getClientRects().length > 0;
  // Gemini renders parts of conversation content inside open shadow roots.
  // querySelectorAll on document does not cross those boundaries, even though
  // the content is visible and exposed in the accessibility tree.
  const all = (selector, root = document) => {
    const found = new Set(), pending = [root], visited = new Set();
    while (pending.length) {
      const current = pending.pop();
      if (!current || visited.has(current)) continue;
      visited.add(current);
      if (current.shadowRoot) pending.push(current.shadowRoot);
      for (const el of current.querySelectorAll?.(selector) || []) found.add(el);
      for (const el of current.querySelectorAll?.('*') || []) if (el.shadowRoot) pending.push(el.shadowRoot);
    }
    return [...found].filter(visible);
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
    const emails = [...new Set(labels.flatMap(t => t.match(/[^\s()（）<>]+@[^\s()（）<>]+\.[^\s()（）<>]+/g) || []))];
    if (emails.length !== 1 || emails[0] !== args.account) throw new Error('account_mismatch');
    return emails[0];
  };
  const notebookPath = value => new URL(value).pathname.replace(/^\/u\/\d+/, '');
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
  const blocked = location.pathname.startsWith('/sorry') || all('[role="alert"],[role="dialog"]').some(el => /验证码|异常流量|配额|使用上限|captcha|unusual traffic|quota exceeded|usage limit/i.test(deepText(el)));
  if (blocked) throw new Error('interrupted');
  if (action === 'identity') {
    account();
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
    if (location.origin !== 'https://gemini.google.com' || notebookPath(location.href) !== new URL(args.chat).pathname) throw new Error('notebook_mismatch');
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
    const containers = all('[data-test-id="project-chat-back-button"]');
    if (containers.length === 1) {
      one(all('button',containers[0]),'notebook_mismatch').click();
    } else {
      // New Gemini chat pages expose a plain 返回/Back button instead of
      // the old project-chat data-test-id.
      const back = all('button,[role="button"]').filter(el =>
        ['返回','Back'].includes(label(el)) || /(?:返回|back)/i.test(el.getAttribute('aria-label') || '')
      );
      one(back,'notebook_mismatch').click();
    }
    return {clicked:true};
  }
  const openChatDialog = async () => {
    button(['打开对话操作菜单。','Open conversation actions menu.','Open conversation actions menu']).click();
    await wait(() => all('[role="menuitem"]').some(el => ['重命名','Rename'].includes(label(el))));
    button(['重命名','Rename']).click();
    return wait(() => all('[role="dialog"]').find(el => all('input[type="text"],input:not([type])',el).length === 1));
  };
  const dialogField = dialog => one(all('input[type="text"],input:not([type])',dialog));
  if (action === 'chat-title') {
    chatEvidence();
    const dialog = await openChatDialog();
    const title = dialogField(dialog).value;
    button(['取消','Cancel'],dialog).click();
    if (!title) throw new Error('ui_changed');
    return {title};
  }
  const setInput = (field,value) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,value);
    field.focus?.();
    const InputEventCtor = globalThis.InputEvent || Event;
    field.dispatchEvent(new InputEventCtor('input',{bubbles:true,composed:true,inputType:'insertText',data:value}));
    field.dispatchEvent(new Event('change',{bubbles:true,composed:true}));
    field.blur?.();
  };
  if (action === 'chat-rename') {
    chatEvidence();
    const dialog = await openChatDialog();
    if (dialogField(dialog).value !== args.old_chat_title && dialogField(dialog).value !== args.title) throw new Error('save_unverified');
    const field = dialogField(dialog);
    setInput(field,args.title);
    if (field.value !== args.title) throw new Error('save_unverified');
    const confirm = button(['重命名','Rename'],dialog);
    if (confirm.disabled || confirm.getAttribute('aria-disabled') === 'true') throw new Error('save_unverified');
    confirm.focus?.();
    confirm.click();
    // Gemini's dialog is a component-managed form. On some UI revisions a
    // programmatic click updates the button state but does not submit the
    // form; use Enter only if the same dialog is still visible.
    await new Promise(resolve => setTimeout(resolve,150));
    if (visible(dialog)) {
      field.focus?.();
      field.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,composed:true}));
      field.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',code:'Enter',bubbles:true,composed:true}));
    }
    await wait(() => !visible(dialog));
    return {saved:true};
  }
  throw new Error('ui_changed');
}
