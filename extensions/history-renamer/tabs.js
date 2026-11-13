export async function openOrReuseTab(tabs, url, tabId) {
  if (tabId !== undefined && tabId !== null) {
    try {
      await tabs.get(tabId);
      let lastError;
      for (let attempt=0; attempt<4; attempt++) {
        try {
          await tabs.update(tabId, {url, active:true});
          return tabId;
        } catch (error) {
          lastError=error;
          if (!/Tabs cannot be edited right now|dragging a tab/i.test(String(error?.message || error))) throw error;
          await new Promise(resolve => setTimeout(resolve, 350*(attempt+1)));
        }
      }
      throw lastError;
    } catch (error) {
      if (!/No tab with id/i.test(String(error?.message || error))) throw error;
    }
  }

  const created = await tabs.create({url, active:true});
  if (!Number.isInteger(created?.id)) throw new Error('work_tab_create_failed');
  return created.id;
}
