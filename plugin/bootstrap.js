/* global Zotero, Services, IOUtils, PathUtils */

// Zotero 9 keeps a bootstrapped add-on's sandbox alive when the add-on is
// disabled. Keep every global declaration restart-safe and all lexical
// declarations inside this factory.
var ZoteroQuickReadPlugin = typeof ZoteroQuickReadPlugin === "object" && ZoteroQuickReadPlugin
	? ZoteroQuickReadPlugin
	: (function () {
		"use strict";

		const ADDON_ID = "zotero-quick-read@fkkzz.github.io";
		const MENU_ID = "zotero-quick-read-item-menu";
		const FTL_FILE = "zotero-quick-read.ftl";
		const PREF_ROOT = "extensions.zoteroQuickRead.";
		const DEFAULT_BACKEND = "http://127.0.0.1:23120";
		const STATE_VERSION = 1;
		const NOTIFIER_EVENTS = new Set(["add", "modify", "redraw"]);
		const POLLABLE_STATUSES = new Set([
			"waiting_fulltext",
			"queued",
			"processing",
		]);
		const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

		const runtime = {
			started: false,
			epoch: 0,
			rootURI: "",
			menuHandle: null,
			notifierID: null,
			windows: new Set(),
			state: null,
			token: "",
			tokenSource: "none",
			pendingItemIDs: new Set(),
			attachmentRetries: new Map(),
			debounceTimer: null,
			pollTimers: new Map(),
			pollFailures: new Map(),
			otherTimers: new Set(),
			ownWriteIDs: new Set(),
			applyingJobs: new Set(),
			syncPromise: null,
		};

		const fallbackStrings = {
			"zqr-menu-root": "Zotero AI Quick Read",
			"zqr-menu-generate": "Generate AI Quick Read",
			"zqr-menu-regenerate": "Regenerate",
			"zqr-menu-status": "View Status",
			"zqr-menu-settings": "Settings",
			"zqr-menu-auto": "Automatic processing",
			"zqr-dialog-title": "Zotero AI Quick Read",
			"zqr-settings-url": "Local backend URL (HTTP loopback only):",
			"zqr-settings-token": "Backend Bearer token. Leave blank to keep the current value.",
			"zqr-settings-clear-token": "Clear token saved in preferences",
			"zqr-settings-saved": "Settings saved.",
			"zqr-settings-connected": "Backend connection and authentication succeeded.",
			"zqr-settings-health-only": "The backend is reachable, but authentication failed: {error}",
			"zqr-settings-invalid-url": "Use an HTTP URL on 127.0.0.1 without a path, credentials, query, or fragment.",
			"zqr-settings-invalid-token": "The token cannot contain a line break or exceed 4096 characters.",
			"zqr-notify-submitted": "Submitted {count} quick-read task(s).",
			"zqr-notify-no-ready-pdf": "No downloaded PDF with a regular parent item was found.",
			"zqr-notify-token-missing": "No backend token was found. Open Settings or create the plugin-token file.",
			"zqr-notify-backend-error": "Local backend request failed: {error}",
			"zqr-notify-applied": "AI Quick Read was saved to “{title}”.",
			"zqr-notify-manual-edit": "The existing AI note was edited manually. It was preserved and a new note was created.",
			"zqr-notify-auto-on": "Automatic processing is on. Existing items are not scanned.",
			"zqr-notify-auto-off": "Automatic processing is off.",
			"zqr-status-title": "Zotero AI Quick Read Status",
			"zqr-status-empty": "The selected paper has no quick-read task yet.",
			"zqr-status-line": "{title}: {status}",
			"zqr-status-detail": "{message}",
			"zqr-status-waiting-fulltext": "Waiting for the PDF download",
			"zqr-status-queued": "Queued",
			"zqr-status-processing": "Generating",
			"zqr-status-completed": "Completed; waiting to be saved",
			"zqr-status-applied": "Completed and saved to Zotero",
			"zqr-status-failed": "Failed",
			"zqr-status-waiting-quota": "Waiting for quota",
			"zqr-status-reauthorization-required": "Backend reauthorization required",
			"zqr-status-cancelled": "Cancelled",
			"zqr-status-unknown": "Unknown status",
			"zqr-status-backend-unavailable": "Backend status could not be refreshed; showing cached data: {error}",
		};
		const chineseMenuFallbackStrings = {
			"zqr-menu-root": "Zotero 论文 AI 速读",
			"zqr-menu-generate": "生成 AI 速读",
			"zqr-menu-regenerate": "重新生成",
			"zqr-menu-status": "查看状态",
			"zqr-menu-settings": "设置",
		};

		function menuFallbackString(id) {
			const locale = String(Zotero.locale || Services.locale.appLocaleAsBCP47 || "").toLowerCase();
			const chinese = locale.startsWith("zh");
			if (id === "zqr-menu-auto") {
				const enabled = getBoolPref("autoProcess", false);
				return chinese
					? `自动处理：${enabled ? "开启" : "关闭"}`
					: `Automatic processing: ${enabled ? "On" : "Off"}`;
			}
			return (chinese ? chineseMenuFallbackStrings[id] : null)
				|| fallbackStrings[id]
				|| id;
		}

		function newState() {
			return {
				version: STATE_VERSION,
				jobs: {},
				applied: {},
				records: {},
			};
		}

		function getStringPref(name, defaultValue) {
			try {
				return Services.prefs.getStringPref(PREF_ROOT + name, defaultValue);
			}
			catch (error) {
				Zotero.logError(error);
				return defaultValue;
			}
		}

		function setStringPref(name, value) {
			Services.prefs.setStringPref(PREF_ROOT + name, String(value));
		}

		function getBoolPref(name, defaultValue) {
			try {
				return Services.prefs.getBoolPref(PREF_ROOT + name, defaultValue);
			}
			catch (error) {
				Zotero.logError(error);
				return defaultValue;
			}
		}

		function setBoolPref(name, value) {
			Services.prefs.setBoolPref(PREF_ROOT + name, Boolean(value));
		}

		function loadState() {
			let parsed;
			try {
				parsed = JSON.parse(getStringPref("state", ""));
			}
			catch (error) {
				Zotero.logError(error);
			}
			if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
				return newState();
			}
			return {
				version: STATE_VERSION,
				jobs: parsed.jobs && typeof parsed.jobs === "object" ? parsed.jobs : {},
				applied: parsed.applied && typeof parsed.applied === "object" ? parsed.applied : {},
				records: parsed.records && typeof parsed.records === "object" ? parsed.records : {},
			};
		}

		function pruneState() {
			const state = runtime.state;
			if (!state) {
				return;
			}
			const jobs = Object.values(state.jobs)
				.filter(job => job && typeof job.id === "string")
				.sort((a, b) => String(b.updatedAt || b.createdAt || "")
					.localeCompare(String(a.updatedAt || a.createdAt || "")));
			const keep = new Set();
			for (const job of jobs) {
				if (keep.size < 250 || !TERMINAL_STATUSES.has(job.status)) {
					keep.add(job.id);
				}
			}
			for (const jobID of Object.keys(state.jobs)) {
				if (!keep.has(jobID)) {
					delete state.jobs[jobID];
				}
			}

			const appliedEntries = Object.entries(state.applied)
				.sort((a, b) => String(b[1]?.appliedAt || "").localeCompare(String(a[1]?.appliedAt || "")));
			for (const [jobID] of appliedEntries.slice(500)) {
				delete state.applied[jobID];
			}
		}

		function persistState() {
			if (!runtime.state) {
				return;
			}
			pruneState();
			setStringPref("state", JSON.stringify(runtime.state));
		}

		function validateBackendURL(rawValue) {
			let parsed;
			try {
				parsed = new URL(String(rawValue).trim());
			}
			catch (_error) {
				throw codedError("INVALID_URL", "Invalid backend URL");
			}
			const hostname = parsed.hostname.toLowerCase();
			const isLoopback = hostname === "127.0.0.1";
			if (parsed.protocol !== "http:" || !isLoopback || parsed.username || parsed.password
					|| (parsed.pathname && parsed.pathname !== "/") || parsed.search || parsed.hash) {
				throw codedError("INVALID_URL", "Backend URL must be an HTTP loopback origin");
			}
			return parsed.origin;
		}

		function validateToken(rawValue, allowEmpty) {
			const token = String(rawValue || "").replace(/^\uFEFF/, "").trim();
			if (!token && allowEmpty) {
				return "";
			}
			if (!token || token.length > 4096 || /[\r\n]/.test(token)) {
				throw codedError("INVALID_TOKEN", "Invalid backend token");
			}
			return token;
		}

		async function resolveToken() {
			const preferenceToken = validateToken(getStringPref("apiToken", ""), true);
			if (preferenceToken) {
				runtime.token = preferenceToken;
				runtime.tokenSource = "preference";
				return preferenceToken;
			}

			try {
				const localAppData = Services.env.get("LOCALAPPDATA");
				if (localAppData) {
					const tokenPath = PathUtils.join(localAppData, "ZoteroQuickRead", "plugin-token");
					if (await IOUtils.exists(tokenPath)) {
						const fileToken = validateToken(await IOUtils.readUTF8(tokenPath), false);
						runtime.token = fileToken;
						runtime.tokenSource = "file";
						return fileToken;
					}
				}
			}
			catch (error) {
				Zotero.debug(`${ADDON_ID}: unable to read plugin-token (${safeError(error)})`, 2);
			}
			runtime.token = "";
			runtime.tokenSource = "none";
			return "";
		}

		function codedError(code, message, status) {
			const error = new Error(message);
			error.code = code;
			if (status !== undefined) {
				error.status = status;
			}
			return error;
		}

		function safeError(error) {
			let message = error && error.message ? String(error.message) : String(error || "Unknown error");
			if (runtime.token) {
				message = message.split(runtime.token).join("[redacted]");
			}
			return message.replace(/[\r\n\t]+/g, " ").replace(/\s{2,}/g, " ").trim().slice(0, 400);
		}

		function responseJSON(xhr) {
			if (xhr && xhr.response && typeof xhr.response === "object") {
				return xhr.response;
			}
			if (xhr && typeof xhr.responseText === "string" && xhr.responseText) {
				try {
					return JSON.parse(xhr.responseText);
				}
				catch (_error) {
					return null;
				}
			}
			return null;
		}

		async function apiRequest(method, path, options) {
			options = options || {};
			if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//")) {
				throw codedError("INVALID_API_PATH", "Invalid API path");
			}
			const baseURL = validateBackendURL(getStringPref("backendURL", DEFAULT_BACKEND));
			const needsAuth = options.auth !== false;
			let token = runtime.token;
			if (needsAuth && !token) {
				token = await resolveToken();
			}
			if (needsAuth && !token) {
				throw codedError("TOKEN_MISSING", "Backend token is missing");
			}

			const headers = { Accept: "application/json" };
			let body;
			if (needsAuth) {
				headers.Authorization = `Bearer ${token}`;
			}
			if (options.body !== undefined) {
				headers["Content-Type"] = "application/json";
				body = JSON.stringify(options.body);
			}

			let xhr;
			try {
				xhr = await Zotero.HTTP.request(method, baseURL + path, {
					body,
					headers,
					responseType: "json",
					timeout: options.timeout || 30000,
					errorDelayMax: 0,
					followRedirects: false,
					successCodes: false,
					anon: true,
				});
			}
			catch (error) {
				throw codedError("BACKEND_UNREACHABLE", safeError(error));
			}

			const payload = responseJSON(xhr);
			if (!xhr || xhr.status < 200 || xhr.status >= 300) {
				const detail = payload && (payload.detail || payload.message || payload.error);
				const text = typeof detail === "string"
					? detail
					: `HTTP ${xhr ? xhr.status : 0}`;
				throw codedError("BACKEND_HTTP", text, xhr ? xhr.status : 0);
			}
			return payload;
		}

		function ownerWindow(context) {
			return context?.menuElem?.ownerGlobal || Zotero.getMainWindow() || null;
		}

		function loadWindow(window) {
			if (!window || !window.document || runtime.windows.has(window)) {
				return;
			}
			try {
				window.MozXULElement.insertFTLIfNeeded(FTL_FILE);
				runtime.windows.add(window);
			}
			catch (error) {
				Zotero.logError(error);
			}
		}

		function unloadWindow(window) {
			if (!window || !window.document) {
				return;
			}
			try {
				for (const link of window.document.querySelectorAll(
					`link[rel="localization"][href="${FTL_FILE}"]`
				)) {
					link.remove();
				}
			}
			catch (error) {
				Zotero.logError(error);
			}
			runtime.windows.delete(window);
		}

		function interpolateFallback(template, args) {
			return Object.entries(args || {}).reduce(
				(value, [key, replacement]) => value.split(`{${key}}`).join(String(replacement)),
				template
			);
		}

		async function l10n(id, args, window) {
			window = window || Zotero.getMainWindow();
			if (window) {
				loadWindow(window);
				try {
					const value = await window.document.l10n.formatValue(id, args || undefined);
					if (value && value !== id) {
						return value;
					}
				}
				catch (error) {
					Zotero.debug(`${ADDON_ID}: Fluent lookup failed for ${id} (${safeError(error)})`, 2);
				}
			}
			return interpolateFallback(fallbackStrings[id] || id, args);
		}

		async function showAlert(messageID, args, window, titleID) {
			const title = await l10n(titleID || "zqr-dialog-title", null, window);
			const message = await l10n(messageID, args, window);
			Services.prompt.alert(window || null, title, message);
		}

		async function showNotification(messageID, args, window, forceModal) {
			const title = await l10n("zqr-dialog-title", null, window);
			const message = await l10n(messageID, args, window);
			if (!forceModal) {
				try {
					const progressWindow = new Zotero.ProgressWindow({ closeOnClick: true });
					progressWindow.changeHeadline(title);
					progressWindow.addDescription(message);
					progressWindow.show();
					progressWindow.startCloseTimer(6000);
					return;
				}
				catch (error) {
					Zotero.logError(error);
				}
			}
			Services.prompt.alert(window || null, title, message);
		}

		function runCommand(task, window) {
			Promise.resolve()
				.then(task)
				.catch(async error => {
					Zotero.logError(error);
					if (error?.code === "TOKEN_MISSING") {
						await showAlert("zqr-notify-token-missing", null, window);
					}
					else if (error?.code === "INVALID_URL") {
						await showAlert("zqr-settings-invalid-url", null, window);
					}
					else {
						await showAlert("zqr-notify-backend-error", { error: safeError(error) }, window);
					}
				});
		}

		function selectionMayContainPDF(items) {
			for (const item of Array.isArray(items) ? items : []) {
				try {
					if (item?.isPDFAttachment?.() && item.parentID) {
						return true;
					}
					if (item?.isRegularItem?.() && item.numAttachments() > 0) {
						return true;
					}
				}
				catch (error) {
					Zotero.logError(error);
				}
			}
			return false;
		}

		function menuItems(context) {
			return Array.isArray(context?.items) ? context.items.slice() : [];
		}

		function registerMenu() {
			if (runtime.menuHandle) {
				return;
			}
			const command = handler => (event, context) => {
				const window = event?.target?.ownerGlobal || ownerWindow(context);
				runCommand(() => handler(menuItems(context), window), window);
			};
			const safeShowing = (labelID, handler) => (event, context) => {
				try {
					const menuElem = context?.menuElem;
					if (menuElem && !menuElem.getAttribute("label")) {
						menuElem.setAttribute("label", menuFallbackString(labelID));
					}
					handler?.(event, context);
				}
				catch (error) {
					// MenuManager runs hooks inside Zotero's own popup event. Never let a
					// plugin callback abort construction of Zotero's native menus.
					Zotero.logError(error);
				}
			};
			const eligibility = safeShowing("zqr-menu-generate", (_event, context) => {
				context?.setEnabled?.(selectionMayContainPDF(context.items));
			});
			runtime.menuHandle = Zotero.MenuManager.registerMenu({
				menuID: MENU_ID,
				pluginID: ADDON_ID,
				target: "main/library/item",
				menus: [{
					menuType: "submenu",
					l10nID: "zqr-menu-root",
					onShowing: safeShowing("zqr-menu-root"),
					menus: [
						{
							menuType: "menuitem",
							l10nID: "zqr-menu-generate",
							onShowing: eligibility,
							onCommand: command((items, window) => submitSelection(items, false, window)),
						},
						{
							menuType: "menuitem",
							l10nID: "zqr-menu-regenerate",
							onShowing: safeShowing("zqr-menu-regenerate", (_event, context) => {
								context?.setEnabled?.(selectionMayContainPDF(context.items));
							}),
							onCommand: command((items, window) => submitSelection(items, true, window)),
						},
						{
							menuType: "menuitem",
							l10nID: "zqr-menu-status",
							onShowing: safeShowing("zqr-menu-status", (_event, context) => {
								context?.setEnabled?.(menuItems(context).length > 0);
							}),
							onCommand: command((items, window) => showStatus(items, window)),
						},
						{ menuType: "separator" },
						{
							menuType: "menuitem",
							l10nID: "zqr-menu-settings",
							onShowing: safeShowing("zqr-menu-settings"),
							onCommand: command((_items, window) => openSettings(window)),
						},
						{
							menuType: "menuitem",
							l10nID: "zqr-menu-auto",
							l10nArgs: JSON.stringify({ enabled: getBoolPref("autoProcess", false) ? "true" : "false" }),
							onShowing: safeShowing("zqr-menu-auto", (_event, context) => {
								context?.setL10nArgs?.(JSON.stringify({
									enabled: getBoolPref("autoProcess", false) ? "true" : "false",
								}));
							}),
							onCommand: command((_items, window) => toggleAutomatic(window)),
						},
					],
				}],
			});
			if (!runtime.menuHandle) {
				throw new Error("Zotero.MenuManager rejected the item menu registration");
			}
		}

		function registerNotifier() {
			if (runtime.notifierID) {
				return;
			}
			runtime.notifierID = Zotero.Notifier.registerObserver({
				notify(event, type, ids, extraData) {
					if (!runtime.started || type !== "item" || !NOTIFIER_EVENTS.has(event)
							|| !getBoolPref("autoProcess", false)) {
						return;
					}
					for (const rawID of Array.isArray(ids) ? ids : [ids]) {
						const id = Number(rawID);
						if (!Number.isInteger(id) || id <= 0 || runtime.ownWriteIDs.has(id)
								|| isOwnNotifierData(extraData, id)) {
							continue;
						}
						runtime.pendingItemIDs.add(id);
					}
					debounceNotifier();
				},
			}, ["item"], "zotero-quick-read");
		}

		function isOwnNotifierData(extraData, id) {
			return Boolean(extraData && (
				extraData.zoteroQuickRead === true
				|| extraData[id]?.zoteroQuickRead === true
				|| extraData[String(id)]?.zoteroQuickRead === true
			));
		}

		function debounceNotifier() {
			if (runtime.debounceTimer) {
				clearTimeout(runtime.debounceTimer);
			}
			const epoch = runtime.epoch;
			runtime.debounceTimer = setTimeout(() => {
				runtime.debounceTimer = null;
				if (!runtime.started || epoch !== runtime.epoch) {
					return;
				}
				flushNotifierItems().catch(Zotero.logError);
			}, 1200);
		}

		async function flushNotifierItems() {
			const ids = Array.from(runtime.pendingItemIDs);
			runtime.pendingItemIDs.clear();
			for (const id of ids) {
				if (!runtime.started || !getBoolPref("autoProcess", false)) {
					return;
				}
				try {
					const attachment = await Zotero.Items.getAsync(id);
					if (!attachment || hasKnownAttachmentJob(attachment.libraryID, attachment.key)) {
						clearAttachmentRetry(id);
						continue;
					}
					const candidate = await readyCandidate(attachment, true);
					if (!candidate) {
						if (attachment.isPDFAttachment?.() && attachment.parentID) {
							scheduleAttachmentRetry(id);
						}
						continue;
					}
					clearAttachmentRetry(id);
					if (hasKnownAttachmentJob(candidate.attachment.libraryID, candidate.attachment.key)) {
						continue;
					}
					await submitCandidate(candidate, false, "automatic");
				}
				catch (error) {
					Zotero.debug(`${ADDON_ID}: automatic submission skipped (${safeError(error)})`, 2);
				}
			}
		}

		function scheduleAttachmentRetry(itemID) {
			let entry = runtime.attachmentRetries.get(itemID);
			if (!entry) {
				entry = { attempts: 0, timer: null };
				runtime.attachmentRetries.set(itemID, entry);
			}
			if (entry.timer || entry.attempts >= 120 || !runtime.started
					|| !getBoolPref("autoProcess", false)) {
				return;
			}
			entry.attempts++;
			const epoch = runtime.epoch;
			entry.timer = setTimeout(() => {
				runtime.otherTimers.delete(entry.timer);
				entry.timer = null;
				if (!runtime.started || runtime.epoch !== epoch
						|| !getBoolPref("autoProcess", false)) {
					return;
				}
				runtime.pendingItemIDs.add(itemID);
				debounceNotifier();
			}, Math.min(30000, 4000 + entry.attempts * 1000));
			runtime.otherTimers.add(entry.timer);
		}

		function clearAttachmentRetry(itemID) {
			const entry = runtime.attachmentRetries.get(itemID);
			if (entry?.timer) {
				clearTimeout(entry.timer);
				runtime.otherTimers.delete(entry.timer);
			}
			runtime.attachmentRetries.delete(itemID);
		}

		function hasKnownAttachmentJob(libraryID, attachmentKey) {
			if (!runtime.state || !attachmentKey) {
				return false;
			}
			return Object.values(runtime.state.jobs).some(job => job
				&& job.libraryID === libraryID
				&& job.attachmentKey === attachmentKey
				&& job.status !== "cancelled");
		}

		async function readyCandidate(attachment, verifyStable) {
			if (!attachment || attachment.deleted || !attachment.isPDFAttachment?.() || !attachment.parentID) {
				return null;
			}
			if (attachment.attachmentSyncState === 1 || attachment.attachmentSyncState === 4) {
				return null;
			}
			const parent = await Zotero.Items.getAsync(attachment.parentID);
			if (!parent || parent.deleted || !parent.isRegularItem?.() || !parent.isEditable()) {
				return null;
			}
			const path = await attachment.getFilePathAsync();
			if (!path) {
				return null;
			}
			let first;
			try {
				first = await IOUtils.stat(path);
			}
			catch (_error) {
				return null;
			}
			if (!first || !first.size) {
				return null;
			}
			if (verifyStable) {
				await Zotero.Promise.delay(900);
				let second;
				try {
					second = await IOUtils.stat(path);
				}
				catch (_error) {
					return null;
				}
				if (!second || second.size !== first.size) {
					return null;
				}
			}
			return { attachment, parent, path };
		}

		async function candidatesFromSelection(items) {
			const candidates = [];
			const seen = new Set();
			for (const selected of Array.isArray(items) ? items : []) {
				let attachments = [];
				if (selected?.isPDFAttachment?.()) {
					attachments = [selected];
				}
				else if (selected?.isRegularItem?.()) {
					try {
						const preferred = await selected.getBestAttachments();
						const preferredPDF = preferred.find(item => item?.isPDFAttachment?.());
						if (preferredPDF) {
							attachments = [preferredPDF];
						}
						else {
							const childIDs = selected.getAttachments(false);
							const children = childIDs.length ? await Zotero.Items.getAsync(childIDs) : [];
							const childPDF = children.find(item => item?.isPDFAttachment?.());
							if (childPDF) {
								attachments = [childPDF];
							}
						}
					}
					catch (error) {
						Zotero.logError(error);
					}
				}
				for (const attachment of attachments) {
					const key = `${attachment.libraryID}:${attachment.key}`;
					if (seen.has(key)) {
						continue;
					}
					seen.add(key);
					const candidate = await readyCandidate(attachment, true);
					if (candidate) {
						candidates.push(candidate);
					}
				}
			}
			return candidates;
		}

		async function submitSelection(items, force, window) {
			const candidates = await candidatesFromSelection(items);
			if (!candidates.length) {
				await showAlert("zqr-notify-no-ready-pdf", null, window);
				return;
			}
			let submitted = 0;
			for (const candidate of candidates) {
				await submitCandidate(candidate, force, force ? "regenerate" : "manual");
				submitted++;
			}
			await showNotification("zqr-notify-submitted", { count: submitted }, window, false);
		}

		async function submitCandidate(candidate, force, source) {
			const parent = candidate.parent;
			const attachment = candidate.attachment;
			const response = await apiRequest("POST", "/api/v1/jobs", {
				body: {
					library_id: parent.libraryID,
					parent_key: parent.key,
					attachment_key: attachment.key,
					attachment_path: candidate.path,
					title: String(parent.getField("title") || attachment.attachmentFilename || "").slice(0, 1000),
					force: Boolean(force),
				},
				timeout: 30000,
			});
			const job = response?.job;
			if (!job || typeof job.id !== "string" || typeof job.status !== "string") {
				throw codedError("INVALID_RESPONSE", "The backend returned an invalid job response");
			}
			recordJob(job, source);
			await handleJob(job, { notifyOnApply: source !== "automatic" });
			return { job, created: Boolean(response.created) };
		}

		function recordJob(job, source) {
			const previous = runtime.state.jobs[job.id] || {};
			runtime.state.jobs[job.id] = {
				id: job.id,
				status: String(job.status || "unknown"),
				libraryID: Number(job.library_id),
				parentKey: String(job.parent_key || ""),
				attachmentKey: job.attachment_key ? String(job.attachment_key) : null,
				title: String(job.title || previous.title || ""),
				message: job.message ? String(job.message).slice(0, 1000) : null,
				errorCode: job.error_code ? String(job.error_code).slice(0, 200) : null,
				createdAt: String(job.created_at || previous.createdAt || ""),
				updatedAt: String(job.updated_at || new Date().toISOString()),
				forceGeneration: Boolean(job.force_generation),
				source: source || previous.source || "backend-sync",
				applyError: previous.applyError || null,
			};
			persistState();
		}

		async function handleJob(job, options) {
			recordJob(job, null);
			clearPoll(job.id);
			if (job.status === "completed") {
				try {
					await applyCompletedJob(job, options || {});
				}
				catch (error) {
					const saved = runtime.state.jobs[job.id];
					if (saved) {
						saved.applyError = safeError(error);
						persistState();
					}
					throw error;
				}
				return;
			}
			if (POLLABLE_STATUSES.has(job.status)) {
				schedulePoll(job.id, pollDelay(job.status));
			}
		}

		function pollDelay(status) {
			return status === "waiting_fulltext" ? 8000 : 2500;
		}

		function clearPoll(jobID) {
			const timer = runtime.pollTimers.get(jobID);
			if (timer) {
				clearTimeout(timer);
				runtime.pollTimers.delete(jobID);
			}
		}

		function schedulePoll(jobID, delay) {
			if (!runtime.started || runtime.pollTimers.has(jobID)) {
				return;
			}
			const epoch = runtime.epoch;
			const timer = setTimeout(() => {
				runtime.pollTimers.delete(jobID);
				if (!runtime.started || epoch !== runtime.epoch) {
					return;
				}
				pollJob(jobID).catch(Zotero.logError);
			}, delay);
			runtime.pollTimers.set(jobID, timer);
		}

		async function pollJob(jobID) {
			try {
				const response = await apiRequest("GET", `/api/v1/jobs/${encodeURIComponent(jobID)}`);
				if (!response?.job) {
					throw codedError("INVALID_RESPONSE", "The backend returned an invalid job status");
				}
				runtime.pollFailures.delete(jobID);
				await handleJob(response.job, { notifyOnApply: true });
			}
			catch (error) {
				const failures = (runtime.pollFailures.get(jobID) || 0) + 1;
				runtime.pollFailures.set(jobID, failures);
				Zotero.debug(`${ADDON_ID}: job polling failed (${safeError(error)})`, 2);
				if (runtime.started && error?.status !== 401 && error?.status !== 403) {
					schedulePoll(jobID, Math.min(30000, 2000 * (2 ** Math.min(failures, 4))));
				}
			}
		}

		async function syncRecentJobs(options) {
			if (runtime.syncPromise) {
				return runtime.syncPromise;
			}
			runtime.syncPromise = (async () => {
				const response = await apiRequest("GET", "/api/v1/jobs?limit=50");
				if (!Array.isArray(response?.jobs)) {
					throw codedError("INVALID_RESPONSE", "The backend returned an invalid job list");
				}
				const returnedIDs = new Set();
				for (const job of response.jobs) {
					if (!job || typeof job.id !== "string") {
						continue;
					}
					returnedIDs.add(job.id);
					await handleJob(job, options || { notifyOnApply: false });
				}
				const pendingIDs = Object.values(runtime.state.jobs)
					.filter(job => job && POLLABLE_STATUSES.has(job.status) && !returnedIDs.has(job.id))
					.map(job => job.id);
				for (const jobID of pendingIDs) {
					try {
						const individual = await apiRequest("GET", `/api/v1/jobs/${encodeURIComponent(jobID)}`);
						if (individual?.job) {
							await handleJob(individual.job, options || { notifyOnApply: false });
						}
					}
					catch (error) {
						Zotero.debug(`${ADDON_ID}: unable to synchronize job ${jobID} (${safeError(error)})`, 2);
					}
				}
			})();
			try {
				return await runtime.syncPromise;
			}
			finally {
				runtime.syncPromise = null;
			}
		}

		function normalizeTags(tags) {
			const result = [];
			const seen = new Set();
			for (const rawTag of Array.isArray(tags) ? tags : []) {
				const tag = String(rawTag || "").replace(/[\u0000-\u001F\u007F]/g, " ")
					.replace(/\s+/g, " ").trim().slice(0, 80);
				const folded = tag.toLocaleLowerCase();
				if (tag && !seen.has(folded)) {
					seen.add(folded);
					result.push(tag);
				}
				if (result.length >= 8) {
					break;
				}
			}
			return result;
		}

		function escapeHTML(value) {
			return String(value)
				.replace(/&/g, "&amp;")
				.replace(/</g, "&lt;")
				.replace(/>/g, "&gt;")
				.replace(/"/g, "&quot;")
				.replace(/'/g, "&#39;");
		}

		function sanitizeNoteHTML(source) {
			const html = String(source || "");
			if (!html || html.length > 1000000) {
				throw codedError("INVALID_RESULT", "The generated note is empty or too large");
			}
			const window = Zotero.getMainWindow();
			if (!window?.DOMParser) {
				throw codedError("NO_DOM_PARSER", "A Zotero window is required to sanitize the note");
			}
			const document = new window.DOMParser().parseFromString(html, "text/html");
			const allowed = new Set([
				"div", "p", "h1", "h2", "h3", "ul", "ol", "li", "strong", "b",
				"em", "i", "small", "span", "hr", "br", "blockquote", "code",
			]);
			const voidElements = new Set(["hr", "br"]);
			const serialize = node => {
				if (node.nodeType === 3) {
					return escapeHTML(node.nodeValue || "");
				}
				if (node.nodeType !== 1) {
					return "";
				}
				const tag = node.localName.toLowerCase();
				const children = Array.from(node.childNodes).map(serialize).join("");
				if (!allowed.has(tag)) {
					return children;
				}
				let attributes = "";
				if (tag === "span" && node.getAttribute("class") === "zqr-source") {
					attributes = ' class="zqr-source"';
				}
				else if (tag === "div") {
					for (const name of ["data-zqr-note", "data-zqr-schema"]) {
						const value = node.getAttribute(name);
						if (value && /^[A-Za-z0-9._-]{1,32}$/.test(value)) {
							attributes += ` ${name}="${escapeHTML(value)}"`;
						}
					}
				}
				return voidElements.has(tag)
					? `<${tag}${attributes}>`
					: `<${tag}${attributes}>${children}</${tag}>`;
			};
			return Array.from(document.body.childNodes).map(serialize).join("");
		}

		function normalizeNoteContent(value) {
			return String(value || "")
				.normalize("NFC")
				.replace(/\r\n?/g, "\n")
				.replace(/>\s+</g, "><")
				.trim();
		}

		async function contentHash(value) {
			const normalized = normalizeNoteContent(value);
			const window = Zotero.getMainWindow();
			const cryptoObject = globalThis.crypto || window?.crypto;
			const Encoder = globalThis.TextEncoder || window?.TextEncoder;
			if (!cryptoObject?.subtle || !Encoder) {
				throw codedError("HASH_UNAVAILABLE", "SHA-256 is unavailable");
			}
			const digest = await cryptoObject.subtle.digest("SHA-256", new Encoder().encode(normalized));
			return "sha256:" + Array.from(new Uint8Array(digest))
				.map(byte => byte.toString(16).padStart(2, "0")).join("");
		}

		function parentRef(libraryID, parentKey) {
			return `${libraryID}:${parentKey}`;
		}

		function markOwnWrite(id) {
			if (!Number.isInteger(id) || id <= 0) {
				return;
			}
			runtime.ownWriteIDs.add(id);
			const timer = setTimeout(() => {
				runtime.ownWriteIDs.delete(id);
				runtime.otherTimers.delete(timer);
			}, 5000);
			runtime.otherTimers.add(timer);
		}

		async function applyCompletedJob(job, options) {
			if (runtime.state.applied[job.id] || runtime.applyingJobs.has(job.id)) {
				return;
			}
			if (!job.result || typeof job.result.note_html !== "string") {
				throw codedError("INVALID_RESULT", "Completed job has no note_html result");
			}
			runtime.applyingJobs.add(job.id);
			try {
				const libraryID = Number(job.library_id);
				const parentKey = String(job.parent_key || "");
				const parent = await Zotero.Items.getByLibraryAndKeyAsync(libraryID, parentKey);
				if (!parent || !parent.isRegularItem?.()) {
					throw codedError("PARENT_MISSING", "The parent Zotero item no longer exists");
				}
				if (!parent.isEditable()) {
					throw codedError("PARENT_READ_ONLY", "The parent Zotero item is read-only");
				}

				const reference = parentRef(libraryID, parentKey);
				const previous = runtime.state.records[reference];
				let existingNote = null;
				let manualEdit = false;
				if (previous?.noteKey) {
					existingNote = await Zotero.Items.getByLibraryAndKeyAsync(libraryID, previous.noteKey);
					if (!existingNote || !existingNote.isNote?.() || existingNote.parentKey !== parentKey
							|| existingNote.deleted) {
						existingNote = null;
					}
					else if (!previous.normalizedContentHash
							|| await contentHash(existingNote.getNote()) !== previous.normalizedContentHash) {
						manualEdit = true;
						existingNote = null;
					}
				}

				const sanitized = sanitizeNoteHTML(job.result.note_html);
				const noteHTML = `<div data-zqr-job-id="${escapeHTML(job.id)}">${sanitized}</div>`;
				const aiTags = normalizeTags(job.result.tags);
				let targetNote = existingNote;
				await Zotero.DB.executeTransaction(async () => {
					if (targetNote) {
						markOwnWrite(targetNote.id);
						targetNote.setNote(noteHTML);
						await targetNote.save({ notifierData: { zoteroQuickRead: true } });
					}
					else {
						targetNote = new Zotero.Item("note");
						targetNote.libraryID = parent.libraryID;
						targetNote.parentKey = parent.key;
						targetNote.setNote(noteHTML);
						await targetNote.save({ notifierData: { zoteroQuickRead: true } });
						markOwnWrite(targetNote.id);
					}

					const currentTags = new Set(parent.getTags().map(tag => tag.tag));
					let tagsChanged = false;
					for (const tag of aiTags) {
						if (!currentTags.has(tag) && parent.addTag(tag, 0)) {
							currentTags.add(tag);
							tagsChanged = true;
						}
					}
					if (tagsChanged) {
						markOwnWrite(parent.id);
						await parent.save({ notifierData: { zoteroQuickRead: true } });
					}
				});

				const hash = await contentHash(targetNote.getNote());
				const appliedAt = new Date().toISOString();
				const application = {
					jobID: job.id,
					libraryID,
					parentKey,
					noteKey: targetNote.key,
					normalizedContentHash: hash,
					aiTags,
					appliedAt,
					manualEditPreserved: manualEdit,
				};
				runtime.state.applied[job.id] = application;
				runtime.state.records[reference] = application;
				if (runtime.state.jobs[job.id]) {
					runtime.state.jobs[job.id].applyError = null;
					runtime.state.jobs[job.id].noteKey = targetNote.key;
					runtime.state.jobs[job.id].normalizedContentHash = hash;
					runtime.state.jobs[job.id].aiTags = aiTags;
				}
				persistState();

				const window = Zotero.getMainWindow();
				if (manualEdit) {
					await showNotification("zqr-notify-manual-edit", null, window, false);
				}
				else if (options?.notifyOnApply) {
					await showNotification("zqr-notify-applied", {
						title: String(parent.getField("title") || parent.key),
					}, window, false);
				}
			}
			finally {
				runtime.applyingJobs.delete(job.id);
			}
		}

		async function openSettings(window) {
			const title = await l10n("zqr-dialog-title", null, window);
			const urlLabel = await l10n("zqr-settings-url", null, window);
			const urlValue = { value: getStringPref("backendURL", DEFAULT_BACKEND) };
			if (!Services.prompt.prompt(window || null, title, urlLabel, urlValue, null, {})) {
				return;
			}
			let normalizedURL;
			try {
				normalizedURL = validateBackendURL(urlValue.value);
			}
			catch (_error) {
				await showAlert("zqr-settings-invalid-url", null, window);
				return;
			}

			const tokenLabel = await l10n("zqr-settings-token", null, window);
			const clearLabel = await l10n("zqr-settings-clear-token", null, window);
			const tokenValue = { value: "" };
			const clearValue = { value: false };
			if (!Services.prompt.promptPassword(
				window || null, title, tokenLabel, tokenValue, clearLabel, clearValue
			)) {
				return;
			}
			let newToken = "";
			try {
				newToken = validateToken(tokenValue.value, true);
			}
			catch (_error) {
				await showAlert("zqr-settings-invalid-token", null, window);
				return;
			}

			setStringPref("backendURL", normalizedURL);
			if (clearValue.value) {
				try {
					Services.prefs.clearUserPref(PREF_ROOT + "apiToken");
				}
				catch (_error) {}
			}
			else if (newToken) {
				setStringPref("apiToken", newToken);
			}
			runtime.token = "";
			runtime.tokenSource = "none";
			await resolveToken();
			await showNotification("zqr-settings-saved", null, window, false);

			try {
				await apiRequest("GET", "/health", { auth: false, timeout: 5000 });
				if (!runtime.token) {
					await showAlert("zqr-notify-token-missing", null, window);
					return;
				}
				try {
					await apiRequest("GET", "/api/v1/info", { timeout: 5000 });
					await showNotification("zqr-settings-connected", null, window, false);
				}
				catch (error) {
					await showAlert("zqr-settings-health-only", { error: safeError(error) }, window);
				}
			}
			catch (error) {
				await showAlert("zqr-notify-backend-error", { error: safeError(error) }, window);
			}
		}

		async function toggleAutomatic(window) {
			const enabled = !getBoolPref("autoProcess", false);
			setBoolPref("autoProcess", enabled);
			if (enabled) {
				setStringPref("autoSince", new Date().toISOString());
			}
			if (!enabled) {
				runtime.pendingItemIDs.clear();
				for (const itemID of Array.from(runtime.attachmentRetries.keys())) {
					clearAttachmentRetry(itemID);
				}
				if (runtime.debounceTimer) {
					clearTimeout(runtime.debounceTimer);
					runtime.debounceTimer = null;
				}
			}
			await showNotification(enabled ? "zqr-notify-auto-on" : "zqr-notify-auto-off", null, window, false);
		}

		async function selectedParentRefs(items) {
			const references = new Map();
			for (const item of Array.isArray(items) ? items : []) {
				let parent = null;
				if (item?.isRegularItem?.()) {
					parent = item;
				}
				else if (item?.parentID) {
					parent = await Zotero.Items.getAsync(item.parentID);
				}
				if (parent?.isRegularItem?.()) {
					const key = parentRef(parent.libraryID, parent.key);
					references.set(key, {
						libraryID: parent.libraryID,
						parentKey: parent.key,
						title: String(parent.getField("title") || parent.key),
					});
				}
			}
			return references;
		}

		async function localizedStatus(job, window) {
			let key = job.status;
			if (job.status === "completed" && runtime.state.applied[job.id]) {
				key = "applied";
			}
			const known = new Set([
				"waiting_fulltext", "queued", "processing", "completed", "applied", "failed",
				"waiting_quota", "reauthorization_required", "cancelled",
			]);
			return l10n(`zqr-status-${known.has(key) ? key.replaceAll("_", "-") : "unknown"}`, null, window);
		}

		async function showStatus(items, window) {
			const references = await selectedParentRefs(items);
			let refreshError = null;
			try {
				await syncRecentJobs({ notifyOnApply: false });
			}
			catch (error) {
				refreshError = error;
			}

			const lines = [];
			if (refreshError) {
				lines.push(await l10n("zqr-status-backend-unavailable", {
					error: safeError(refreshError),
				}, window));
				lines.push("");
			}
			for (const reference of references.values()) {
				const jobs = Object.values(runtime.state.jobs)
					.filter(job => job && job.libraryID === reference.libraryID
						&& job.parentKey === reference.parentKey)
					.sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
				if (!jobs.length) {
					continue;
				}
				const job = jobs[0];
				lines.push(await l10n("zqr-status-line", {
					title: reference.title,
					status: await localizedStatus(job, window),
				}, window));
				const detail = job.applyError || job.message || job.errorCode;
				if (detail) {
					lines.push("  " + await l10n("zqr-status-detail", {
						message: String(detail).slice(0, 500),
					}, window));
				}
			}
			if (!lines.length || (lines.length === 2 && refreshError)) {
				lines.push(await l10n("zqr-status-empty", null, window));
			}
			const title = await l10n("zqr-status-title", null, window);
			Services.prompt.alert(window || null, title, lines.join("\n"));
		}

		function scheduleMenuProbe() {
			if (Services.env.get("ZOTERO_QUICK_READ_MENU_PROBE") !== "1") {
				return;
			}
			const epoch = runtime.epoch;
			const timer = setTimeout(async () => {
				runtime.otherTimers.delete(timer);
				if (!runtime.started || runtime.epoch !== epoch) {
					return;
				}
				try {
					const window = Zotero.getMainWindow();
					const popup = window?.document?.getElementById("zotero-itemmenu");
					if (!window?.ZoteroPane || !popup) {
						throw new Error("The item context menu is unavailable");
					}
					await window.ZoteroPane.buildItemContextMenu();
					await Zotero.Promise.delay(250);
					popup.dispatchEvent(new window.Event("popupshowing"));
					await Zotero.Promise.delay(250);
					const root = popup.querySelector('[data-l10n-id="zqr-menu-root"]');
					const subPopup = root?.querySelector("menupopup");
					if (subPopup) {
						subPopup.dispatchEvent(new window.Event("popupshowing"));
						await Zotero.Promise.delay(250);
					}
					const entries = Array.from(popup.querySelectorAll(".zotero-custom-menu-item"))
						.map(element => ({
							node: element.localName,
							label: element.getAttribute("label"),
							l10nID: element.dataset.l10nId || "",
							hidden: element.hidden,
							disabled: element.disabled,
						}));
					Zotero.debug(`${ADDON_ID}: menu probe ${JSON.stringify({
						childCount: popup.children.length,
						entries,
					})}`);
				}
				catch (error) {
					Zotero.logError(new Error(`${ADDON_ID}: menu probe failed: ${safeError(error)}`));
				}
			}, 2500);
			runtime.otherTimers.add(timer);
		}

		function scheduleStartupSync() {
			const epoch = runtime.epoch;
			const timer = setTimeout(async () => {
				runtime.otherTimers.delete(timer);
				if (!runtime.started || runtime.epoch !== epoch) {
					return;
				}
				try {
					if (!runtime.token) {
						await resolveToken();
					}
					if (runtime.token) {
						await syncRecentJobs({ notifyOnApply: false });
					}
				}
				catch (error) {
					Zotero.debug(`${ADDON_ID}: startup synchronization deferred (${safeError(error)})`, 2);
					for (const job of Object.values(runtime.state.jobs)) {
						if (job && POLLABLE_STATUSES.has(job.status)) {
							schedulePoll(job.id, 10000);
						}
					}
				}
			}, 750);
			runtime.otherTimers.add(timer);
		}

		async function startupImpl(data) {
			if (runtime.started) {
				return;
			}
			runtime.started = true;
			runtime.epoch++;
			runtime.rootURI = data?.rootURI || "";
			runtime.state = loadState();
			const epoch = runtime.epoch;
			try {
				await Zotero.initializationPromise;
				if (!runtime.started || runtime.epoch !== epoch) {
					return;
				}
				await resolveToken();
				for (const window of Zotero.getMainWindows()) {
					loadWindow(window);
				}
				registerMenu();
				registerNotifier();
				scheduleStartupSync();
				scheduleMenuProbe();
				Zotero.debug(`${ADDON_ID}: started`);
			}
			catch (error) {
				Zotero.logError(error);
				await shutdownImpl();
				throw error;
			}
		}

		async function shutdownImpl() {
			if (!runtime.started && !runtime.menuHandle && !runtime.notifierID) {
				return;
			}
			runtime.started = false;
			runtime.epoch++;
			if (runtime.debounceTimer) {
				clearTimeout(runtime.debounceTimer);
				runtime.debounceTimer = null;
			}
			for (const timer of runtime.pollTimers.values()) {
				clearTimeout(timer);
			}
			for (const timer of runtime.otherTimers) {
				clearTimeout(timer);
			}
			runtime.pollTimers.clear();
			runtime.otherTimers.clear();
			runtime.pendingItemIDs.clear();
			runtime.attachmentRetries.clear();
			runtime.pollFailures.clear();
			runtime.ownWriteIDs.clear();
			runtime.applyingJobs.clear();
			runtime.syncPromise = null;

			if (runtime.notifierID) {
				Zotero.Notifier.unregisterObserver(runtime.notifierID);
				runtime.notifierID = null;
			}
			if (runtime.menuHandle) {
				Zotero.MenuManager.unregisterMenu(runtime.menuHandle);
				runtime.menuHandle = null;
			}
			for (const window of Array.from(runtime.windows)) {
				unloadWindow(window);
			}
			if (runtime.state) {
				persistState();
			}
			runtime.token = "";
			runtime.tokenSource = "none";
			Zotero.debug(`${ADDON_ID}: stopped`);
		}

		function installImpl() {
			Zotero.debug(`${ADDON_ID}: installed`);
		}

		function uninstallImpl() {
			// Preserve local application state so reinstalling never loses the
			// hashes used to protect manually edited notes.
			Zotero.debug(`${ADDON_ID}: uninstalled (local state preserved)`);
		}

		function onMainWindowLoadImpl(data) {
			if (runtime.started) {
				loadWindow(data?.window);
			}
		}

		function onMainWindowUnloadImpl(data) {
			unloadWindow(data?.window);
		}

		return {
			startup: startupImpl,
			shutdown: shutdownImpl,
			install: installImpl,
			uninstall: uninstallImpl,
			onMainWindowLoad: onMainWindowLoadImpl,
			onMainWindowUnload: onMainWindowUnloadImpl,
		};
	})();

var startup = ZoteroQuickReadPlugin.startup;
var shutdown = ZoteroQuickReadPlugin.shutdown;
var install = ZoteroQuickReadPlugin.install;
var uninstall = ZoteroQuickReadPlugin.uninstall;
var onMainWindowLoad = ZoteroQuickReadPlugin.onMainWindowLoad;
var onMainWindowUnload = ZoteroQuickReadPlugin.onMainWindowUnload;
