# Proposal: self-host-static-assets

## Why

全站前端关键资源（Bootstrap CSS/JS、marked.js）从 `cdn.jsdelivr.net` 加载。jsdelivr 在当前网络环境间歇性不可达（实测连接被重置），一旦失败：Bootstrap CSS 缺失导致全站布局塌陷（"样式错乱"），Bootstrap JS 缺失导致下拉/弹层/折叠全部失效、按钮退化为浏览器默认灰色不可交互（"灰色点击不了"）。本应用是本地工具，运行时不应依赖境外公网 CDN。

## What Changes

- 新增 `static/vendor/` 目录，落地三个固定的第三方库文件：`bootstrap.min.css`（5.3.0）、`bootstrap.bundle.min.js`（5.3.0）、`marked.min.js`（下载时固定版本）。
- 5 个模板的 CDN 引用改为本地 `url_for('static', ...)` 路径：`base.html`（3 处）、`login.html`、`signup.html`（bootstrap CSS）、`article.html`、`article_edit.html`（marked）。
- Google Fonts 外链保持不变（当前可达；字体缺失只降级字体，不破坏布局与交互）。

## Capabilities

### New Capabilities

- `frontend-vendor-assets`: 前端关键第三方资源由本地 `static/vendor/` 提供，页面在无外网/CDN 不可达环境下样式与交互完整可用。

### Modified Capabilities

（无）

## Impact

- 模板：`templates/base.html`、`templates/login.html`、`templates/signup.html`、`templates/article.html`、`templates/article_edit.html`。
- 静态资源：新增 `static/vendor/`（约 230KB CSS + 80KB JS + 40KB JS，入库）。
- 无 Python 代码、数据库、API 变更；无破坏性变更。
- 风险：marked 由 unpinned 最新版改为固定版本（下载时点版本），markdown 渲染行为以落地版本为准。
