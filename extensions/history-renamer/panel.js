// page.js is the source for page_inject.js (see build_page_inject.py). It is
// no longer loaded as a module here: Chrome could not reliably serialize the
// long imported function when passed to scripting.executeScript({func}).
import {openOrReuseTab} from './tabs.js';
import {runWithRecovery} from './recovery.js';

const $ = id => document.getElementById(id);
let pairing, running = false, stopping = false, workTab, notebookTab, notebookUrl, channelName, accountName;
let heartbeatTimer, keepaliveTimer, heartbeatFailures=0;
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
const status = text => { $('status').textContent = text; };
function checkStop() { if (stopping) throw new Error('interrupted'); }

async function api(path, body = {}) {
  if (!pairing) throw new Error('请先点击启动本机服务');
  let response;
  try {
    response = await fetch(pairing.bridge + path, {
      method:'POST', headers:{'Content-Type':'application/json','X-YTG-Pairing':pairing.pairing},
      body:JSON.stringify(body), signal:AbortSignal.timeout(15000), cache:'no-store'
    });
  } catch (error) {
    throw new Error(`bridge_disconnected:${path}:${error?.name || 'network_error'}`);
  }
  let result;
  try { result = await response.json(); }
  catch { throw new Error(`bridge_invalid_response:${path}:HTTP_${response.status}`); }
  if (!response.ok || result.stopped) throw new Error(`bridge_rejected:${path}:HTTP_${response.status}:${result.stopped || '本机服务拒绝操作'}`);
  return result;
}

function startHeartbeat() {
  stopHeartbeat();
  heartbeatFailures=0;
  heartbeatTimer=setInterval(async()=>{
    if (!running || !pairing) return;
    try {
      await api('/heartbeat');
      heartbeatFailures=0;
    } catch {
      heartbeatFailures++;
      if (heartbeatFailures>=3) {
        stopping=true;
        status('本机服务连续 3 次未响应，已停止并保留未完成项目。');
      } else {
        status('本机服务心跳暂时失败，正在自动重试（'+heartbeatFailures+'/3）…');
      }
    }
  },30000);
}
function stopHeartbeat() {
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer=undefined; }
}
// Keep the worker lease alive while the panel is open, even when no batch is
// running. A lapsed 15-minute lease used to turn every later bridge call into
// a 409 that the panel reported as a page-structure error.
function startKeepalive() {
  if (keepaliveTimer) clearInterval(keepaliveTimer);
  keepaliveTimer=setInterval(async()=>{
    if (!pairing) return;
    try { await api('/heartbeat'); } catch { /* transient; next tick retries */ }
  },60000);
}
function native(action, payload={}) {
  return new Promise((resolve, reject) => {
    let settled=false;
    const finish=(value, error=false)=>{
      if (settled) return;
      settled=true;
      clearTimeout(timer);
      error ? reject(new Error(value)) : resolve(value);
    };
    const timer=setTimeout(() => finish('扩展后台在 15 秒内没有返回。请重新加载扩展并重新打开面板。', true),15000);
    try {
      chrome.runtime.sendMessage({type:`native-${action}`,...payload}, response => {
        const lastError=chrome.runtime.lastError;
        if (lastError) finish(`扩展后台调用失败：${lastError.message}`, true);
        else finish(response || {ok:false,error:'扩展后台未返回结果'});
      });
    } catch (error) {
      finish(`扩展后台调用异常：${error?.message || error}`, true);
    }
  });
}
function selectedRun() {
  const value=$('runSelect')?.value;
  if (!value) return null;
  try { return JSON.parse(value); } catch { return null; }
}
function updateNativeStop() {
  const choice=selectedRun();
  // A bridge can remain active after its 15-minute worker lease expires.
  // Keep stop available for any explicitly selected run so that stale
  // sessions can be released even when the candidate is shown as recoverable.
  $('nativeStop').disabled=!(pairing || choice?.run);
  $('cleanupRun').disabled=!choice?.run;
}
async function refreshCandidates() {
  status('正在读取可用历史改名任务…');
  const result=await native('candidates',{fresh:true});
  if (!result?.candidates) throw new Error(result?.error || '没有读取到可用任务');
  const select=$('runSelect'); select.replaceChildren();
  if (!result.candidates.length) { select.add(new Option('没有有效的历史扩展任务','')); status('没有未完成的历史扩展任务。'); return; }
  for (const item of result.candidates) {
    const duplicate=item.duplicate_runs ? `｜另有 ${item.duplicate_runs} 个重复 run` : '';
    select.add(new Option(`${item.channel_name}｜${item.live?'运行中':'可恢复'}${duplicate}｜${item.run}`,JSON.stringify(item)));
  }
  select.selectedIndex=0; updateNativeStop(); status(`已读取 ${result.candidates.length} 个历史任务，请选择目标频道后启动。`);
}
async function bindPairing(value, selected=null, mappingOnly=false) {
  pairing=value;
  notebookUrl=undefined; channelName=undefined; accountName=undefined;
  const job=mappingOnly ? {notebook:selected?.notebook,channel_name:selected?.channel_name,account:selected?.account||''} : await api('/next');
  notebookUrl=job.notebook; channelName=job.channel_name; accountName=job.account;
  if (!notebookUrl || !channelName) throw new Error('当前 run 缺少登记 Notebook 或频道信息');
  let roster='';
  try {
    const r=await api('/collect',{items:[]});
    roster=`\n名单：${r.report?.total||0} 条待改名`;
  } catch { /* Keep the panel usable even if the roster probe fails. */ }
  $('mapping').textContent=`本机服务：已启动并已连接\n频道：${job.channel_name||'未登记名称'}\n账号：${job.account}\nGemini：${job.notebook}` + roster;
  status(mappingOnly ? '本机服务已启动并已连接。当前为重建映射模式，不领取批量改名项目。' : job.done
    ? '名单为空。若曾发生数据库回退，请先点「重建映射」，再执行只检查一条。'
    : '本机服务已启动并已连接。名单已就绪，先点「只检查一条」。');
  startKeepalive();
}

