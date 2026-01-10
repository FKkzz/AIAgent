# AIAgent：AI 驱动的 Zotero PDF自动处理插件

本项目为一个与 AI 代理集成的 Zotero 插件，可在将 PDF 导入 Zotero 时，或者为 Zotero 库中已有的 PDF 文档生成摘要和添加关键词 tag。

本项目的构建基于 Zotero 插件的[开发模板](https://github.com/windingwind/zotero-plugin-template/)，利用 AI 辅助编程完成。

## 工作原理

本项目在本地端部署了 Python 程序，利用 PyMuPDF 包进行 PDF 文档的文字提取，随后利用 api 调用 llm 模型进行摘要生成或关键词处理。该程序也可以单独运行。

为方便使用，进一步将 Python 程序与 Zotero 插件集成，插件通过调用 Python 代理的 HTTP 端口运行上述程序。当用户在 Zotero 中触发 AI 处理功能时，插件会将 PDF 文件发送到本地运行的 Python 代理服务器，由代理服务器调用 AI 模型生成摘要和关键词，然后将结果返回给 Zotero 插件并保存到相应的文献条目中。插件将识别 item 类型，仅在类型为 Journal Article 时触发调用，避免在书籍等类型的条目上错误耗费大量资源。

## 项目内容
- `ai-agent.xpi` - 打包后的 Zotero 插件，可直接在 Zotero 中安装
- `ai_agent_local/` - 部署在本地的 AI 处理 PDF 的 Python 程序，通过 HTTP 端口与 Zotero 插件通信
- `ai_agent_zotero/` - Zotero 插件源代码
- `basic_agent.py` -可独立使用的 agent 示例，未与插件集成


## 系统要求

- Zotero 7.0 或更高版本
- Python 3.8 或更高版本


## 环境依赖

详见`ai_agent_local/requirement.txt`文档。
- __flask__ - 用于创建 Web 服务器和处理 HTTP 请求

- __PyMuPDF__ (fitz) - 用于提取 PDF 文档中的文本内容

- __openai__ - 用于与 AI 模型进行交互

- __python-dotenv__ (dotenv) - 用于加载环境变量

另外，若希望修改插件源代码，还需要 __Node.js__ 和 __npm__ 用于将源码打包成可用插件


## 使用方法

用户需要在本地部署一个 agent.py 的环境，运行后再启动 Zotero：

1. 首先启动 Python 代理服务器
2. 然后启动 Zotero 并安装插件
3. 在 Zotero 中配置插件设置
4. 使用插件处理 PDF 文档

## 安装

### Python 代理设置

1. 进入本地的 `ai_agent_local/` 目录：
   ```bash
   cd path/to/ai_agent_local
   ```

2. 安装 Python 依赖：
   ```bash
   pip install -r requirements.txt
   ```

3. 运行 AI 代理服务器：
   ```bash
   python agent.py
   ```
   
   代理将在 `https://127.0.0.1:3333` 上启动本地 HTTPS 服务器（默认）。

### Zotero 插件安装

1. 下载项目提供的 `ai_agent.xpi` 或在 `ai_agent_zotero/.scaffold/build` 下找到源代码打包的`.xpi`文件
2. 在 Zotero 中，转到 `工具` → `插件` → 点击齿轮图标 → `从文件安装插件...`
3. 选择 `ai-agent.xpi` 文件
4. （可选）重启 Zotero

## 配置

1. 安装插件后，进入 `工具` → `插件首选项` → `AIAgent`
2. 配置以下选项：
   - 启用 AI 摘要：为 PDF 生成 AI 摘要
   - 启用 AI 关键词：为 PDF 生成 AI 关键词
   - 启用摘要+关键词：同时生成摘要和关键词
   - 代理 URL：设置 AI 代理的 URL，与本地的`agent.py` Python 程序设置需一致 （默认：`https://127.0.0.1:3333`）

## 使用方法

### 处理 PDF

有几种方法可以使用 AI 处理 PDF：

1. **右键上下文菜单**：在 Zotero 中右键单击 PDF 附件，选择"AI Process PDF"以生成摘要和/或关键词
2. **拖放**：将 PDF 文件直接拖放到 Zotero 中，如果已配置，它们将自动处理
3. **批量处理**：选择多个 PDF 项目并使用右键菜单批量处理

### 功能

- **AI 摘要**：自动生成 PDF 文档的简洁摘要
- **AI 关键词**：从 PDF 文档中提取相关关键词
- **进度指示器**：弹窗显示处理状态的进度通知或错误通知
- **多语言支持**：支持中英文界面


## 故障排除

- 在尝试处理 PDF 之前，请确保 Python 代理正在运行
- 验证插件设置中的代理 URL 与代理运行的 URL 匹配
- 检查 PDF 文件是否可访问且未损坏
- 确保系统满足插件和 AI 代理的要求

## 开发

要修改和重新构建插件：

1. 进入 `ai-agent/` 目录
2. 安装依赖：`npm install`
3. 对源代码进行更改
4. 构建插件：`npm run build`
5. 新的 `.xpi` 文件将在 `.scaffold/build/` 中创建

或者也可通过`npm run start`，在不构建`.xpi`文件的情况下进行即时修改，详见 Zotero 模板编辑的[官方文档](https://github.com/windingwind/zotero-plugin-template/)

对于 AI 生成摘要和关键词的细节要求可以通过修改本地`agent.py`中的 prompt 进行，不需要重修构建插件，提供了相对便捷的修改方案。

