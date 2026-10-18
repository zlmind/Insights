# Task 4 报告：数据层 standalone_posts 与 recent prompts 作用域

**Status:** DONE
**Commit:** `aba9732` 数据层：standalone_posts 与 recent prompts 按 user 作用域（仅 `database.py`，141 insertions / 88 deletions；作者 l0lsec no-reply，无任何 attribution）
**分支:** main

## TDD 过程

### RED（先失败）

命令（brief Step 2 原文）：

```bash
venv/Scripts/python.exe -c "
import tempfile, os, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p)
a = database.create_user('a', 'x', db_path=p); b = database.create_user('b', 'x', db_path=p)
pa = database.add_standalone_post('freeform', 'prompt1', 'wechat', '正文', a, db_path=p)
assert database.get_standalone_post(pa, b, db_path=p) is None
...
print('STANDALONE_SCOPING_OK')
"
```

失败摘要：

```
Traceback (most recent call last):
  File "<string>", line 7, in <module>
TypeError: get_standalone_post() got multiple values for argument 'db_path'
```

特征性失败确认：现签名缺 `user_id` 第二位置参数，`b` 撞上 `db_path` 具名参——正是本任务要消除的旧签名。临时库用 tempfile，未触碰 insights.db。

### GREEN（后通过）

改动 `database.py` 后重跑同一脚本，输出：

```
STANDALONE_SCOPING_OK
```

全部 9 个断言通过：跨用户 get/list/prompts/prompts_bulk/count 均隔离；B 的 `mark_standalone_post_used` 未改动 A 的行（`row['used'] == 0`）。

### 冒烟

```bash
venv/Scripts/python.exe -c "import database"   # → SMOKE_OK
```

## 实现内容（17 个函数，全部 SQL 显式 `user_id = ?`）

**standalone_posts（12 个）**
- `add_standalone_post(source_type, source_content, platform, content, user_id: int 必填, image_url=None, repost=False, db_path=..., ...)`：INSERT 列清单加 `user_id`；`_require_user` 后落库。
- `get_existing_standalone_content(user_id, db_path=...)`：import 去重按用户过滤。
- `list_standalone_posts(user_id, source_type=None, platform=None, limit=None, offset=None, db_path=...)`：**user_id 成为第一个位置参数**；conditions 初始即含 `user_id = ?`，永远生效。
- `get_standalone_post(post_id, user_id, ...)`、`list_standalone_posts_by_source_url(url, user_id, ...)`、`count_standalone_posts_by_source_urls(urls, user_id, ...)`：读路径全部加 `user_id = ?`（count 的参数元组改为 `(uid, *truncated_keys)`）。
- `update_standalone_post(post_id, user_id, content, ...)`、`update_standalone_post_image(post_id, image_url, user_id, ...)`、`update_standalone_post_video(post_id, video_url, user_id, ...)`、`delete_standalone_post(post_id, user_id, ...)`、`delete_standalone_posts_bulk(post_ids, user_id, ...)`（`(*post_ids, uid)`，与 Task 3 social_posts 同款）：写路径 WHERE 全部加 `AND user_id = ?`。
- `mark_standalone_post_used(post_id, used=True, user_id, ...)`。

**recent prompts（5 个，派生自 standalone_posts，无独立表）**
- `list_recent_prompts(user_id, limit=20, ...)`、`list_recent_image_prompts(user_id, limit=10, ...)`、`clear_recent_prompts(user_id, ...)`、`delete_prompt_by_content(prompt_content, user_id, ...)`、`delete_prompts_bulk(prompt_contents, user_id, ...)`：SQL 仍按 brief 要求显式带 `user_id = ?`。

## 签名约定说明

- 语法允许处 user_id 字面必填（add/list/get/delete/prompts 等绝大多数）。
- **例外一处**：`mark_standalone_post_used` 因 `used=True` 默认参在 user_id 之前（brief 亦如此指定），user_id 落 `Optional[int] = None` + `_require_user`（None 会抛 `ValueError: user_id is required for content operations`），docstring 已注明原因。Task 2/3 同款惯例。

## 备注 / 已知事项

1. **brief 行号偏移**：brief 写的 `database.py:2951-3072`（prompts 段）因 Task 2/3 的改动实际已漂移至约 3175-3296（现该区间是 URL Sources CRUD）。按符号名定位改的，五个 prompts 函数全部覆盖，未漏。
2. **insights_web 暂时不兼容属预期**：`list_standalone_posts` user_id 变第一位置参、其余函数加 user_id 后，web 层旧调用点会报错；brief 明确调用点在 Task 8/9 改，本任务冒烟只要求 `import database`（已过）。
3. `database.py` 内部无这 17 个函数的自调用点（已 grep 确认），改动自包含。
4. 未触碰 Task 1/2/3 已完成的代码；工作区中他人未暂存的 `.claude/skills/comet/scripts/comet-hook-guard.sh` 改动未纳入提交（精确 `git add database.py`）。
5. 对应 tasks.md：2.2（standalone_posts 部分）、2.3（recent prompts）、2.5。