async function pace() {
  for (;;) {
    checkStop();
    const result = await api('/pace');
    if (result.ready) return;
    const until = Date.now() + result.seconds_remaining*1000;
    while (Date.now()<until) { checkStop(); await sleep(Math.min(500,until-Date.now())); }
  }
}

async function page(action,job,mutation = false) {
  checkStop();
  if (mutation) await pace();
  // Chrome MV3 serializes `func` via toString(). Injecting the long pageAction
  // function imported from an ES module silently returned result:null on this
  // Gemini build. Install a classic script in the isolated world instead and
  // call a tiny wrapper, so every outcome (including thrown errors) is data.
  // Notebook conversation tiles can take several render cycles after a
  // re-anchor. Give that read-only action extra time without weakening the
  // immediate-stop rules for identity or anti-bot failures.
  const maxAttempts = action==='open-recent-chat' ? 8 : 3;
  for (let attempt=0; attempt<maxAttempts; attempt++) {
    checkStop();
    const current = await chrome.tabs.get(workTab);
    const url = new URL(current.url || 'about:blank');
    if (blockedUrl(current.url)) throw new Error('rate_limited');
    if (!['gemini.google.com','notebook.google.com'].includes(url.hostname)) throw new Error('interrupted');
    let results;
    try {
      await chrome.scripting.executeScript({target:{tabId:workTab},files:['page_inject.js']});
      results = await chrome.scripting.executeScript({
        target:{tabId:workTab},
        func:(a,j)=>globalThis.__dqPageAction
          ? globalThis.__dqPageAction(a,j)
          : {ok:false,error:'injector_missing'},
        args:[action,job]
      });
    } catch (error) {
      if (attempt===maxAttempts-1) throw new Error(`page_inject_failed:${action}:${String(error?.message||error).slice(0,110)}`);
      await sleep(900);
      continue;
    }
    const first = results?.[0];
    if (first?.exceptionDetails) {
      const detail = first.exceptionDetails?.exception?.description
        || first.exceptionDetails?.text || 'injection_exception';
      if (attempt===maxAttempts-1) throw new Error(`page_exception:${action}:${String(detail).slice(0,140)}`);
      await sleep(900);
      continue;
    }
    const value = first?.result;
    if (value && typeof value === 'object') {
      if (value.ok === true) return value.data;
      if (value.ok === false) {
        const reason = String(value.error || 'page_action_failed');
        // Anti-bot and identity mismatches must stop immediately, not retry.
        if (/rate_limited|interrupted|notebook_mismatch|account_mismatch|channel_mismatch/i.test(reason)) {
          throw new Error(reason);
        }
        if (attempt===maxAttempts-1) throw new Error(`page_error:${action}:${reason.slice(0,140)}`);
        await sleep(action==='open-recent-chat' ? 1500 : 900);
        continue;
      }
    }
    if (attempt===maxAttempts-1) throw new Error(`page_failed:${action}:${JSON.stringify(first||null).slice(0,160)}`);
    await sleep(900);
  }
  throw new Error('page_ui_changed:' + action);
}

