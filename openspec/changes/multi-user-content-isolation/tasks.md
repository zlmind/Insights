# Tasks: multi-user-content-isolation

## 1. 数据层迁移

- [x] 1.1 内容域数据表 in-place 加 `user_id` 列（feeds、episodes、articles、social_posts、standalone_posts、url_sources、uploaded_images、generated_thumbnails、prompt_library、content_brief_runs、scheduled_posts；content_briefs 列已存在；幂等，沿用 `PRAGMA table_info` 检测）；episodes 的 url 全局唯一索引改为 (url, user_id) 复合唯一；无回填、无标志位
- [x] 1.2 存量数据不迁移（2026-10-17 改定）：旧数据 NULL 归属对所有用户不可见（含 admin），数据保留在库中，可后续手工 SQL 划归或清理；核查无任何代码路径自动为 NULL 归属赋值

## 2. 数据层作用域

- [ ] 2.1 feeds/episodes：list 按 user 过滤，get/update/delete 归属校验（不匹配按不存在处理）
- [ ] 2.2 articles/social_posts/standalone_posts：同上（含 bulk 操作的归属限定）
- [ ] 2.3 url_sources/uploaded_images/generated_thumbnails/prompt 库与最近 prompts：同上
- [ ] 2.4 content_briefs/content_brief_runs：补齐查询过滤（列已存在）；scheduled_posts：归属 + 查询过滤
- [ ] 2.5 写入口强制归属：内容域 create/import/enqueue 函数签名要求 user_id，缺失即失败

## 3. Web 层作用域

- [ ] 3.1 源与文章路由（/、/feed/*、/articles、/article/*、episode 操作）按 current_user 过滤与校验，跨用户 404
- [ ] 3.2 发布工作台路由（/compose 及其全部子接口）按 current_user 过滤与校验，跨用户 404
- [ ] 3.3 briefs 与 schedule 路由按 current_user 过滤与校验，跨用户 404
- [ ] 3.4 用量/活动页「全部」视角仅 admin 可用，非 admin 请求按"我的"处理

## 4. 后台与通道

- [ ] 4.1 episode 处理 / content_agent 跑批 / YouTube 频道回填 worker：按行上归属执行，产物写归属
- [ ] 4.2 brief 定时跑批：查询过滤补齐，跑批产物归属 brief 的 user_id（沿用现有用量归属链路）

## 5. 验证与门禁

- [ ] 5.1 gate 脚本：扫描内容表 SELECT 是否带 user 过滤、内容域 INSERT 是否带归属列，并入 run_all_gates.py
- [ ] 5.2 端到端验收：双账号（admin + 新账号）对照——新账号全页空列表、直访他人资源 404、双账号互不可见、存量 NULL 数据对双方均不可见、后台产物归属正确
