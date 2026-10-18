# Task 2 报告：数据层 feeds/episodes 作用域

- **Change:** multi-user-content-isolation
- **Commit:** `26bee2d` 数据层：feeds/episodes 按 user 作用域，写入口强制归属（仅 `database.py`，141 insertions / 81 deletions）
- **基线:** 8f043d6（Task 0/1 已完成）
- **TDD:** 按要求执行 RED → GREEN，验证脚本来自 brief Step 2，逐字使用。

## 实现内容

1. **守卫**：`_require_user(user_id) -> int` 定义于 feeds 区块之前（`get_feed` 上方），docstring 与校验逻辑与 brief 逐字一致。Task 3-6 直接复用此位置与签名。
2. **feeds 组（7 函数）**：
   - `add_feed(url, title, user_id, db_path=...)`：INSERT OR IGNORE 保留，列清单加 `user_id`，回查改 `WHERE url = ? AND user_id = ?`（复合唯一键语义落地）。
   - `get_feed(url, user_id, ...)`、`get_feed_by_id(feed_id, user_id, ...)`、`list_feeds(user_id, ...)`：读路径加 `AND user_id = ?` / `WHERE user_id = ?`。
   - `delete_feed(feed_id, user_id, ...)`、`delete_feeds_bulk(feed_ids, user_id, ...)`：三段 DELETE 全部限定——articles 段改为 `episode_id IN (SELECT id FROM episodes WHERE feed_id ... AND user_id = ?)` 子查询限定，防止借他人 episode_id 越权删其 articles。
   - `update_feed_metadata(feed_id, feed_type, last_post, item_count, user_id, ...)`：user_id 置于 item_count 之后、db_path 之前，`UPDATE ... WHERE id = ? AND user_id = ?`。
3. **episodes 组（10 函数）**：
   - `save_episode(..., channel, user_id, ...)`：INSERT OR REPLACE 列清单加 `user_id`（11 列 11 占位符）。
   - `queue_episode(..., published, user_id, ...)`：INSERT OR IGNORE 列清单加 `user_id`（同 URL 双用户各一行，靠 Task 1 复合唯一键）。
   - `get_episode` / `update_episode_status` / `get_episode_by_id` / `delete_episode_by_id` / `delete_episodes_bulk` / `reset_episode_for_reprocess` / `list_episodes` / `list_all_episodes`：读/写/删路径全部加归属谓词。`delete_episode_by_id`/`delete_episodes_bulk` 的 articles 段同样改为子查询限定归属（原实现按 episode_id 直接删 articles，存在越权删除他人文章的口子，已堵）。
   - `list_all_episodes(order_by="id", user_id, ...)`：`user_id = ?` 过滤；`order_by` 白名单注入防护保留。
4. **白名单未动**：`get_youtube_episodes_missing_channel`、`set_episode_channel` 保持无 user 过滤（brief 注明的后台元数据回填）。
5. **未动 Task 1 迁移代码**；未改 database.py 以外任何文件。

## TDD Evidence

**RED**（实现前，brief Step 2 验证脚本原样运行）：

```
$ venv/Scripts/python.exe -c "<brief Step 2 脚本>"
Traceback (most recent call last):
  File "<string>", line 6, in <module>
TypeError: add_feed() got multiple values for argument 'db_path'
```

失败原因符合预期：`add_feed` 旧签名 `(url, title, db_path=DB_PATH)`，第 3 个位置参数 `a` 落到 `db_path` 上与关键字冲突——新签名未实现的特征性失败。

**GREEN**（实现后，同一脚本）：

```
$ venv/Scripts/python.exe -c "<brief Step 2 脚本>"
FEEDS_EPISODES_SCOPING_OK
```

脚本覆盖的归属语义：None user 被拒（ValueError）、双用户同 URL feed 各一行、跨用户 `get_feed_by_id` 返回 None、episode 跨用户不可见、同 URL 第二用户不去重、`list_all_episodes(user_id=b)` 仅 1 行、A 的 `update_episode_status` 不触及 B 的行。

