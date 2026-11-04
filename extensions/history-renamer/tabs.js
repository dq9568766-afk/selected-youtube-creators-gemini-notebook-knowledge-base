export async function openOrReuseTab(tabs, url, tabId) {
  if (tabId !== undefined && tabId !== null) {
    try {
      await tabs.get(tabId);
      await tabs.update(tabId, {url, active:true});
      return tabId;
    } catch (error) {
      if (!/No tab with id/i.test(String(error?.message || error))) throw error;
    }
  }

  const created = await tabs.create({url, active:true});
  if (!Number.isInteger(created?.id)) throw new Error('work_tab_create_failed');
  return created.id;
}
