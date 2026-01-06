import {
  BasicExampleFactory,
} from "./modules/examples";
import { PDFAnalyzer } from "./modules/pdfAnalyzer";
import { getString, initLocale } from "./utils/locale";
import { registerPrefsScripts } from "./modules/preferenceScript";
import { setDefaultPrefSettings } from "./modules/defaultPrefs";
import { createZToolkit } from "./utils/ztoolkit";

// Declare global variables
declare const ztoolkit: any;
declare const Zotero: any;
declare const addon: any;

async function onStartup() {
  await Promise.all([
    Zotero.initializationPromise,
    Zotero.unlockPromise,
    Zotero.uiReadyPromise,
  ]);

  initLocale();

  // Initialize default preferences during startup
  setDefaultPrefSettings();

  BasicExampleFactory.registerPrefs();

  BasicExampleFactory.registerNotifier();

  await Promise.all(
    Zotero.getMainWindows().map((win: any) => onMainWindowLoad(win)),
  );

  // Mark initialized as true to confirm plugin loading status
  // outside of the plugin (e.g. scaffold testing process)
  addon.data.initialized = true;
}

async function onMainWindowLoad(win: any): Promise<void> {
  // Create ztoolkit for every window
  addon.data.ztoolkit = createZToolkit();

  win.MozXULElement.insertFTLIfNeeded(
    `${addon.data.config.addonRef}-mainWindow.ftl`,
  );
  
  // Register right-click menu item for existing items
  registerRightClickMenuItem();
}

async function onMainWindowUnload(win: Window): Promise<void> {
  ztoolkit.unregisterAll();
  addon.data.dialog?.window?.close();
}

function onShutdown(): void {
  ztoolkit.unregisterAll();
  addon.data.dialog?.window?.close();
  // Remove addon object
  addon.data.alive = false;
  delete Zotero[addon.data.config.addonInstance];
}

/**
 * Register right-click menu item for existing items
 */
async function registerRightClickMenuItem() {
  // Create menu item for right-click context menu
  ztoolkit.Menu.register("item", {
    tag: "menuitem",
    id: "zotero-itemmenu-pdf-analyze",
    label: getString("ai-analyze-pdf" as any),
    commandListener: (event: Event) => {
      // Get selected items using Zotero.getActiveZoteroPane()
      const activeZoteroPane = Zotero.getActiveZoteroPane();
      if (!activeZoteroPane) {
        ztoolkit.log("No active Zotero pane found");
        return;
      }
      
      // Get selected items from the active pane
      const selectedItems = activeZoteroPane.getSelectedItems();
      if (!selectedItems || selectedItems.length === 0) {
        ztoolkit.log("No items selected");
        return;
      }

      // Process each selected item
      for (const item of selectedItems) {
        // If it's a PDF attachment, process it directly
        if (item.isAttachment() && item.attachmentContentType === "application/pdf") {
          PDFAnalyzer.processPDFItem(item);
        } 
        // If it's a parent item, find its PDF attachments and process them
        else if (!item.isAttachment()) {
          const attachments = item.getAttachments(true);
          for (const attachmentId of attachments) {
            const attachment = Zotero.Items.get(attachmentId);
            if (attachment && attachment.attachmentContentType === "application/pdf") {
              PDFAnalyzer.processPDFItem(attachment);
            }
          }
        }
      }
    },
  });
}

/**
 * This function is just an example of dispatcher for Notify events.
 * Any operations should be placed in a function to keep this funcion clear.
 */
async function onNotify(
  event: string,
  type: string,
  ids: Array<string | number>,
  extraData: { [key: string]: any },
) {
  // You can add your code to the corresponding notify type
  ztoolkit.log("notify", event, type, ids, extraData);
  if (
    event == "select" &&
    type == "tab" &&
    extraData[ids[0]].type == "reader"
  ) {
    BasicExampleFactory.exampleNotifierCallback();
  } else if ((event === "add" || event === "modify") && (type === "item" || type === "collection-item")) {
    // Handle new item added or modified - specifically look for PDF attachments
    (async () => {
      try {
        const items = await Zotero.Items.getAsync(ids as number[]);
        for (const item of items) {
          if (item && item.isAttachment() && item.attachmentContentType === "application/pdf") {
            // Only process if it's a PDF attachment
            ztoolkit.log("Processing new or modified PDF attachment:", item.getFilePath());
            await PDFAnalyzer.processPDFItem(item);
          }
        }
      } catch (error) {
        ztoolkit.log("Error in onNotify when processing new or modified PDFs:", error);
      }
    })();
  } else if (event === "add" && type === "file") {
    // Handle file events which might occur during drag-and-drop
    (async () => {
      try {
        const items = await Zotero.Items.getAsync(ids as number[]);
        for (const item of items) {
          if (item && item.isAttachment() && item.attachmentContentType === "application/pdf") {
            ztoolkit.log("Processing PDF file event:", item.getFilePath());
            await PDFAnalyzer.processPDFItem(item);
          }
        }
      } catch (error) {
        ztoolkit.log("Error in onNotify when processing PDF file events:", error);
      }
    })();
  } else {
    return;
  }
}

/**
 * This function is called when the preferences window is loaded
 */
async function onPrefsLoad(event: Event) {
  registerPrefsScripts((event.target as any).ownerGlobal);
}

// Add your hooks here. For element click, etc.
// Keep in mind hooks only do dispatch. Don't add code that does real jobs in hooks.
// Otherwise the code would be hard to read and maintain.

export default {
  onStartup,
  onShutdown,
  onMainWindowLoad,
  onMainWindowUnload,
  onNotify,
  onPrefsLoad,
};
