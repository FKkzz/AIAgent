# Windows 安装与故障排查

## 环境

- Windows 10/11 x64
- Zotero 9.0.6–9.0.x
- Firefox（用于交互式授权）
- SOCKS 4/5 参数（如网络需要代理）

源码运行需要 Python 3.11+；使用 `dist/ZoteroQuickRead.exe` 不需要单独安装 Python。本次本机交付包含该 EXE；它不提交进源码仓库，从 GitHub 克隆后需运行 `scripts/build-backend.ps1` 生成。

## 后台配置位置

默认目录：`%LOCALAPPDATA%\ZoteroQuickRead\`

| 文件 | 内容 |
|---|---|
| `settings.json` | 不含密码的配置 |
| `secrets.bin` | CurrentUser DPAPI 密文 |
| `plugin-token` | 当前用户 ACL 保护的本地 Bearer token |
| `queue.db` | WAL 持久任务队列，不含论文全文 |
| `previews/` | 用户显式执行 `read` 产生的结果预览 |

## 分层诊断

1. `diagnose` 检查 Firefox 路径和代理配置。
2. 代理开启时先直连代理 TCP 端口。
3. 通过配置的 requests/PySocks transport 读取 OIDC discovery 和 JWKS。
4. 检查已保存会话及 `chatgpt.tokens.use.direct` scope。
5. `diagnose --models` 请求账号模型目录。
6. `diagnose --inference` 发起最小 SSE 请求，并等待 `response.completed`。

`socks5h`/`socks4a` 会把域名交给代理解析。测试套件使用伪 SOCKS 服务检查握手中的 ATYP/目标地址，而不是仅检查环境变量。

## 服务不可达

```powershell
Invoke-RestMethod http://127.0.0.1:23120/health
```

应返回 `status=ok`。23119 是 Zotero Connector 端口，不应使用。如修改 23120，运行：

```powershell
.\dist\ZoteroQuickRead.exe configure --port <新端口>
```

随后在 Zotero 右键菜单 → AI 速读 → 设置中填入同一地址。

## 重新授权

```powershell
.\dist\ZoteroQuickRead.exe auth-status
.\dist\ZoteroQuickRead.exe auth-login
```

终止型 refresh 错误会保留 issued client ID 但要求重新登录。普通网络错误不会删除凭据。

如需退出并撤销 refresh token：

```powershell
.\dist\ZoteroQuickRead.exe auth-logout
```

撤销请求也使用配置的 SOCKS；网络失败时本地凭据不会被误删。

## PDF 问题

- 尚未下载或仍在写入：`waiting_fulltext`，稍后重新检查。
- 扫描件/文本过少：`scanned_or_empty_pdf`，需要 OCR。
- 加密：`encrypted_pdf`，需要先解密。
- 过大/分段数超限：`pdf_too_large` 或 `document_too_large`，不会静默截断。
- 模型不支持输入图像：服务端返回 unsupported capability；可执行 `configure --page-images off` 后明确降级为文本模式。

## Zotero 插件问题

- 确认 Zotero 是 9.0.6–9.0.x。
- 重新安装 `dist/zotero-quick-read-0.1.0.xpi`。
- 后台先运行，插件再连接。
- 插件 token 默认自动读取；如 401，执行 `token-path` 检查文件位置，再在插件设置中重新载入或手工输入。
- 只读组库不会写入笔记或标签，并显示明确错误。
