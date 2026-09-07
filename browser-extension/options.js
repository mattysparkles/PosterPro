const DEFAULT_SETTINGS = {
  posterproBaseUrl: "https://posterpro.sparkleserver.site",
  defaultMarketplace: "mercari",
};

const el = (id) => document.getElementById(id);

async function load() {
  const settings = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  el("posterproBaseUrl").value = settings.posterproBaseUrl || DEFAULT_SETTINGS.posterproBaseUrl;
  el("defaultMarketplace").value = settings.defaultMarketplace || DEFAULT_SETTINGS.defaultMarketplace;
}

async function save() {
  await chrome.storage.sync.set({
    posterproBaseUrl: el("posterproBaseUrl").value.trim() || DEFAULT_SETTINGS.posterproBaseUrl,
    defaultMarketplace: el("defaultMarketplace").value.trim().toLowerCase() || DEFAULT_SETTINGS.defaultMarketplace,
  });
  el("status").textContent = "Settings saved.";
}

document.addEventListener("DOMContentLoaded", async () => {
  await load();
  el("saveButton").addEventListener("click", async () => {
    try {
      await save();
    } catch (error) {
      el("status").textContent = error.message || String(error);
    }
  });
});
