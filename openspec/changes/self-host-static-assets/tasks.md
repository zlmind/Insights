# Tasks: self-host-static-assets

## 1. Vendor 资源落地

- [x] 1.1 下载 bootstrap.min.css（5.3.0）、bootstrap.bundle.min.js（5.3.0）、marked.min.js（记录下载时点版本号）到 `static/vendor/`，保持上游原样字节；验证：三个文件存在且与源镜像字节一致（大小/哈希对照）

## 2. 模板引用切换

- [x] 2.1 `base.html`、`login.html`、`signup.html` 的 Bootstrap CSS/JS 引用改为 `url_for('static', filename='vendor/...')`；验证：渲染出的 HTML 中 bootstrap 引用指向 `/static/vendor/` 且 HTTP 200
- [x] 2.2 `base.html`、`article.html`、`article_edit.html` 的 marked 引用改本地路径；验证：article 页 markdown 预览功能正常

## 3. 验收

- [x] 3.1 `grep -r 'cdn.jsdelivr.net' templates/` 零命中（Bootstrap 与 marked 均无残留）；模拟离线环境下登录页与主页样式完整、下拉/弹层/折叠可交互
