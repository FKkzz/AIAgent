# Zotero 论文 AI 速读

Windows 本地运行的 Zotero 论文速读工具。把带 PDF 的论文交给本地后台处理，并在父文献条目下生成中文“AI 速读”子笔记和规范标签。

当前维护版本：`2.0.1`。`2.0.0` 是替换旧软件的完整重构版；`2.0.1` 修复 Zotero 9.0.6 右键菜单与菜单文字显示问题。详见 [CHANGELOG.md](CHANGELOG.md)。

## 先认识三个文件

| 文件 | 用途 |
|---|---|
| `dist/ZoteroQuickRead.exe` | Windows 本地后台。本机交付已生成；GitHub 源码仓库不保存 EXE |
| `dist/zotero-quick-read-2.0.1.xpi` | 安装到 Zotero 的插件 |
| `README.md` | 你正在阅读的使用教程 |

默认后台地址是 `http://127.0.0.1:23120`。不要使用 Zotero Connector 占用的 `23119`。

## 使用前准备

- Windows 10/11 64 位。
- Zotero 9.0.6–9.0.x。
- Firefox，用于本人完成 OpenAI 官方授权。
- 如网络需要代理，准备 Firefox 当前使用的 SOCKS 版本、主机和端口。
- 在 Zotero 中，论文必须已有下载完成的本地 PDF 附件。
- 只有从 GitHub 源码开始构建时，才需要额外安装 Git 和 Python 3.11 或更高版本；已经拿到本机完整交付目录时不需要。

下面所有命令都在 PowerShell 中执行。带 `<...>` 的文字必须替换，不能原样复制。

## 第一次使用：从零到成功

### 第 1 步：进入项目目录

如果你正在使用本机已经构建好的完整交付，打开 PowerShell，执行：

```powershell
cd D:\CodeWorkspace\AIAgent
```

如果是第一次从 GitHub 获取源码，可以在 GitHub 页面点击 `Code → Download ZIP` 并解压；也可以安装 Git 后执行：

```powershell
cd <你希望保存项目的目录>
git clone https://github.com/FKkzz/AIAgent.git
cd .\AIAgent
```

GitHub 源码仓库不保存 Windows EXE。仅源码用户还要确认 `python --version` 显示 3.11 或更高版本，然后执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-backend.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-backend.ps1
```

看到 `Built ...\dist\ZoteroQuickRead.exe` 后再继续。下载 ZIP 的用户也应先在 PowerShell 中 `cd` 到解压后的项目目录，再运行上面两条构建命令。

### 第 2 步：初始化后台

```powershell
.\dist\ZoteroQuickRead.exe init
```

程序会在 `%LOCALAPPDATA%\ZoteroQuickRead\` 创建设置、加密凭据、任务队列和插件认证文件。重复执行不会删除已有登录或任务。

### 第 3 步：配置 SOCKS 代理

如果以前已经填写过代理，只是尚未启用：

```powershell
.\dist\ZoteroQuickRead.exe configure --proxy on
```

第一次配置时，把示例端口替换成 Firefox 的真实 SOCKS 端口：

```powershell
.\dist\ZoteroQuickRead.exe configure `
  --proxy on `
  --socks-version 5 `
  --proxy-host 127.0.0.1 `
  --proxy-port <你的端口> `
  --proxy-dns proxy `
  --firefox-path "C:\Program Files\Mozilla Firefox\firefox.exe"