async function waitPage(expected) {
  for (let i=0;i<100;i++) {
    checkStop();
    const current = await chrome.tabs.get(workTab);
    if (current.status==='complete') {
      if (!current.url || !current.url.startsWith('https://')) throw new Error('interrupted');
      // Do not follow redirects to login or another notebook automatically.
      const actual = new URL(current.url), target = new URL(expected);
      if (actual.origin===target.origin && actual.pathname.replace(/^\/u\/\d+/,'')===target.pathname.replace(/^\/u\/\d+/,'')) return;
      if (actual.hostname==='accounts.google.com' || actual.pathname.startsWith('/sorry')) throw new Error('interrupted');
    }
    await sleep(300);
  }
  throw new Error('ui_changed');
}

async function navigate(url) {
  await pace();
  if (workTab === undefined && accountName) {
    const candidates = await chrome.tabs.query({url:'https://gemini.google.com/*'});
    for (const candidate of candidates) {
      try {
        const probe = await chrome.scripting.executeScript({
          target:{tabId:candidate.id},
          func:expected => {
            const text=[];
            const seen=new Set(), pending=[document];
            while (pending.length) {
              const root=pending.pop();
              if (!root || seen.has(root)) continue;
              seen.add(root);
              text.push(root.body?.innerText || root.textContent || '');
              for (const el of root.querySelectorAll?.('*') || []) if (el.shadowRoot) pending.push(el.shadowRoot);
            }
            return text.join(' ').includes(expected);
          },
          args:[accountName]
        });
        if (probe?.[0]?.result) { workTab=candidate.id; break; }
      } catch { /* tab may be changing; try the next Gemini tab */ }
    }
  }
  workTab = await openOrReuseTab(chrome.tabs,url,workTab);
  await waitPage(url);
  // Initial render is allowed time to expose the account header. No response
  // body is returned or stored; the page adapter checks identity before acting.
  for (let i=0;i<40;i++) {
    checkStop();
    const state = await chrome.scripting.executeScript({target:{tabId:workTab},func:() => !!document.querySelector('[aria-label*="Google"]')});
    if (state[0]?.result) return;
    await sleep(250);
  }
  throw new Error('account_mismatch');
}

async function chat(job,preview,setPhase=()=>{}) {
  // The chat view does not expose the Google account button in the current
  // Gemini UI. Verify account + Notebook on the Notebook page first, then
  // carry that same-tab identity check into the chat inspection.
  setPhase('打开登记笔记本');
  await navigate(job.notebook);
  setPhase('核对账号和笔记本');
  await page('identity',job);
  const checkedJob = {...job,identity_checked:true};
  setPhase('打开历史聊天');
  await navigate(job.chat);
  setPhase('读取视频和总结结构');
  await page('chat-check',checkedJob);
  // Follow the product's own return control to establish actual membership.
  setPhase('验证聊天所属笔记本');
  await page('chat-back',checkedJob,true);
  await sleep(500);
  await waitPage(job.notebook);
  setPhase('重新核对笔记本');
  await page('identity',job);
  setPhase('重新打开历史聊天');
  await navigate(job.chat);
  setPhase('读取当前聊天标题');
  const old = await page('chat-title',checkedJob,true);
  if (preview) return;
  await api('/checkpoint',{video_id:job.video_id,old_chat_title:old.title});
  job.old_chat_title ||= old.title;
  if (old.title !== job.title) await page('chat-rename',{...checkedJob,old_chat_title:old.title},true);
  // Reopen the chat after saving; read the title from its actual rename field.
  await navigate(job.chat);
  const evidence = await page('chat-check',checkedJob);
  const readback = await page('chat-title',checkedJob,true);
  if (readback.title !== job.title) throw new Error('save_unverified');
  await api('/result',{...evidence,kind:'chat',status:'verified',title:job.title,video_id:job.video_id,
    chat:job.chat,notebook:job.notebook,membership_checked:true,old_chat_title:job.old_chat_title,
    verification_mode:'extension-reopen-v2',recovery_attempts:job.recovery_attempts || 0,
    verification_phase:'readback'});
}

