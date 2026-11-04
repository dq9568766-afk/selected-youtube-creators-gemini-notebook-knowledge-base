import {pageAction} from './page.js';
import {openOrReuseTab} from './tabs.js';
import {runWithRecovery} from './recovery.js';

const $ = id => document.getElementById(id);
let pairing, running = false, stopping = false, workTab;
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
const status = text => { $('status').textContent = text; };
function checkStop() { if (stopping) throw new Error('interrupted'); }

async function api(path, body = {}) {
  if (!pairing) throw new Error('请先选择配对文件');
  const response = await fetch(pairing.bridge + path, {
    method:'POST', headers:{'Content-Type':'application/json','X-YTG-Pairing':pairing.pairing},
    body:JSON.stringify(body), signal:AbortSignal.timeout(15000), cache:'no-store'
  });
  const result = await response.json();
  if (!response.ok || result.stopped) throw new Error(result.stopped || '本机服务拒绝操作');
  return result;
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
    pairing=value;
    const job=await api('/next');
    $('mapping').textContent=job.done ? '本批次已处理完成' : `账号：${job.account}\nGemini：${job.notebook}`;
    status('配对完成。首次使用请先检查一条。');
  } catch { pairing=null; status('无法配对，请确认本机服务正在运行并选择它生成的配对文件。'); }
};

const reasons = new Set(['ui_changed','account_mismatch','notebook_mismatch','video_mismatch','summary_unconfirmed','save_unverified','interrupted']);
const messages = {ui_changed:'页面结构不匹配，需要适配',account_mismatch:'账号未匹配或尚未加载',notebook_mismatch:'笔记本或聊天归属未核实',video_mismatch:'原视频链接未匹配',summary_unconfirmed:'总结正文或生成状态未确认',save_unverified:'保存后的名称未核实',interrupted:'已停止，保留已登记进度'};

async function run(preview,one) {
  if (running) return;
  if (!$('confirmed').checked) { status('请先核对账号、笔记本并勾选确认。'); return; }
  running=true; stopping=false;
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
    const reason = reasons.has(error.message) ? error.message : 'ui_changed';
    if (job && !job.done && !preview) {
      try {
        await api('/result',{video_id:job.video_id,title:job.title,kind,status:'pending',reason,
          recovery_attempts:Number.isInteger(error?.recovery_attempts) ? error.recovery_attempts : 0,
          verification_phase:phase});
      } catch { /* Keep pending if the bridge disconnected. */ }
    }
    const detail = String(error?.message || '').replace(/[^a-zA-Z0-9_.:-]/g,' ').slice(0,80) || reason;
    status(`${messages[reason]}（步骤：${phase}；代码：${detail}）。未完成项目保留待处理。`);
    if (stopping && pairing) {
      try { await api('/release'); pairing=null; } catch { /* Queue lease expires if the local service is already gone. */ }
    }
  } finally {
    running=false;
    ['preview','one','start','pairing'].forEach(id=>$(id).disabled=false);
    $('stop').disabled=true;
  }
}
$('preview').onclick=()=>run(true,true);
$('one').onclick=()=>run(false,true);
$('start').onclick=()=>run(false,false);
$('stop').onclick=()=>{stopping=true;status('正在停止，将保留已登记结果。');};
