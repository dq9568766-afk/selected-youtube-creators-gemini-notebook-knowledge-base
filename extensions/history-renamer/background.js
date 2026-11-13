chrome.action.onClicked.addListener(() => chrome.tabs.create({url: chrome.runtime.getURL('panel.html')}));

let nativePort;
let pendingNative;

function getNativePort() {
  if (nativePort) return nativePort;
  nativePort = chrome.runtime.connectNative('com.dq.history_rename');
  nativePort.onDisconnect.addListener(() => {
    // Reading lastError is required by Chrome for a handled native-host
    // disconnect; otherwise it is reported as an unchecked runtime error.
    const error = chrome.runtime.lastError;
    if (error) console.warn(`Native host disconnected: ${error.message}`);
    nativePort = undefined;
    if (pendingNative) {
      const pending = pendingNative;
      pendingNative = undefined;
      pending.finish({ok:false,error:`本机组件连接已断开${error ? `：${error.message}` : ''}`});
    }
  });
  return nativePort;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Some Codex IAB builds omit sender.id for messages from extension pages.
  // The native-* prefix is the only message surface exposed by this worker.
  if (!message?.type?.startsWith('native-')) return;
  try {
    const action=message.type.slice(7);
    if (action === 'candidates' && chrome.runtime.sendNativeMessage) {
      let finished=false;
      const timer=setTimeout(() => {
        if (!finished) { finished=true; sendResponse({ok:false,error:'Native Messaging 一次性调用超时'}); }
      },15000);
      try {
        chrome.runtime.sendNativeMessage('com.dq.history_rename', {action:'candidates'}, response => {
          if (finished) return;
          finished=true;
          clearTimeout(timer);
          const error=chrome.runtime.lastError;
          sendResponse(response || {ok:false,error:error?.message || '本机组件未返回候选任务'});
        });
      } catch (error) {
        if (!finished) { finished=true; clearTimeout(timer); sendResponse({ok:false,error:String(error?.message || error)}); }
      }
      return true;
    }
    if (message.fresh && nativePort && !pendingNative) {
      nativePort.disconnect();
      nativePort = undefined;
    }
    nativePort = getNativePort();
    if (pendingNative) {
      sendResponse({ok:false,error:'已有本机请求正在处理'});
      return true;
    }
    let timer;
    const finish = response => {
      if (pendingNative?.finish !== finish) return;
      pendingNative = undefined;
      clearTimeout(timer);
      nativePort?.onMessage.removeListener(listener);
      sendResponse(response || {ok:false,error:'本机组件未返回结果'});
    };
    const listener = response => finish(response);
    pendingNative = {finish};
    nativePort.onMessage.addListener(listener);
    timer=setTimeout(() => finish({ok:false,error:'本机组件读取超时，请重新加载扩展后重试'}),15000);
    nativePort.postMessage({action,run:message.run || null});
  } catch (error) {
    pendingNative = undefined;
    sendResponse({ok:false,error:String(error?.message || error || '本机组件未安装')});
  }
  return true;
});
