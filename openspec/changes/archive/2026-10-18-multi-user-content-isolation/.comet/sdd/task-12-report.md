# Task 12 报告：episode worker 与 feed 元数据刷新的归属

- **Status:** DONE
- **Commit:** `858b216` worker：episode 落库与状态更新按入队者归属，元数据刷新带 feed 主人（单文件 insights_web.py，16+/5-，作者 l0lsec no-reply，无 attribution）
- **对应 tasks.md:** 4.1（episode 部分）

## 现状核对（动手前）

按函数符号定位（brief 行号已漂移），发现前批已完成的部分**如实记录为已在位，未重复改动**：

| 项 | 状态 | 证据 |
|---|---|---|
| `_backfill_youtube_source(url, transcript, summary, user_id)` 必参 | 已在位（Task 7） | insights_web.py:1357，内部 `get_url_source_by_url(url, user_id)` + `update_url_source_content(id, user_id, ...)` |
| `refresh_feed_metadata(feed_id, feed_url, user_id)` 必参 | 已在位（Task 7） | insights_web.py:1718，内部 `update_feed_metadata(feed_id, feed_type, last_post_str, item_count, user_id)` |
| 全部 4 处调用点持 uid（index POST / view_feed / refresh_all_feeds worker / refresh_feed） | 已在位 | insights_web.py:1803、1898、1909、1935，均传 `uid` |
| DB 层新签名（Task 2） | 已在位 | database.py:980/990/1017/1039，均 `_require_user(user_id)`（None → ValueError） |
| 路由侧 `save_episode(..., user_id=uid)`（2145、2236） | 已在位（前批） | 归属已带 |
| `_backfill_youtube_channels_bg`（insights_web.py:6924，现 6924 行非 brief 的 6791） | 白名单，未动 | 全局元数据回填保持原样 |
| `_proactive_worker` | 未动（仅包裹 worker） | brief 要求"不变，仅包裹" |

**实际缺口只在 `worker()` 本体**（insights_web.py:1370）：对 Task 2 新签名仍是旧调用形态——
`update_episode_status(url, "processing")`（缺 user_id → TypeError）、两处 `save_episode`（缺 user_id → `_require_user` ValueError）、except 分支 `update_episode_status(url, "error")`（缺 user_id → TypeError，且在 except 内二次抛错会**杀死 worker 线程**），且无"item 无 user_id"防御。

## TDD 循环

### RED（断言先行验证失败于预期改动点）

1. Brief Step 2 原文脚本先行：

```
venv/Scripts/python.exe C:\Users\zhuilin\AppData\Local\Temp\task12_brief_step2.py
→ WORKER_ATTRIBUTION_OK（绿：Task 2 的 DB 层已在位，brief 脚本验证的是队列同步原语）
```

   按 brief 脚本全绿的处理规则，构造**真实 `worker()` 驱动**的缺失场景：隔离库 + 假 `requests.get/transcribe_audio/summarize_text/extract_action_items/write_results_json`（不入真网络），向 `web.task_queue` 投递 item + None 哨兵后直接调 `web.worker()`，断言四阶段：① happy path 落库归属入队者、他用户不可见；② 管道中途失败 → error 分支归属；③ 无 user_id 的 item 不产 NULL 行且线程存活；④ brief 原语复验。

```
venv/Scripts/python.exe C:\Users\zhuilin\AppData\Local\Temp\task12_worker_driver.py
→ TypeError: update_episode_status() missing 1 required positional argument: 'user_id'
   （insights_web.py:1386 processing 处首抛；except 分支 :1420 同因二次抛错）
```

   失败原因即缺失功能本身（worker 未按新签名传归属），非笔误——RED 命中预期改动点。

### GREEN（最小实现）

仅改 `worker()`（三处 Edit，见 commit diff）：

1. `update_episode_status(url, "processing", item['user_id'])`
2. YouTube 分支：`save_episode(..., channel=channel, user_id=item['user_id'])`；`_backfill_youtube_source(url, transcript, summary, user_id=item['user_id'])`（改关键字传参对齐 brief）
3. 非 YouTube 分支：`save_episode(..., user_id=item['user_id'])`
4. except 分支：`error_uid = item.get("user_id")`，非 None 时 `update_episode_status(url, "error", error_uid)`；None 时仅记 error 日志、**跳过置状态**（见下方偏差说明）

防御形态：item 缺 `user_id` 键 → 调用点 `item['user_id']` 抛 KeyError；键在但为 None（两处入队兜底 `_user['id'] if _user else None`）→ DB 层 `_require_user` 抛 ValueError——两条路都"抛错进 except 分支"，不产 NULL 行。

```
venv/Scripts/python.exe C:\Users\zhuilin\AppData\Local\Temp\task12_worker_driver.py
→ 四阶段全过，WORKER_ATTRIBUTION_OK
venv/Scripts/python.exe C:\Users\zhuilin\AppData\Local\Temp\task12_brief_step2.py
→ WORKER_ATTRIBUTION_OK
venv/Scripts/python.exe -c "import insights_web" → IMPORT_OK（冒烟）
```

## 有意的偏差（对 brief 字面的一处偏离，需评审知晓）

brief 字面要求 error 分支写 `update_episode_status(url, s, item['user_id'])`。若照写，item 无归属时（键缺失 → KeyError；键为 None → `_require_user` ValueError）异常会在 **except 块内**再次抛出，穿透 `while True` **杀死 worker 线程**，此后所有 episode 永不处理直至重启。故 error 分支改为 `item.get("user_id")` + None 守卫跳过：有归属时与字面完全等价；无归属时仅记日志。brief 的硬性要求"不产 NULL"两条路都满足（`update_episode_status` 对 None 本就在 SQL 前抛错）。RED 阶段已实证该风险真实存在（:1420 二次 TypeError 即线程死亡路径）。

## 套件状态（按 TDD 要求如实列名）

`scripts/run_all_gates.py`：**66/79 失败，全部为预先存在**，与本任务无关——失败因均为：

- `AttributeError: module 'database' has no attribute 'save_instagram_token'`（database.py 中该函数已不存在，`grep -c "def save_instagram_token"` = 0）
- `social_publisher.py` 文件已删除（`install_fake_clients` 里 publisher 为 None）——MEMORY「并行会话删了 accounts/schedule/social_publisher 致 gate 失效」
- 旧签名门禁脚本（如 `check_manual_posts.py route/wiring`：`get_standalone_post()/add_url_source() missing 1 required positional argument: 'user_id'`）——多用户迁移中旧门禁未跟新，属后续任务/verify 阶段范畴

归属最近邻门禁 `check_usage.py scoping` → **USAGE_SCOPING_OK**（含 user_context 后台归属）。我的 diff 仅 `worker()` 函数体内 3 个 hunk，不触及上述任何失败路径。

## 其他

- 验证脚本（临时目录，非仓库文件）：`C:\Users\zhuilin\AppData\Local\Temp\task12_brief_step2.py`、`C:\Users\zhuilin\AppData\Local\Temp\task12_worker_driver.py`
- 提交前确认 insights_web.py 的未暂存 diff 只含本任务 3 个 hunk（16+/5-），未夹带并行会话改动；`git add insights_web.py` 精确单文件
- HEAD 在任务期间从 7ea49e5 前移到 860cab4（并行会话提交），无冲突
