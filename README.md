# Zotero Quick Read（Zotero 论文自动速读）

一个面向 Windows 与 Zotero 9 的本地工具：PDF 就绪后，通过官方 OpenAI 接口生成中文物理论文速读，在父文献下创建子笔记，并增量添加规范检索标签。

项目由两个部分组成：

- Zotero 9 插件：右键处理、自动监听、任务状态、子笔记和标签写回。
- 仅监听 loopback 的本地后台：SOCKS、Sign in with ChatGPT、PDF 按页提取、模型调用、持久队列、结果校验和缓存。

当前版本：`0.1.0`。默认后台地址是 `http://127.0.0.1:23120`。没有使用 Zotero 自带 Connector 占用的 `23119` 端口。

## 已实现功能

- 官方 Sign in with ChatGPT 开源本地应用流程：动态注册、稳定 host ID、PKCE、state、nonce、JWKS/ID token 校验、scope 检查、旋转 refresh token、撤销。
- ChatGPT plan usage 与普通 API Key 两种显式模式；两者绝不自动互相回退。
- SOCKS 4/4a/5/5h，支持用户名/密码和本机/代理端 DNS；所有 OpenAI 外部请求使用同一个显式代理 transport。
- Firefox 只负责交互式授权页；OAuth 回调和插件通信均直连 loopback。
- Windows CurrentUser DPAPI 保护 OAuth token、API Key 和代理密码。
- PDF 全文逐页提取、真实页码标记、图注保留、长文分段、不静默截断、选取图像密集页作为内联图像输入。
- 扫描件、加密件、空文本、超大文件和流中断均产生明确失败状态。
- 固定 JSON Schema 校验；只有收到 `response.completed` 且结构验证成功后才生成正式笔记结果。
- SQLite WAL 持久队列：去重、取消、有限重试、单任务并发、重启恢复、额度等待和重新授权状态。
- Zotero 右键菜单：生成、重新生成、查看状态、设置、自动处理开关。
- 自动模式只处理启用后的事件，记录启用时间，不扫描整个历史库；PDF 尚未落盘时有限重试。
- 只使用 Zotero 支持的数据 API 创建子笔记和增量标签，不修改 Zotero SQLite。
- 人工标签不会被删除；人工编辑过的 AI 笔记不会被静默覆盖，重新生成时保留旧笔记并另建新笔记。
- 本地 API Bearer token、Host/Origin 校验、固定 HTML 白名单和模型内容不可信输入边界。

## 官方接口核实结果（2026-10-09）

实现以当前 OpenAI Docs 为准：

- 本地个人/开源项目属于 ChatGPT plan usage 的适用场景，但 Plus/Pro 账号仍须以实际授权和返回 scope 为准，不能仅凭订阅名称保证可用。
- 首次使用 `dynamic_agent_client` 注册，保存回调返回的 issued `client_id`；后续复用该 ID。
- 套餐路线使用公开的 `POST https://api.openai.com/v1/responses`，要求 `store=false`、`stream=true`，不使用 ChatGPT 私有 `backend-api`。
- 模型由当前账号的 `/v1/models` 返回值生成，不硬编码型号。
- 当前文档已明确：所选模型支持时可发送内联文本、图像和文件；但该路线仍不支持 Files 上传 API。本项目使用本地按页文本与必要页面图像，不调用 Files 上传。
- 套餐路线当前不支持 `max_output_tokens` 等一组普通 API 参数，长度目标通过阅读指令和结果校验控制。
- Plus 的五小时用量窗口在使用 ChatGPT plan 的应用间共享，还可能有 app-specific limit；本项目不承诺固定论文篇数，也不绕过限制。

官方依据：