## Step 2 / Step 3 输出

- Step 2：`FEEDS_EPISODES_SCOPING_OK`（临时库 tempfile，未触碰 insights.db）。
- Step 3 冒烟：`venv/Scripts/python.exe -c "import database"` → `import database OK`。
- Gates 参考（信息性，非本任务验收项）：`run_all_gates.py` 60/79 失败，grep 全量输出无一条 `user_id`/positional/signature 类错误——全部为并行会话删除 accounts/schedule/social_publisher 的既有遗留，与本任务无关。

## 变更函数清单（19 + 1 守卫）

| 函数 | 归属谓词落法 |
|---|---|
| `_require_user` | 新增守卫（None → ValueError，返回 int） |
| `add_feed` | INSERT 列加 user_id；回查 `url+user_id` |
| `get_feed` / `get_feed_by_id` / `list_feeds` | SELECT 加 `AND user_id = ?` / `WHERE user_id = ?` |
| `delete_feed` / `delete_feeds_bulk` | 三段 DELETE 各限定，articles 段子查询限定 |
| `update_feed_metadata` | UPDATE 加 `AND user_id = ?` |
| `save_episode` | INSERT OR REPLACE 列加 user_id |
| `queue_episode` | INSERT OR IGNORE 列加 user_id |
| `get_episode` / `get_episode_by_id` / `list_episodes` / `list_all_episodes` | SELECT 加归属谓词 |
| `update_episode_status` / `reset_episode_for_reprocess` | UPDATE 加 `AND user_id = ?` |
| `delete_episode_by_id` / `delete_episodes_bulk` | episodes DELETE 加谓词；articles 段子查询限定 |
| （白名单）`get_youtube_episodes_missing_channel` / `set_episode_channel` | 未改动 |

## 自审与偏差说明

1. **`user_id` 参数形态偏差（3 函数）**：`save_episode`/`queue_episode`/`list_all_episodes` 的 `user_id` 落为 `Optional[int] = None`，而非 brief 字面的无默认必填 int。原因：Python 规定非默认参数不能跟在默认参数后，且实测 CPython 3.10 中 PEP 570 的 `/` 也救不了（`def f(a=1, /, b)` 仍 SyntaxError，本次用最小实验证实）。运行时由函数体首行 `_require_user(user_id)` 强制——brief 守卫的语义本就是「missing = None = caller bug → ValueError」，且 brief 自己的验证脚本对 `add_feed` 的必填检验方式就是传 `None` 断言 ValueError。前面无默认参数的函数（`add_feed`、`get_feed` 等 16 个）保持语法层必填。RED/GREEN 均用 brief 脚本逐字跑通，调用形态（`queue_episode(url, title, feed_id, None, user_id位置传)`、`list_all_episodes(user_id=关键字)`）与 brief 脚本一致。
2. **`_require_user` 副作用**：`int(user_id)` 会把 `"3"`、`3.0` 等强转；SQLite 绑定下合法值本就是 int，无实际风险。
3. **越权删除口子顺手堵上**：`delete_episode_by_id`/`delete_episodes_bulk` 的 articles 段原本按 episode_id 直接删，若只给 episodes 段加谓词会留下「删他人 articles」的越权路径，故两处都改为子查询限定。属归属语义的正确实现，非范围蔓延。

## 顾虑

- **调用点计划内断裂**：`insights_web.py`（`list_feeds()`、`get_feed_by_id(feed_id)`、`save_episode(..., channel=channel)` 等约 15 处）与 `research_engine.py:407`（`list_all_episodes("published")`）未改，本任务期间旧调用会 TypeError/ValueError——Task 7-13 的范围，范围说明已允许。
- gates 60/79 失败为既有遗留（并行会话删除 accounts/schedule/social_publisher），非本任务引入；建议批次 C 后由调用点任务收敛时一并复核。
