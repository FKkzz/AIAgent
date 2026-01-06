import { getString } from "../utils/locale";

// Zotero and ztoolkit are available as global variables in Zotero plugins
declare const ztoolkit: any;
declare const Zotero: any;
declare const addon: any;

export class PDFAnalyzer {
  // Track processing status to prevent duplicate requests
  private static processingItems = new Set<string>();

  private static get agentUrl(): string {
    const { config } = addon.data;
    const prefix = config.prefsPrefix || `extensions.zotero.${config.addonRef}`;
    return Zotero.Prefs.get(`${prefix}.apiUrl`) as string || "https://127.0.0.1:3333";
  }

  private static get enableSummary(): boolean {
    const { config } = addon.data;
    const prefix = config.prefsPrefix || `extensions.zotero.${config.addonRef}`;
    return !!Zotero.Prefs.get(`${prefix}.enableSummary`);
  }

  private static get enableKeywords(): boolean {
    const { config } = addon.data;
    const prefix = config.prefsPrefix || `extensions.zotero.${config.addonRef}`;
    return !!Zotero.Prefs.get(`${prefix}.enableKeywords`);
  }

  private static get enableBoth(): boolean {
    const { config } = addon.data;
    const prefix = config.prefsPrefix || `extensions.zotero.${config.addonRef}`;
    return !!Zotero.Prefs.get(`${prefix}.enableBoth`);
  }

  static async analyzePDF(pdfPath: string, existingTags: string[]): Promise<{ summary: string; keywords: string[] } | null> {
    // First check agent availability
    try {
      // Check if the URL is HTTPS
      if (this.agentUrl.indexOf('https://') !== 0) {
        ztoolkit.log("Warning: Using non-HTTPS URL. For production, use HTTPS for security.");
      }
      
      try {
        const ping = await Zotero.HTTP.request("GET", `${this.agentUrl}/ping`, { responseType: "json", timeout: 5000 });
        if (ping.status !== 200) {
          ztoolkit.log(`Agent ping failed: ${ping.status}`);
          return null;
        }
      } catch (e) {
        ztoolkit.log("Agent not reachable", e);
        return null;
      }

      // Determine the processing mode based on preferences
      let mode = "both";
      if (this.enableSummary && !this.enableKeywords && !this.enableBoth) {
        mode = "summary";
      } else if (!this.enableSummary && this.enableKeywords && !this.enableBoth) {
        mode = "keywords";
      } else if (this.enableBoth && !this.enableSummary && !this.enableKeywords) {
        mode = "both";
      } else {
        // If multiple options are selected or none, default to both
        mode = "both";
      }

      const response = await Zotero.HTTP.request(
        "POST",
        `${this.agentUrl}/process`,
        {
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            pdf_path: pdfPath,
            existing_tags: existingTags,
            mode: mode,
          }),
          responseType: "json",
          timeout: 30000, // Increased timeout for larger PDFs
        }
      );

      if (response.status !== 200) {
        ztoolkit.log(`PDF analysis failed: ${response.status}`);
        return null;
      }

      const data = response.response as { summary: string; keywords: string[] };
      return data;
    } catch (error) {
      ztoolkit.log(`Error analyzing PDF: ${error}`);
      // Check if it's a certificate error or network error
      if (error instanceof Error) {
        if (error.message.indexOf("SSL") !== -1 || error.message.indexOf("certificate") !== -1) {
          ztoolkit.log("SSL/TLS connection error. Please ensure your HTTPS certificate is valid or consider using proper certificate configuration.");
        }
      }
      return null;
    }
  }

  static async processPDFItem(item: any) {
    if (!item.isAttachment() || item.attachmentContentType !== "application/pdf") {
      return;
    }

    const pdfPath = item.getFilePath();
    if (!pdfPath) {
      ztoolkit.log("No PDF path found");
      return;
    }

    // Check if this item is already being processed
    if (this.processingItems.has(pdfPath)) {
      ztoolkit.log("Item is already being processed:", pdfPath);
      return;
    }

    // Add to processing set
    this.processingItems.add(pdfPath);

    const parentItem = item.parentItem;
    if (!parentItem) {
      ztoolkit.log("No parent item found");
      // Remove from processing set before returning
      this.processingItems.delete(pdfPath);
      return;
    }

    // Get existing tags from the library
    const libraryID = item.libraryID;
    const allTags = await Zotero.Tags.getAll(libraryID);
    const existingTags = allTags.map((tag: any) => tag.name);

    // Show progress window while calling the agent
    let progressWin: any = null;
    try {
      progressWin = new ztoolkit.ProgressWindow(addon.data.config.addonName, {
        closeOnClick: true,
        closeTime: -1,
      })
        .createLine({
          text: `${addon.data.config.addonName}: ${getString("ai-processing" as any)}`,
          type: "default",
          progress: 0,
        })
        .show();

      const result = await this.analyzePDF(pdfPath, existingTags);
      if (!result) {
        progressWin.changeLine({ text: getString("ai-processing-failed" as any), type: "danger", progress: 100 });
        progressWin.startCloseTimer(3000);
        return;
      }

      // Add summary as note to parent item if enabled
      if (this.enableSummary || this.enableBoth) {
        const noteItem = new Zotero.Item("note");
        noteItem.libraryID = libraryID;
        noteItem.parentID = parentItem.id;
        noteItem.setNote(`<p><strong>AI Summary</strong></p><p>${result.summary}</p>`);
        await noteItem.saveTx();
      }

      // Add keywords as tags to parent item if enabled
      if (this.enableKeywords || this.enableBoth) {
        for (const keyword of result.keywords) {
          if (existingTags.indexOf(keyword) === -1) {
            parentItem.addTag(keyword);
          }
        }
        await parentItem.saveTx();
      }

      progressWin.changeLine({ text: getString("ai-processing-complete" as any), type: "success", progress: 100 });
      progressWin.startCloseTimer(2500);
      ztoolkit.log("PDF analysis completed and added to item");
    } catch (err) {
      ztoolkit.log("Error in processPDFItem", err);
      if (progressWin) {
        try {
          progressWin.changeLine({ text: getString("ai-processing-error" as any), type: "danger", progress: 100 });
          progressWin.startCloseTimer(3000);
        } catch (e) {
          // ignore
        }
      }
    } finally {
      // Remove from processing set when complete
      this.processingItems.delete(pdfPath);
    }
  }
}