- [Sign in with ChatGPT 概览](https://developers.openai.com/siwc/token-sharing-open-source)
- [注册与登录](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [账号、刷新与用量](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [模型与推理](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview 限制](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [官方集成文章](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)

## 快速安装（Windows）

### 1. 初始化后台

本次 Windows 本机交付已生成 `dist/ZoteroQuickRead.exe`。EXE 属于本机构建产物，不提交进源码仓库；从 GitHub 克隆后可先运行 `scripts/build-backend.ps1` 重新生成。随后在仓库根目录打开 PowerShell：

```powershell
.\dist\ZoteroQuickRead.exe init
```

也可以从源码安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-backend.ps1
```

运行时数据默认位于 `%LOCALAPPDATA%\ZoteroQuickRead\`，不在仓库中。

### 2. 配置 SOCKS

请把示例值替换为 Firefox 实际使用的 SOCKS 参数：

```powershell
.\dist\ZoteroQuickRead.exe configure `
  --proxy on `
  --socks-version 5 `
  --proxy-host 127.0.0.1 `
  --proxy-port <你的端口> `
  --proxy-dns proxy `
  --firefox-path "C:\Program Files\Mozilla Firefox\firefox.exe"
```

如有代理密码，使用交互式输入，避免密码进入命令历史：

```powershell
.\dist\ZoteroQuickRead.exe configure --set-proxy-password
```

`--proxy-dns proxy` 会使用 `socks5h`/`socks4a`，域名由代理端解析；`local` 使用 `socks5`/`socks4`。

### 3. 分层诊断并登录

```powershell
.\dist\ZoteroQuickRead.exe diagnose
.\dist\ZoteroQuickRead.exe auth-login
```

`auth-login` 会启动随机端口的 `127.0.0.1/auth/callback` 监听器，并在配置的 Firefox 中打开 OpenAI 官方授权页。请本人完成登录并同意 ChatGPT plan usage；浏览器登录不能由程序代替。

登录后读取账号模型目录并选择一个返回的模型 ID：

```powershell
.\dist\ZoteroQuickRead.exe models
.\dist\ZoteroQuickRead.exe configure --model "<models 返回的 id>"
.\dist\ZoteroQuickRead.exe diagnose --models --inference
```

只有最后一步收到完整 `response.completed` 才表示阶段一真实联调成功。

### 4. 启动后台

```powershell
.\dist\ZoteroQuickRead.exe serve
```

或运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1
```

服务只绑定 `127.0.0.1:23120`。若修改端口，也要在 Zotero 插件“设置”菜单中修改相同地址。

### 5. 安装 Zotero 插件

可安装文件：`dist/zotero-quick-read-0.1.0.xpi`。

1. Zotero → 工具 → 插件。
2. 点击齿轮 → Install Plugin From File / 从文件安装插件。
3. 选择上述 XPI。
4. 右键一篇带本地 PDF 的父文献，打开“AI 速读”子菜单。

插件默认从 `%LOCALAPPDATA%\ZoteroQuickRead\plugin-token` 自动读取本地 API token；该文件由后台初始化，并限制为当前 Windows 用户访问。如自动读取失败，可在插件“设置”中手工粘贴 token。

## 单篇论文预览（阶段二）

在写入 Zotero 前，可先处理一篇本地论文：

```powershell
.\dist\ZoteroQuickRead.exe read "D:\papers\paper.pdf" --title "论文题名"
```

通过后会在 `%LOCALAPPDATA%\ZoteroQuickRead\previews\` 生成结构化 JSON 和 HTML 预览，并报告实际 usage。PDF 全文不会写入日志或队列数据库。

## Zotero 工作流（阶段三）

- “生成 AI 速读”：处理选中文献的首选本地 PDF；同一父条目、附件指纹、指令版本、模型和认证模式会去重。
- “重新生成”：强制创建新任务。原 AI 笔记未被人工修改时可安全更新；发现修改时保留旧笔记并创建新笔记。
- “查看任务状态”：刷新后台并显示等待全文、排队、处理中、完成、失败、等待额度、重新授权或取消。
- “自动处理”：从启用时刻开始监听 `add/modify/redraw`，不扫描历史库；只在 PDF 有父文献、文件存在且大小稳定后提交。
- 标签采用 `addTag()` 增量添加，绝不使用 `setTags()` 覆盖现有人工标签。

阅读规则位于 [reading-instructions-v1.md](backend/src/zotero_quick_read/resources/reading-instructions-v1.md)，规范词表位于 [tag-vocabulary.json](backend/src/zotero_quick_read/resources/tag-vocabulary.json)。每次推理均显式加载版本化指令。

## 普通 API Key 模式（可选、显式付费）

```powershell
.\dist\ZoteroQuickRead.exe set-api-key
.\dist\ZoteroQuickRead.exe configure --auth-mode api_key
.\dist\ZoteroQuickRead.exe models --auth-mode api_key
```

API Key 由 DPAPI 保护。程序不会在套餐限额、权限错误或网络失败时自动切换到 API Key；切换必须由用户明确执行 `configure --auth-mode ...`。

ChatGPT plan 与关联 credits 的使用/限制在 ChatGPT Settings → Usage 管理。遇到套餐限额时任务进入 `waiting_quota`，不会新建身份或自动付费重试。

## 数据与安全

- OAuth access/refresh/ID token、API Key 和代理密码保存在 DPAPI 加密的 `secrets.bin`。
- Zotero 插件不会接触 OpenAI token，只持有 loopback 后台 token。
- 后台拒绝非 loopback 客户端、异常 Host 和带浏览器 Origin 的请求。
- 不抓取 Firefox Cookie，不复制其他应用 token，不调用私有网页接口。
- 日志不记录 token、授权 URL、代理密码或论文全文。
- 模型文本先经过 JSON Schema 与来源页码校验；笔记 HTML 由后端固定模板生成，并在插件中再次白名单清洗。
- 旧项目中未提交的本地 `.env` 已删除，未复制到新代码或构建产物。

更多说明见 [SECURITY.md](SECURITY.md)。

## 测试与当前验收状态

已在本机完成：

- Python 自动测试：DPAPI、PKCE/state 防重放、refresh、SSE 任意分片/中断、额度/权限错误、SOCKS5 真实握手与代理端 DNS、队列去重/取消/恢复、PDF 多页/扫描件/超限、结构校验、loopback API。
- JavaScript 语法、XPI 根目录结构和 Zotero 9 清单校验。
- 独立 EXE 初始化、DPAPI 存储、`127.0.0.1:23120` 启动、健康检查和 Bearer 认证。
- 本机 Zotero 9.0.6 隔离 profile 实际加载 XPI：插件记录为 `active=true`、`appDisabled=false`，未使用用户真实 Zotero 数据库。

尚需本人操作才能完成：

- 真实 SOCKS 参数下的 OIDC/JWKS、token exchange、模型目录和最小推理联调。
- 使用本人 ChatGPT 账号确认 `chatgpt.tokens.use.direct` 实际获批。
- 用本人指定论文核对 600–1000 字笔记、页码证据、图像页和标签质量。
- 在真实 Zotero 窗口中验证右键、多选、Connector 下载、同步按需下载、只读组库、人工编辑保护和实际写回。

模拟测试不会被当成上述真实联调。详细矩阵见 [docs/TESTING.md](docs/TESTING.md)。

## 常见故障

- `proxy_tcp_failed`：代理进程、主机或端口不通。
- `proxy_authentication_failed`：SOCKS 用户名/密码错误。
- `oidc_failed`：代理虽可达，但 OpenAI OIDC/JWKS 请求失败；检查远程 DNS 和 TLS。
- `chatgpt_plan_scope_missing`：身份登录成功，但未获套餐推理权限；重新授权并确认 consent。
- `subscription_sharing_user_not_eligible`：账号/workspace/策略不适用，不能用重复 OAuth 伪装修复。
- `subscription_sharing_usage_limit_exceeded`：套餐或 app-specific limit 已达；到 ChatGPT Settings → Usage 查看并等待。
- `model_not_found`：重新执行 `models` 并选择账号实际返回的模型。
- `stream_interrupted` / `response_incomplete`：不会写正式笔记，可有限重试。
- `scanned_or_empty_pdf`：需要先 OCR；不会用摘要冒充全文。
- `encrypted_pdf`：先解密 PDF。
- 后台端口被占用：不要改回 Zotero Connector 的 23119；选择其他空闲 loopback 端口，并同步修改插件设置。

完整 Windows 排错步骤见 [docs/INSTALL-WINDOWS.md](docs/INSTALL-WINDOWS.md)。

## 开发与构建

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-backend.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-all.ps1
```

源码结构：

```text
backend/      Python 后台、资源与测试
plugin/       Zotero bootstrapped 插件
scripts/      Windows 安装、测试和打包脚本
docs/         安装、架构与验收文档
dist/         已提交的 XPI，以及本机生成且不入库的 EXE
```

许可证：MIT。

## 已知问题

（注：由于仅个人使用，暂无修补计划。）

* 在添加部分文件时会多次触发本插件
* 在打开部分文件时会触发本插件，例如对部分book类型文件打开时会反复弹窗提示格式不支持
