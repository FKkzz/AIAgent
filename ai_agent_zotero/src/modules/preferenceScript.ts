import { config } from "../../package.json";
import { getString } from "../utils/locale";

export async function registerPrefsScripts(_window: Window) {
  // This function is called when the prefs window is opened
  // See addon/content/preferences.xhtml onpaneload
  // initialize prefs holder
  if (!addon.data.prefs) {
    addon.data.prefs = { window: _window, columns: [], rows: [] } as any;
  } else {
    addon.data.prefs.window = _window;
  }


  // No complex UI to render; just bind events for simple controls
  bindPrefEvents();
}

async function updatePrefsUI() {
  // No dynamic rendering required for simplified prefs page
  return;
}

function bindPrefEvents() {
  const doc = addon.data.prefs!.window.document;
  if (!doc) return;
  const prefix = config.prefsPrefix || `extensions.zotero.${config.addonRef}`;

  const cbSummary = doc.querySelector(
    `#zotero-prefpane-${config.addonRef}-enable-summary`,
  ) as XUL.Checkbox | null;
  const cbKeywords = doc.querySelector(
    `#zotero-prefpane-${config.addonRef}-enable-keywords`,
  ) as XUL.Checkbox | null;
  const cbBoth = doc.querySelector(
    `#zotero-prefpane-${config.addonRef}-enable-both`,
  ) as XUL.Checkbox | null;
  const apiInput = doc.querySelector(
    `#zotero-prefpane-${config.addonRef}-api-url`,
  ) as HTMLInputElement | null;

  // Initialize UI from prefs
  try {
    if (cbSummary) cbSummary.checked = !!Zotero.Prefs.get(`${prefix}.enableSummary`);
    if (cbKeywords) cbKeywords.checked = !!Zotero.Prefs.get(`${prefix}.enableKeywords`);
    if (cbBoth) cbBoth.checked = !!Zotero.Prefs.get(`${prefix}.enableBoth`);
    if (apiInput) apiInput.value = Zotero.Prefs.get(`${prefix}.apiUrl`) as string || "https://127.0.0.1:3333";
  } catch (e) {
    ztoolkit.log("Error initializing prefs UI", e);
  }

  cbSummary?.addEventListener("command", (e: Event) => {
    Zotero.Prefs.set(`${prefix}.enableSummary`, (e.target as XUL.Checkbox).checked);
    ztoolkit.log("Pref enableSummary changed");
  });

  cbKeywords?.addEventListener("command", (e: Event) => {
    Zotero.Prefs.set(`${prefix}.enableKeywords`, (e.target as XUL.Checkbox).checked);
    ztoolkit.log("Pref enableKeywords changed");
  });

  cbBoth?.addEventListener("command", (e: Event) => {
    Zotero.Prefs.set(`${prefix}.enableBoth`, (e.target as XUL.Checkbox).checked);
    ztoolkit.log("Pref enableBoth changed");
  });

  apiInput?.addEventListener("change", (e: Event) => {
    Zotero.Prefs.set(`${prefix}.apiUrl`, (e.target as HTMLInputElement).value);
    ztoolkit.log("Pref apiUrl changed");
  });
}