$('nativeStart').onclick=async()=>{
  try {
    const selected=$('runSelect').value;
    if (!selected) { status('请先读取并选择目标频道的历史 run。'); return; }
    const choice=JSON.parse(selected);
    status('正在启动本机服务…');
    // A previous task may have left a live Native Messaging port behind.
    // Force a fresh host connection so its pairing belongs to this run.
    let result=await native('start',{run:choice.run,resume:!choice.live,fresh:true});
    if (!result?.ok) throw new Error(result?.error || '本机组件未安装');
    try {
      // Starting the service must not claim a rename item. Mapping rebuilds
      // use the bridge before any batch item is eligible for /next.
      await bindPairing(result.pairing,choice,true);
    } catch (error) {
      // The native host can return a pairing file from a dead/replaced bridge.
      // Repair only the explicitly selected run, then establish one fresh
      // session and validate it through /next before reporting success.
      if (!String(error?.message || error).includes('pairing required')) throw error;
      status('检测到旧配对会话，正在清理当前频道并重新连接…');
      await native('cleanup',{run:choice.run});
      pairing=null;
      result=await native('start',{run:choice.run,resume:true,fresh:true});
      if (!result?.ok) throw new Error(result?.error || '重新启动本机服务失败');
      await bindPairing(result.pairing,choice,true);
    }
    $('nativeStop').disabled=false;
  } catch (error) {
    $('mapping').textContent='本机服务：未连接';
    status(`启动失败：${String(error?.message || error || '未知错误').slice(0,160)}。首次使用请运行 install_native_host.cmd。`);
  }
};
$('nativeStop').onclick=async()=>{
  if (running) { status('当前仍在处理项目，请先点击“停止”结束批量操作。'); return; }
  $('nativeStop').disabled=true;
  try {
    if (pairing) { try { await api('/release'); } catch { /* stop still terminates the local child */ } }
    const choice=selectedRun();
    await native('stop',{run:choice?.run || null});
    pairing=null; notebookUrl=undefined; channelName=undefined; accountName=undefined;
    $('mapping').textContent='本机服务：未连接';
    status('本机服务已停止，频道租约已释放。');
  } catch (error) {
    $('nativeStop').disabled=false;
    status(`停止本机服务失败：${String(error?.message || error).slice(0,160)}`);
  }
};
$('cleanupRun').onclick=async()=>{
  const choice=selectedRun();
  if (!choice?.run) { status('请先选择需要清理的历史 run。'); return; }
  if (running) { status('当前仍在处理项目，请先停止批量操作。'); return; }
  $('cleanupRun').disabled=true;
  try {
    const result=await native('cleanup',{run:choice.run});
    if (!result?.ok) throw new Error(result?.error || '残留状态清理失败');
    pairing=null; notebookUrl=undefined; channelName=undefined; accountName=undefined;
    $('mapping').textContent='本机服务：未连接';
    status('已清理该 run 的残留服务、bridge、租约和会话状态。');
    await refreshCandidates();
  } catch (error) {
    $('cleanupRun').disabled=false;
    status(`清理残留状态失败：${String(error?.message || error).slice(0,160)}`);
  }
};
$('runSelect').onchange=updateNativeStop;
$('refreshCandidates').onclick=()=>refreshCandidates().catch(error=>status(`读取任务失败：${String(error?.message||error).slice(0,160)}`));

const reasons = new Set(['ui_changed','account_mismatch','notebook_mismatch','video_mismatch','summary_unconfirmed','save_unverified','interrupted']);
const messages = {ui_changed:'页面结构不匹配，需要适配',account_mismatch:'账号未匹配或尚未加载',notebook_mismatch:'笔记本或聊天归属未核实',video_mismatch:'原视频链接未匹配',summary_unconfirmed:'总结正文或生成状态未确认',save_unverified:'保存后的名称未核实',interrupted:'已停止，保留已登记进度',bridge_disconnected:'本机改名服务连接中断',bridge_rejected:'本机服务拒绝领取或继续操作',bridge_invalid_response:'本机服务返回了无效响应'};

