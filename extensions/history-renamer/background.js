chrome.action.onClicked.addListener(() => chrome.tabs.create({url: chrome.runtime.getURL('panel.html')}));

let nativePort;
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !message?.type?.startsWith('native-')) return;
  try {
    nativePort ||= chrome.runtime.connectNative('com.dq.history_rename');
    nativePort.onDisconnect.addListener(() => { nativePort = undefined; });
    const listener = response => {
      nativePort?.onMessage.removeListener(listener);
      sendResponse(response || {ok:false,error:'本机组件未返回结果'});
    };
    nativePort.onMessage.addListener(listener);
    nativePort.postMessage({action:message.type.slice(7)});
  } catch (error) {
    sendResponse({ok:false,error:String(error?.message || error || '本机组件未安装')});
  }
  return true;
});