```

`--proxy-dns proxy` 表示域名也由代理端解析，对应 `socks5h` 或 `socks4a`。如果代理需要用户名和密码：

```powershell
.\dist\ZoteroQuickRead.exe configure --proxy-username "<用户名>"
.\dist\ZoteroQuickRead.exe configure --set-proxy-password
```

第二条命令会安全地交互输入密码，不让密码进入 PowerShell 历史。如果所在网络可以直接访问 OpenAI：

```powershell
.\dist\ZoteroQuickRead.exe configure --proxy off
```

验证网络路径：

```powershell
.\dist\ZoteroQuickRead.exe diagnose
```

启用代理时，登录前至少应看到：

```text
proxy_configuration  ok
proxy_tcp            ok
oidc_and_jwks         ok
```

首次尚未登录时，最后出现 `reauthorization_required` 是正常现象。

### 第 4 步：登录 ChatGPT

```powershell
.\dist\ZoteroQuickRead.exe auth-login
```

程序会在配置的 Firefox 中打开 OpenAI 官方授权页。请本人登录并同意 ChatGPT plan usage。浏览器显示完成后回到 PowerShell，再检查：

```powershell
.\dist\ZoteroQuickRead.exe auth-status
```

成功结果应包含 `signed_in: true` 和 `chatgpt.tokens.use.direct`。账号是否具备套餐调用权限，以 OpenAI 实际授权结果为准。

### 第 5 步：选择模型

读取账号实际可用的模型：

```powershell
.\dist\ZoteroQuickRead.exe models
```

每个模型会返回 `id` 和 `display_name`。配置时必须复制区分大小写的 `id`，不要复制显示名称。例如列表中若出现：

```json
{
  "id": "gpt-6.1-sol",
  "display_name": "GPT-6.1-Sol"
}
```

正确命令是：

```powershell
.\dist\ZoteroQuickRead.exe configure --model "gpt-6.1-sol"
```

错误示例是 `--model "GPT-6.1-Sol"`；它是显示名称，可能返回 HTTP 400。

### 第 6 步：完成最小推理验收

```powershell
.\dist\ZoteroQuickRead.exe diagnose --models --inference
```

当 `models` 和 `inference` 均为 `status: ok` 时，代理、登录、套餐权限、模型目录和最小流式推理已经全部打通。

### 第 7 步：启动本地后台

```powershell
.\dist\ZoteroQuickRead.exe serve
```

保持这个 PowerShell 窗口打开。另开一个 PowerShell 可以检查：

```powershell
Invoke-RestMethod http://127.0.0.1:23120/health
```

正常结果包含 `status: ok`。

### 第 8 步：安装或升级 Zotero 插件

安装文件位于项目目录的 `dist\zotero-quick-read-2.0.1.xpi`。本机当前完整路径是 `D:\CodeWorkspace\AIAgent\dist\zotero-quick-read-2.0.1.xpi`。

1. 打开 Zotero。
2. 进入“工具 → 插件”。
3. 点击右上角齿轮。
4. 选择“从文件安装插件”。
5. 选择 `zotero-quick-read-2.0.1.xpi`。
6. 如果已安装旧版，确认替换，然后完整退出并重新打开 Zotero。

插件默认读取 `%LOCALAPPDATA%\ZoteroQuickRead\plugin-token`，通常不需要手工复制 Token。

### 第 9 步：处理第一篇论文

1. 确认 Zotero 父文献条目下面已经有本地 PDF。
2. 在中间文献列表中选中父文献条目。
3. 右键，打开“Zotero 论文 AI 速读”。
4. 点击“生成 AI 速读”。
5. 通过“查看状态”观察排队、处理中和完成状态。

完成后，父条目下会出现中文“AI 速读”子笔记，并增量添加通常 4–6 个标签。已有人工标签不会被删除。

## 以后每天怎么用

通常只需要先启动后台：

```powershell
cd D:\CodeWorkspace\AIAgent
.\dist\ZoteroQuickRead.exe serve
```

保持窗口运行，再打开 Zotero：

- “生成 AI 速读”：处理选中的论文。
- “重新生成”：强制建立新任务。人工修改过的旧笔记会被保留。
- “查看状态”：刷新并显示任务状态。
- “设置”：检查后台地址和本地 Token。
- “自动处理”：只监听启用之后新增或变化的 PDF，不会突然处理整个历史文库。

## 先处理本地 PDF，不写入 Zotero

建议第一次先做预览：

```powershell
.\dist\ZoteroQuickRead.exe read "D:\papers\paper.pdf" --title "论文题名"
```

结构化 JSON 和 HTML 预览位于：

```text
%LOCALAPPDATA%\ZoteroQuickRead\previews\
```

确认内容正常后，再从 Zotero 右键菜单生成正式笔记。

## 常见问题

### 启用插件后右键无反应，或“查看”等菜单文字缺失

这是 2.0.0 基线包的 Fluent 菜单标签兼容问题，已在 2.0.1 修复。重新安装 `dist/zotero-quick-read-2.0.1.xpi`，然后完整重启 Zotero。若问题仍在，先禁用其他插件做一次隔离验证，并查看 [docs/TESTING.md](docs/TESTING.md)。

### `unsupported_country_region_territory`

先检查诊断中是否出现 `enabled: false` 或 `direct_configured`。如果应该使用 SOCKS：

```powershell
.\dist\ZoteroQuickRead.exe configure --proxy on
.\dist\ZoteroQuickRead.exe diagnose
```

如果代理已经启用但仍返回地区错误，确认实际访问地区符合 OpenAI 当前支持范围。不要循环登录，也不要自动改用收费 API Key。

### `OIDC/JWKS 连接失败`

这表示后台网络路径没有打通，不是 Zotero 问题。检查 SOCKS 程序、主机、端口和 DNS 模式，然后重新运行 `diagnose`。

### 模型显示在列表里，但推理返回 HTTP 400

确认 `configure --model` 使用的是模型小写 `id`，而不是 `display_name`。重新执行：

```powershell
.\dist\ZoteroQuickRead.exe models
.\dist\ZoteroQuickRead.exe configure --model "<精确的 id>"
.\dist\ZoteroQuickRead.exe diagnose --models --inference
```

### 插件提示 401 或 Token 缺失

```powershell
.\dist\ZoteroQuickRead.exe token-path
```

确认后台已经执行 `init`，然后在 Zotero 的“AI 速读 → 设置”中重新读取，必要时再手工粘贴 Token。

### PDF 没有生成结果

- `waiting_fulltext`：PDF 尚未下载完成或仍在写入。
- `scanned_or_empty_pdf`：扫描件或文本太少，需要先 OCR。
- `encrypted_pdf`：需要先解除 PDF 加密。
- `pdf_too_large` / `document_too_large`：文件或分段数量超过安全上限；程序不会静默截断。
- 模型不支持页面图像时，可明确降级到纯文本：

```powershell
.\dist\ZoteroQuickRead.exe configure --page-images off
```

### 后台端口被占用

不要使用 Zotero Connector 的 `23119`。选择其他 loopback 端口：

```powershell
.\dist\ZoteroQuickRead.exe configure --port <新端口>
```

随后在 Zotero 插件“设置”中填入相同地址。

## 重新授权与退出

```powershell
.\dist\ZoteroQuickRead.exe auth-status
.\dist\ZoteroQuickRead.exe auth-refresh
.\dist\ZoteroQuickRead.exe auth-login
.\dist\ZoteroQuickRead.exe auth-logout
```

`auth-logout` 会尝试撤销 refresh token；临时网络失败时不会误删本地凭据。

## 可选的普通 API Key 模式

这是显式付费模式，必须由用户主动选择：

```powershell
.\dist\ZoteroQuickRead.exe set-api-key
.\dist\ZoteroQuickRead.exe configure --auth-mode api_key
.\dist\ZoteroQuickRead.exe models --auth-mode api_key
```

程序在套餐限额、权限错误或网络失败时不会自动切换到 API Key。切回套餐模式：

```powershell
.\dist\ZoteroQuickRead.exe configure --auth-mode chatgpt
```

## 数据和安全

- 后台只监听 `127.0.0.1`，并使用随机 Bearer Token 认证插件。
- OAuth Token、API Key 和代理密码由 Windows CurrentUser DPAPI 加密保存在 `secrets.bin`。
- Zotero 插件不会接触 OpenAI Token，只持有本机后台 Token。
- 日志不记录 Token、授权 URL、代理密码或论文全文。
- 正式笔记只在完整收到 `response.completed` 且结构与页码校验通过后写入。
- 标签使用增量添加，不覆盖人工标签。
- 发现 AI 笔记被人工修改后，重新生成会保留旧笔记并创建新笔记。

运行数据默认位于 `%LOCALAPPDATA%\ZoteroQuickRead\`，不会提交到 GitHub，也不会进入 Zotero 同步数据。

## 阅读规则与标签词表

- 阅读指令：[reading-instructions-v1.md](backend/src/zotero_quick_read/resources/reading-instructions-v1.md)
- 标签词表：[tag-vocabulary.json](backend/src/zotero_quick_read/resources/tag-vocabulary.json)

默认生成约 600–1000 个中文字，覆盖实验条件、主要结论、物理图像、核心卖点、来源页码和阅读覆盖范围。理论论文会改用模型、假设与可检验预测结构。

## 开发、测试和重新构建

从 GitHub 克隆后，先安装开发环境：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-backend.ps1
```

运行测试：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

构建 XPI 和本机 EXE：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-all.ps1
```

源码结构：

```text
backend/      Python 后台、资源与自动测试
plugin/       Zotero bootstrapped 插件
scripts/      Windows 安装、测试与打包脚本
docs/         安装、架构和验收文档
dist/         已提交的 XPI，以及本机生成且不入库的 EXE
```

更详细的设计和验收边界见 [架构说明](docs/ARCHITECTURE.md)、[Windows 安装说明](docs/INSTALL-WINDOWS.md)和[测试说明](docs/TESTING.md)。

## 官方接口依据

实现遵循当前 OpenAI 官方公开流程，不抓取浏览器 Cookie、不调用 ChatGPT 私有网页接口：

- [Sign in with ChatGPT：开源本地工具](https://developers.openai.com/siwc/token-sharing-open-source)
- [注册与登录](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [账号与会话](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [模型与推理](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [错误与恢复](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
- [Preview 限制](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)

许可证：MIT。
