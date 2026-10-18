# Brainstorm Summary

- Change: multi-user-content-isolation
- Date: 2026-10-17

## 确认的技术方案

1. **数据模型**：12 张内容表（feeds、episodes、articles、social_posts、standalone_posts、url_sources、uploaded_images、generated_thumbnails、prompt_library、scheduled_posts、content_briefs*、content_brief_runs）加可空 `user_id`；启动时幂等加列，**无回填、无标志位**（*content_briefs 列已存在）。episodes 全局 URL 唯一键改为 `(url, user_id)` 复合。recent prompts 从 standalone_posts 派生，随帖子自动隔离。
2. **作用域下沉（第一道防线）**：`database.py` 内容域 `list_*` 必带 user 过滤；`get_/update_/delete_` 归属校验，不匹配按不存在处理；写入口（create/import/enqueue）签名强制归属，缺失抛错。
3. **Web 层**：~60 条内容路由从 `current_user()` 取归属；跨用户直访 404（不泄露存在性）。
4. **后台归属**：episode 队列行创建时写触发者；worker 按行上归属干活；brief 跑批沿用 `brief["user_id"]` 链路，产物（drafts/articles）写 brief 主人。
5. **用量/活动页**：非 admin 固定自己的视角（忽略 user_id 参数），「全部」仅 admin 渲染。

## 关键取舍与风险

- **存量数据不迁移**（用户 2026-10-17 改定）：旧数据 NULL 归属 = 对所有人不可见（含 admin），保留在库中，可手工 SQL 划归或清理；usage/activity 历史记录不受影响。
- 查询遗漏风险 → gate 脚本扫描兜底 + 按表分批提交。
- worker 丢归属风险 → 写入口签名强制。
- 上传文件不迁移目录（UUID 文件名），归属以 DB 行为准。

## 测试策略

- gate 脚本：扫描内容域 SELECT 的 user 过滤与 INSERT 的归属列，并入 `run_all_gates.py`。
- Flask test client 双账号 E2E gate：新账号全页空、直访他人资源 404、互不可见、后台产物归属正确。

## Spec Patch（回写 delta spec）

- 「存量数据一次性划归第一个账号」requirement → 「存量内容数据保留但不再对任何用户可见」+ 边界：数据不删除、可手工划归；usage/activity 历史不受影响。
- 补场景：非 admin 活动/用量固定自己视角（现状 `/activity` 无门控且接受任意 user_id 筛选，已代码核实）。

## 状态

用户已于 2026-10-17 确认技术方案（AskUserQuestion：确认，创建 Design Doc）。
