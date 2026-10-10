zqr-menu-root =
    .label = Zotero 论文 AI 速读
zqr-menu-generate =
    .label = 生成 AI 速读
zqr-menu-regenerate =
    .label = 重新生成
zqr-menu-status =
    .label = 查看状态
zqr-menu-settings =
    .label = 设置
zqr-menu-auto =
    .label = 自动处理：{ $enabled ->
        [true] 开启
       *[false] 关闭
    }

zqr-dialog-title = Zotero 论文 AI 速读
zqr-settings-url = 本地后台地址（仅允许 HTTP 回环地址）：
zqr-settings-token = 后台 Bearer Token。留空将保留当前值；也可勾选清除已保存 Token。
zqr-settings-clear-token = 清除首选项中的 Token（仍会尝试 token 文件）
zqr-settings-saved = 设置已保存。
zqr-settings-connected = 后台连接与认证均正常。
zqr-settings-health-only = 后台可连接，但认证失败：{ $error }
zqr-settings-invalid-url = 地址无效。仅允许 http://127.0.0.1:端口，且不能包含路径、账号或查询参数。
zqr-settings-invalid-token = Token 不能为空白字符、包含换行，且长度不能超过 4096 个字符。

zqr-notify-submitted = 已提交 { $count } 个速读任务。
zqr-notify-no-ready-pdf = 所选条目中没有已下载完成、具有普通父条目的可读 PDF。
zqr-notify-token-missing = 未找到后台 Token。请在“设置”中填写，或写入 %LOCALAPPDATA%\ZoteroQuickRead\plugin-token。
zqr-notify-backend-error = 本地后台请求失败：{ $error }
zqr-notify-applied = AI 速读已写入“{ $title }”。
zqr-notify-manual-edit = 检测到原 AI 笔记已被人工修改，已保留原文并创建新的速读笔记。
zqr-notify-auto-on = 已开启自动处理；只监听此后新增或变化的就绪 PDF，不扫描历史条目。
zqr-notify-auto-off = 已关闭自动处理。

zqr-status-title = Zotero 论文 AI 速读状态
zqr-status-empty = 所选论文尚无速读任务。
zqr-status-line = { $title }：{ $status }
zqr-status-detail = { $message }
zqr-status-waiting-fulltext = 等待 PDF 下载完成
zqr-status-queued = 已排队
zqr-status-processing = 正在生成
zqr-status-completed = 已完成，等待写回
zqr-status-applied = 已完成并写回 Zotero
zqr-status-failed = 失败
zqr-status-waiting-quota = 等待配额恢复
zqr-status-reauthorization-required = 需要在后台重新授权
zqr-status-cancelled = 已取消
zqr-status-unknown = 未知状态
zqr-status-backend-unavailable = 无法刷新后台状态，以下为本地缓存：{ $error }
