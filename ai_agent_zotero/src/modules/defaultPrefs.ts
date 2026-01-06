import { config } from "../../package.json";

export function setDefaultPrefSettings() {
  try {
    const prefix = config.prefsPrefix || `extensions.zotero.${config.addonRef}`;
    
    // Set default values if they don't exist
    if (Zotero.Prefs.get(`${prefix}.enableSummary`) == null) {
      Zotero.Prefs.set(`${prefix}.enableSummary`, true);
      ztoolkit.log("Set default: enableSummary = true");
    }
    if (Zotero.Prefs.get(`${prefix}.enableKeywords`) == null) {
      Zotero.Prefs.set(`${prefix}.enableKeywords`, true);
      ztoolkit.log("Set default: enableKeywords = true");
    }
    if (Zotero.Prefs.get(`${prefix}.enableBoth`) == null) {
      Zotero.Prefs.set(`${prefix}.enableBoth`, false);
      ztoolkit.log("Set default: enableBoth = false");
    }
    if (!Zotero.Prefs.get(`${prefix}.apiUrl`) || Zotero.Prefs.get(`${prefix}.apiUrl`) === "") {
      Zotero.Prefs.set(`${prefix}.apiUrl`, "https://127.0.0.1:3333");
      ztoolkit.log("Set default: apiUrl = https://127.0.0.1:3333");
    }
  } catch (e) {
    ztoolkit.log("Error setting default prefs", e);
  }
}
