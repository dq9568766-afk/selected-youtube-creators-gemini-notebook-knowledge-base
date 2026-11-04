import {test} from 'node:test';
import assert from 'node:assert/strict';
import {pageAction} from '../extensions/history-renamer/page.js';

function fixture() {
  const state={menu:false,dialog:false,title:'自动标题',saved:0};
  const el=(text='',attrs={},query=()=>[])=>({textContent:text,getAttribute:key=>attrs[key]??null,getClientRects:()=>[{}],querySelectorAll:query,click(){}});
  class Input {
    constructor(){this._value=state.title;}
    get value(){return this._value;}
    set value(v){this._value=v;}
    getClientRects(){return [{}];}
    dispatchEvent(){}
  }
  const field=new Input();
  const cancel=el('取消'); cancel.click=()=>{state.dialog=false;};
  const save=el('重命名'); save.click=()=>{state.title=field.value;state.saved++;state.dialog=false;};
  const dialog=el('',{},selector=>selector.startsWith('input')?[field]:selector.startsWith('button')?[cancel,save]:[]);
  dialog.getClientRects=()=>state.dialog?[{}]:[];
  const menu=el('重命名'); menu.click=()=>{state.menu=false;state.dialog=true;field.value=state.title;};
  const open=el('',{'aria-label':'打开对话操作菜单。'}); open.click=()=>{state.menu=true;};
  const account=el('',{'aria-label':'Google 账号：测试 (fixture@example.test)'});
  const query=el('https://www.youtube.com/watch?v=abcdefghijk\n总结');
  const reply=el('正文'.repeat(80));
  globalThis.HTMLInputElement=Input;
  globalThis.location={origin:'https://gemini.google.com',pathname:'/app/'+'1'.repeat(16),href:'https://gemini.google.com/app/'+'1'.repeat(16)};
  globalThis.document={querySelectorAll(selector){
    if(selector==='[aria-label]')return [account,open];
    if(selector==='[role="alert"],[role="dialog"]')return state.dialog?[dialog]:[];
    if(selector==='.query-text')return [query];
    if(selector==='message-content')return [reply];
    if(selector==='[role="menuitem"]')return state.menu?[menu]:[];
    if(selector==='[role="dialog"]')return state.dialog?[dialog]:[];
    if(selector.startsWith('button'))return [open,...state.menu?[menu]:[]];
    return [];
  }};
  globalThis.fetch=()=>{throw new Error('Unexpected network access');};
  const job={account:'fixture@example.test',chat:location.href,video_id:'abcdefghijk',title:'20261026-主题-完整原标题',old_chat_title:state.title};
  return {state,job,query,reply};
}

test('chat guard checks exact account, original video and substantive response without exporting body',async()=>{
  const {job,query,reply}=fixture();
  const result=await pageAction('chat-check',job);
  assert.deepEqual(Object.keys(result).sort(),['account','summary_present','video_checked']);
  await assert.rejects(pageAction('chat-check',{...job,account:'other@example.test'}),/account_mismatch/);
  query.textContent='https://www.youtube.com/watch?v=other';
  await assert.rejects(pageAction('chat-check',job),/video_mismatch/);
  query.textContent=job.video_id;reply.textContent='short';
  await assert.rejects(pageAction('chat-check',job),/summary_unconfirmed/);
});

test('chat flow accepts identity verified on Notebook when chat page omits account control',async()=>{
  const {job}=fixture();
  // The current Gemini chat header has no Google account aria-label. The
  // identity was checked on the Notebook page earlier in this same flow.
  globalThis.document.querySelectorAll=selector=>{
    if(selector==='[aria-label]')return [];
    if(selector==='.query-text')return [{textContent:`https://www.youtube.com/watch?v=${job.video_id}`,getClientRects:()=>[{}]}];
    if(selector==='message-content')return [{textContent:'完整总结正文'.repeat(40),getClientRects:()=>[{}]}];
    if(selector==='button[aria-label]')return [];
    return [];
  };
  const result=await pageAction('chat-check',{...job,identity_checked:true});
  assert.equal(result.video_checked,true);
  assert.equal(result.summary_present,true);
});

test('chat evidence is found when Gemini renders messages in open shadow roots',async()=>{
  const {job}=fixture();
  const inner=(text)=>({textContent:text,getClientRects:()=>[{}],querySelectorAll:()=>[]});
  const queryText=inner(`https://www.youtube.com/watch?v=${job.video_id} 总结`);
  const queryHost={textContent:'',getClientRects:()=>[{}],querySelectorAll:()=>[],shadowRoot:{textContent:queryText.textContent,querySelectorAll:selector=>selector==='.query-text'?[queryText]:selector==='*'?[queryText]:[]}};
  const replyHost={textContent:'',getClientRects:()=>[{}],querySelectorAll:()=>[],shadowRoot:{textContent:'完整总结正文'.repeat(40),querySelectorAll:()=>[]}};
  globalThis.document={querySelectorAll(selector){
    if(selector==='*')return [queryHost,replyHost];
    if(selector==='user-query')return [queryHost];
    if(selector==='model-response')return [replyHost];
    return [];
  }};
  const result=await pageAction('chat-check',{...job,identity_checked:true});
  assert.equal(result.video_checked,true);
  assert.equal(result.summary_present,true);
});

test('title preview cancels and rename saves the exact full title then reads it back',async()=>{
  const {job,state}=fixture();
  assert.equal((await pageAction('chat-title',job)).title,'自动标题');
  assert.equal(state.saved,0);
  await pageAction('chat-rename',job);
  assert.equal(state.saved,1);
  assert.equal((await pageAction('chat-title',job)).title,job.title);
});

test('unexpected current title prevents saving, NotebookLM origin cannot pass as Gemini',async()=>{
  const {job,state}=fixture();
  state.title='人工刚修改的标题';
  await assert.rejects(pageAction('chat-rename',job),/save_unverified/);
  assert.equal(state.saved,0);
  globalThis.location.origin='https://notebooklm.google.com';
  await assert.rejects(pageAction('chat-check',job),/notebook_mismatch/);
});
