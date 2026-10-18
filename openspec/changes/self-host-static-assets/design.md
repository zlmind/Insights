# Design: self-host-static-assets

## Context

现状：`base.html`、`login.html`、`signup.html` 从 `cdn.jsdelivr.net` 加载 Bootstrap 5.3.0 CSS 与 bundle JS；`base.html`、`article.html`、`article_edit.html` 从同源加载 `marked`（unpinned 最新版）。实测 jsdelivr 当前从部署环境直连不可达（连接重置），五个国内镜像（unpkg/cdnjs/bootcdn/npmmirror/staticfile）均可达。应用本地静态目录 `static/` 已由 Flask 正常服务（i18n.js、logo 等先例）。

## Goals / Non-Goals

**Goals:**

- 三个库文件落地 `static/vendor/`，随仓库入库，模板经 `url_for('static', ...)` 引用。
- 断网/jsdelivr 不可达时页面样式与交互完整。

**Non-Goals:**

- 不升级 Bootstrap（保持 5.3.0，与现有样式零差异）。
- 不加 SRI、不做多 CDN fallback、不引入前端构建步骤。
- 不自托管 Google Fonts（纯增强型资源；缺失仅字体退化，不破坏布局）。

## Decisions

1. **自托管而非换 CDN 域名**：换域名只是把风险从 jsdelivr 挪到另一个第三方；本地应用自托管后运行时零外网依赖，一次下载永久有效。镜像仅用于本次下载取件（选 npmmirror，国内最快 0.2s）。
2. **marked 固定下载时点版本**：原引用 unpinned 最新版本就随时漂移；落地后版本固化、行为可预期。下载时记录版本号于文件旁（vendor 目录内一个 README 或文件头注释不强加——保持文件与上游字节一致，版本号记录在 design/tasks 与提交信息中）。
3. **引用改法**：base/login/signup 的 `<link>`/`<script src>` 换 `{{ url_for('static', filename='vendor/...') }}`；article 两页的 marked 同理。与模板内既有 `url_for('static', ...)` 用法一致。
4. **vendor 文件保持上游原样**：不压缩、不魔改，便于日后对照升级。

## Risks / Trade-offs

- 仓库体积 +约 350KB（三个未压缩 vendor 文件）——可接受，换来离线可用。
- marked 版本固化为下载时点版本（当前 latest），markdown 渲染行为可能与「每次都取 latest」有细微差异——固定版本本就是改善。
- Bootstrap 5.3.0 若日后需升级，需手动替换 vendor 文件——频率极低。