async function run(preview,one) {
  if (running) return;
  if (!$('confirmed').checked) { status('请先核对账号、笔记本并勾选确认。'); return; }
  // Never inherit a Gemini tab left by a previous run or another account.
  // The first navigation creates a fresh tab in the browser profile that
  // owns this extension; subsequent steps in this run reuse that tab only.
  workTab=undefined;
  running=true; stopping=false;
  startHeartbeat();
  ['preview','one','start'].forEach(id=>$(id).disabled=true);
  $('stop').disabled=false;
  let job,kind='chat',completed=0,phase='领取名单';
  try {
    for (;;) {
      checkStop();
      phase='领取名单';
      job=await api('/next');
      if (job.done) {
        if (job.report && job.report.total === 0) {
          status('名单为空，请先点「重建映射」恢复视频与已有聊天的对应关系；本机服务保持连接。');
          break;
        }
        await api('/release'); pairing=null; $('nativeStop').disabled=true; status('批次已完成，本机服务已退出并释放租约。可按项目规程恢复内置浏览器。'); break;
      }
      $('counts').textContent=`本次完成 ${completed} 条`;
      status(`${preview?'检查':'处理'}：${job.title}`);
      kind='chat';
      if (job.chat_status !== 'verified' || preview) {
        let recoveryAttempts=0;
        const outcome=await runWithRecovery(
          ()=>chat({...job,recovery_attempts:recoveryAttempts},preview,value=>{phase=value;}),
          async(reason,attempt)=>{
            recoveryAttempts=attempt;
            phase=`自动重开第${attempt}次（${messages[reason] || reason}）`;
            status(`${preview?'检查':'处理'}：${job.title}\n${phase}`);
            // The next chat attempt always starts at the registered Notebook.
            // No prompt is resent and no page body is exported during recovery.
            await sleep(500);
          },
        );
        job.recovery_attempts=outcome.recovery_attempts;
      }
      if (preview) { status('这一条的聊天归属、标题和视频匹配检查通过，尚未改名。'); break; }
      completed++;
      $('counts').textContent=`本次完成 ${completed} 条`;
      if (one) { status('一条改名及读回核对完成，可查看网页结果后开始批量。'); break; }
    }
  } catch (error) {
    const raw = String(error?.message || error || 'unknown_error');
    const prefix = raw.split(':',1)[0];
    const serviceReason = ['bridge_disconnected','bridge_rejected','bridge_invalid_response'].includes(prefix) ? prefix : null;
    const reason = [...reasons].find(value => raw === value || raw.startsWith(`${value}:`)) || serviceReason || 'ui_changed';
    if (job && !job.done && !preview) {
      try {
        await api('/result',{video_id:job.video_id,title:job.title,kind,status:'pending',reason,
          recovery_attempts:Number.isInteger(error?.recovery_attempts) ? error.recovery_attempts : 0,
          verification_phase:phase});
      } catch { /* Keep pending if the bridge disconnected. */ }
    }
    const detail = raw.replace(/[\r\n]/g,' ').slice(0,160) || reason;
    status(`${messages[reason] || '操作失败'}（步骤：${phase}；详情：${detail}）。未完成项目保留待处理。`);
    if (stopping && pairing) {
      try { await api('/release'); pairing=null; $('nativeStop').disabled=true; } catch { /* Queue lease expires if the local service is already gone. */ }
    }
  } finally {
    stopHeartbeat();
    running=false;
    ['preview','one','start'].forEach(id=>$(id).disabled=false);
    $('stop').disabled=true;
  }
}
$('preview').onclick=()=>run(true,true);
$('one').onclick=()=>run(false,true);
$('start').onclick=()=>run(false,false);
$('stop').onclick=()=>{stopping=true;status('正在停止，将保留已登记结果。');};

