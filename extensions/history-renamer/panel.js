import {pageAction} from './page.js';
import {openOrReuseTab} from './tabs.js';
import {runWithRecovery} from './recovery.js';

const $ = id => document.getElementById(id);
let pairing, running = false, stopping = false, workTab;
let heartbeatTimer;
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
const status = text => { $('status').textContent = text; };
function checkStop() { if (stopping) throw new Error('interrupted'); }

async function api(path, body = {}) {
  if (!pairing) throw new Error('请先选择配对文件');
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
  heartbeatTimer=setInterval(async()=>{
    if (!running || !pairing) return;
    try { await api('/heartbeat'); }
    catch { stopping=true; status('本机服务连接中断，已停止并保留未完成项目。'); }
  },30000);
}
function stopHeartbeat() {
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer=undefined; }
}
function native(action) {
  return new Promise(resolve => chrome.runtime.sendMessage({type:`native-${action}`}, resolve));
}
async function bindPairing(value) {
  pairing=value;
  const job=await api('/next');
  $('mapping').textContent=job.done ? '本机服务：已启动并已连接\n本批次已处理完成' : `本机服务：已启动并已连接\n账号：${job.account}\nGemini：${job.notebook}`;
  status('本机服务已启动并已连接。首次使用请先检查一条。');
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
  const current = await chrome.tabs.get(workTab);
  const url = new URL(current.url);
  if (!['gemini.google.com'].includes(url.hostname)) throw new Error('interrupted');
  const results = await chrome.scripting.executeScript({target:{tabId:workTab},func:pageAction,args:[action,job]});
  if (results[0]?.result === undefined) throw new Error('ui_changed');
  return results[0].result;
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

$('pairing').onchange = async () => {
  if (running) return;
  try {
    const value=JSON.parse(await $('pairing').files[0].text());
    if (!/^http:\/\/127\.0\.0\.1:\d{4,5}$/.test(value.bridge) || !/^[\w-]{40,64}$/.test(value.pairing)) throw new Error('配对文件格式不正确');
    await bindPairing(value);
  } catch (error) {
    pairing=null;
    $('mapping').textContent='本机服务：未连接';
    status(`未连接本机服务：${String(error?.message || error || '未知错误').slice(0,160)}。请重新启动服务并选择新配对文件。`);
  }
};

$('nativeStart').onclick=async()=>{
  try {
    status('正在启动本机服务…');
    const result=await native('start');
    if (!result?.ok) throw new Error(result?.error || '本机组件未安装');
    await bindPairing(result.pairing);
  } catch (error) {
    $('mapping').textContent='本机服务：未连接';
    status(`启动失败：${String(error?.message || error || '未知错误').slice(0,160)}。首次使用请运行 install_native_host.cmd。`);
  }
};
$('nativeStop').onclick=async()=>{
  const result=await native('stop');
  pairing=null;
  $('mapping').textContent='本机服务：未连接';
  status(result?.ok ? '本机服务已停止。' : `停止失败：${result?.error || '未知错误'}`);
};

const reasons = new Set(['ui_changed','account_mismatch','notebook_mismatch','video_mismatch','summary_unconfirmed','save_unverified','interrupted']);
const messages = {ui_changed:'页面结构不匹配，需要适配',account_mismatch:'账号未匹配或尚未加载',notebook_mismatch:'笔记本或聊天归属未核实',video_mismatch:'原视频链接未匹配',summary_unconfirmed:'总结正文或生成状态未确认',save_unverified:'保存后的名称未核实',interrupted:'已停止，保留已登记进度',bridge_disconnected:'本机改名服务连接中断',bridge_rejected:'本机服务拒绝领取或继续操作',bridge_invalid_response:'本机服务返回了无效响应'};

async function run(preview,one) {
  if (running) return;
  if (!$('confirmed').checked) { status('请先核对账号、笔记本并勾选确认。'); return; }
  running=true; stopping=false;
  startHeartbeat();
  ['preview','one','start','pairing'].forEach(id=>$(id).disabled=true);
  $('stop').disabled=false;
  let job,kind='chat',completed=0,phase='领取名单';
  try {
    for (;;) {
      checkStop();
      phase='领取名单';
      job=await api('/next');
      if (job.done) { await api('/release'); pairing=null; status('批次已完成，本机服务已退出并释放租约。可按项目规程恢复内置浏览器。'); break; }
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
    const reason = reasons.has(error?.message) ? error.message : (serviceReason || 'ui_changed');
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
      try { await api('/release'); pairing=null; } catch { /* Queue lease expires if the local service is already gone. */ }
    }
  } finally {
    stopHeartbeat();
    running=false;
    ['preview','one','start','pairing'].forEach(id=>$(id).disabled=false);
    $('stop').disabled=true;
  }
}
$('preview').onclick=()=>run(true,true);
$('one').onclick=()=>run(false,true);
$('start').onclick=()=>run(false,false);
$('stop').onclick=()=>{stopping=true;status('正在停止，将保留已登记结果。');};