// One-time recovery after a task-database rollback. Every visit starts from
// the registered Notebook (the channel); the global "Recent" list is never
// enumerated. Visits are deliberately slow to avoid Google's anti-bot gate.
// Keep the project-wide 20-item cooling rule, while allowing the rebuild to
// continue without any total per-round limit.
const COOLDOWN_EVERY = 20;
const COOLDOWN_MS = 40000;
const rand = (min,max) => min + Math.random()*(max-min);
const blockedUrl = url => /google\.com\/sorry|\/sorry\/index|captcha/i.test(String(url||''));
async function slowSleep(ms) {
  const end=Date.now()+ms;
  while (Date.now()<end) { checkStop(); await sleep(Math.min(1000,Math.max(1,end-Date.now()))); }
}
async function gotoLight(url) {
  checkStop();
  const wanted = new URL(url).pathname.replace(/^\/u\/\d+/,'');
  const needHeading = /\/notebook\//i.test(wanted);
  workTab = await openOrReuseTab(chrome.tabs,url,workTab);
  let lastUrl='', stable=0;
  for (let i=0;i<160;i++) {
    checkStop();
    const tab = await chrome.tabs.get(workTab);
    if (blockedUrl(tab.url)) throw new Error('rate_limited');
    if (tab.status==='complete' && tab.url && tab.url.startsWith('https://gemini.google.com/')
        && new URL(tab.url).pathname.replace(/^\/u\/\d+/,'') === wanted) {
      // Gemini is an SPA: status=complete does not mean React has rendered.
      // Wait for a real heading (notebook) or non-empty body (chat) before
      // any injection, otherwise Chrome may discard the execution context.
      for (let k=0;k<40;k++) {
        checkStop();
        let ready;
        try {
          ready = await chrome.scripting.executeScript({target:{tabId:workTab},func:()=>({
            h1: document.querySelector('h1')?.textContent?.trim() || '',
            body: document.body?.innerText?.length || 0
          })});
        } catch { /* context still being replaced */ }
        const r = ready?.[0]?.result;
        if (r && r.body>50 && (!needHeading || r.h1)) {
          await sleep(600);
          const again = await chrome.tabs.get(workTab);
          if (blockedUrl(again.url)) throw new Error('rate_limited');
          if (again.status==='complete' && new URL(again.url||'about:blank').pathname.replace(/^\/u\/\d+/,'')===wanted) return;
        }
        await sleep(400);
      }
      throw new Error('page_not_ready:' + JSON.stringify({wanted,url:tab?.url||''}).slice(0,160));
    }
    if (tab.status==='complete' && tab.url) {
      stable = tab.url === lastUrl ? stable + 1 : 0;
      lastUrl = tab.url;
      if (stable >= 10) throw new Error('nav_redirect:' + JSON.stringify({wanted,url:tab.url}).slice(0,180));
    }
    await sleep(400);
  }
  const finalTab = await chrome.tabs.get(workTab).catch(()=>null);
  throw new Error('nav_timeout:' + JSON.stringify({wanted,url:finalTab?.url||'',status:finalTab?.status||''}).slice(0,180));
}
async function waitForChat() {
  for (let i=0;i<80;i++) {
    checkStop();
    const tab = await chrome.tabs.get(workTab);
    if (blockedUrl(tab.url)) throw new Error('rate_limited');
    if (tab.status==='complete' && /\/app\/[A-Za-z0-9_-]{12,32}/.test(tab.url||'')) return tab.url;
    await sleep(500);
  }
  const finalTab = await chrome.tabs.get(workTab).catch(()=>null);
  throw new Error('chat_not_opened:' + String(finalTab?.url||'').slice(0,140));
}
async function gotoNotebookLm() {
  const id=new URL(notebookUrl).pathname.split('/').filter(Boolean).pop();
  const url=`https://notebook.google.com/notebook/${id}`;
  notebookTab=await openOrReuseTab(chrome.tabs,url,notebookTab);
  for (let i=0;i<100;i++) {
    checkStop();
    const tab=await chrome.tabs.get(notebookTab);
    if (tab.status==='complete' && tab.url?.startsWith('https://notebook.google.com/notebook/')) {
      await sleep(700);
      return;
    }
    await sleep(400);
  }
  throw new Error('notebooklm_not_ready');
}
async function notebookPage(action,args={}) {
  const previous=workTab;
  workTab=notebookTab;
  try { return await page(action,args); }
  finally { workTab=previous; }
}
async function openNotebookSource(title,occurrence,missing) {
  const source=await notebookPage('open-notebooklm-source',{title,occurrence});
  if (!missing.has(source.video_id)) return source;
  const beforeTabs=await chrome.tabs.query({});
  const before=new Map(beforeTabs.map(t=>[t.id,t.url||'']));
  const isGeminiChat=url=>{
    try {
      const parsed=new URL(url||'');
      return parsed.hostname==='gemini.google.com' && /\/app\/[A-Za-z0-9_-]{8,}/.test(parsed.pathname);
    } catch { return false; }
  };
  const [{result:direct}]=await chrome.scripting.executeScript({
    target:{tabId:notebookTab},world:'MAIN',args:[title,new URL(notebookUrl).pathname.split('/').filter(Boolean).pop()],
    func:(expected,id)=>{
      const heading=document.querySelector('#source-title');
      if (location.pathname!=='/notebook/'+id || heading?.textContent.trim()!==expected) return '';
      const box=heading.closest('.source-title-container');
      const btn=box?.querySelector('button.source-link-button');
      if (!btn) return '';
      const original=window.open;
      let chat='';
      try {
        window.open=function(url,...args){
          if (/^https:\/\/gemini\.google\.com\/(?:u\/\d+\/)?app\/[\w-]+\/?(?:[?#].*)?$/.test(String(url))) {
            chat=String(url); return null;
          }
          return original.call(window,url,...args);
        };
        btn.click();
      } finally { window.open=original; }
      return chat;
    }
  });
  if (isGeminiChat(direct)) return {...source,url:direct};
  for (let i=0;i<40;i++) {
    const tabs=await chrome.tabs.query({});
    const found=tabs.find(t=>isGeminiChat(t.url) && !before.has(t.id) && t.openerTabId===notebookTab);
    if (found?.id) return {id:found.id,video_id:source.video_id||'',youtube:source.youtube||'',url:found.url};
    await sleep(500);
  }
  throw new Error('notebooklm_gemini_tab_not_opened');
}

$('rebuild').onclick=async()=>{
  if (running) return;
  const choice=selectedRun();
  if (!choice?.run) { status('请先读取并选择目标频道的历史 run。'); return; }
  if (!pairing) {
    try {
      status('正在为重建映射启动本机服务…');
      const result=await native('start',{run:choice.run,resume:!choice.live,fresh:true});
      if (!result?.ok) throw new Error(result?.error || '本机组件未安装');
      await bindPairing(result.pairing,choice,true);
    } catch (error) {
      status(`重建映射启动失败：${String(error?.message || error).slice(0,180)}。`);
      return;
    }
  }
  if (!notebookUrl) { status('缺少登记笔记本信息，请重新点「启动本机服务」。'); return; }
  workTab=undefined;
  running=true; stopping=false;
  startHeartbeat();
  ['rebuild','preview','one','start'].forEach(id=>$(id).disabled=true);
  $('stop').disabled=false;
  let recorded=0, cumulative=0;
  try {
    status('第 1 步：打开登记笔记本，先确认频道…');
    await gotoLight(notebookUrl);
    const verified=await page('verify-notebook',{notebook:notebookUrl,channel_name:channelName,account:accountName}) || {};
    const verifiedName=verified.name || channelName || '未命名频道';
    status(`已确认频道：${verifiedName}。正在读取该频道内的近期对话…`);
    await slowSleep(rand(3000,6000));
    const mapped=await api('/collect',{items:[]});
    cumulative=mapped.report?.total||0;
    const doneChats=new Set(mapped.mapped_chats||[]);
    const unmappedVideoIds=new Set(mapped.unmapped_video_ids||[]);
    const databaseVideoIds=new Set((mapped.database_candidates||[]).map(row=>row.video_id));
    const extraSources=[];
    status(`数据库中有 ${unmappedVideoIds.size} 条历史视频缺少映射，正在核对 NotebookLM 全部来源…`);
    await gotoNotebookLm();
    const sourceList=await notebookPage('list-notebooklm-sources',{}) || {};
    const sources=sourceList.sources||[];
    if (!sources.length) throw new Error('notebooklm_sources_empty');
    const listed={chats:sources};
    // Titles are not unique in Notebook. After a database rollback, old
    // title-based filtering can skip a different chat with the same title.
    // Re-read every visible chat instance; the bridge deduplicates by actual
    // chat URL and video ID, without sending another Gemini prompt.
    const pending=(listed.chats||[]).filter(c=>!(c.chat && doneChats.has(c.chat)));
    const chats=listed.chats||[];
    if (!chats.length) throw new Error('no_unmapped_chats_in_channel');
    status(`频道「${verifiedName}」可见 ${chats.length} 条来源，正在逐条核对视频 ID；待写入 ${unmappedVideoIds.size} 条。每 ${COOLDOWN_EVERY} 条冷却 40 秒。`);
    let batch=[];
    const flush=async()=>{
      if (!batch.length) return;
      const result=await api('/collect',{items:batch});
      recorded+=result.recorded||0;
      // The displayed total must never move backwards when an interrupted
      // collect response is stale or belongs to an earlier bridge snapshot.
      cumulative=Math.max(cumulative, result.report?.total ?? 0);
      batch=[];
    };
    for (let i=0;i<chats.length;i++) {
      if (!chats.length) break;
      checkStop();
      const chat=chats[i];
      let chatUrl, chatTab;
      if (i>0) {
        await chrome.tabs.reload(notebookTab);
        await notebookPage('list-notebooklm-sources',{});
      }
      const opened=await openNotebookSource(chat.title,chat.occurrence,unmappedVideoIds);
      chatTab=opened.id;
      chatUrl=opened.url;
      await slowSleep(rand(2000,4000));
      // NotebookLM already exposed the original User: YouTube URL. Avoid
      // waiting for the Gemini chat to render during mapping recovery.
      let videoId=opened.video_id || null;
      // Some NotebookLM source cards render the body lazily. Fall back to
      // the corresponding Gemini chat only for those cards.
      if (!videoId) {
        workTab=chatTab;
        await waitForChat();
        for (let k=0;k<12 && !videoId;k++) {
          const read=await page('read-video',{});
          if (read.video_id) videoId=read.video_id; else await sleep(1200);
        }
      }
      // Only rebuild videos that are actually missing a DB chat mapping.
      // Chats already mapped in the database are never written again.
      if (videoId && unmappedVideoIds.has(videoId)) {
        batch.push({chat:chatUrl,video_id:videoId,old_title:chat.title});
        await flush();
        unmappedVideoIds.delete(videoId);
      }
      if (videoId && !databaseVideoIds.has(videoId)) extraSources.push({video_id:videoId,title:chat.title});
      if (chatTab) {
        await chrome.tabs.remove(chatTab).catch(()=>{});
        workTab=undefined;
      }
      status(`频道内 ${i+1}/${chats.length}，本轮新增 ${recorded} 条、名单累计 ${cumulative+batch.length} 条；慢速进行中…`);
      try { await api('/heartbeat'); } catch { /* timer heartbeat also renews */ }
      if ((i+1)%COOLDOWN_EVERY===0) {
        await flush();
        status(`已处理 ${i+1} 条，冷却 40 秒以避免风控…`);
        await slowSleep(COOLDOWN_MS);
      } else {
        await slowSleep(rand(8000,15000));
      }
    }
    await flush();
    const extraText=extraSources.length
      ? `发现 ${extraSources.length} 条 NotebookLM 来源不在频道数据库：${extraSources.map(x=>`${x.title} [${x.video_id}]`).join('；').slice(0,500)}。`
      : 'NotebookLM 来源均能在频道数据库中找到。';
    status(`本次新增 ${recorded} 条，名单累计 ${cumulative} 条。${extraText}已完成全量来源核对；之后点「只检查一条」→「改名一条」→「开始批量改名」。`);
  } catch (error) {
    const msg=String(error?.message||error);
    if (stopping) {
      status(`已停止，保留已写回的 ${recorded} 条。可稍后重新点「重建映射」继续，已取消条数上限。`);
    } else if (msg.includes('rate_limited') || msg.includes('interrupted')) {
      status('检测到 Google 异常流量验证页或页面被拦截，已立即停止。请在该浏览器里手动完成验证，并冷却 10~30 分钟后再点「重建映射」；已写回的条目会保留。');
    } else {
      status(`重建映射中断：${msg.slice(0,160)}。本次新增 ${recorded} 条，名单累计 ${cumulative} 条；已写回的不会丢，可直接点「只检查一条」开始改名，或稍后继续重建映射。`);
    }
  } finally {
    stopHeartbeat();
    running=false;
    ['rebuild','preview','one','start'].forEach(id=>$(id).disabled=false);
    $('stop').disabled=true;
  }
};

