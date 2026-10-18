# Task 5 报告：数据层 url_sources/uploaded_images/prompt_library/generated_thumbnails 作用域

**Status:** DONE
**Commit:** `9c83f21` 数据层：url_sources/上传图/prompt 库/生成封面按 user 作用域（仅 `database.py`，+150/-95）

## 改动内容（全部在 D:\git3\Insights\database.py）

四组共 21 个函数按 brief Interfaces 加 `user_id` 作用域，全部走既有 `_require_user(user_id)` 守卫（复用，未重定义）：

### url_sources（7 个）
- `add_url_source(url, title, description, content, user_id: int, og_image=None, db_path=...)`：user_id 落在 content 后、og_image 前，字面必填。既有 check-then-insert 逻辑保留；check SELECT 改 `WHERE url = ? AND user_id = ?`，UPDATE 分支补 `AND user_id = ?`（防御性，两处 SQL 各加归属），INSERT 列清单加 `user_id`。同 URL 双用户各一行由既有复合唯一索引 `idx_url_sources_url_user(url, user_id)` 兜底（Task 1 迁移已建，未动 schema）。
- `list_url_sources(user_id, db_path=...)`、`get_url_source(source_id, user_id, ...)`、`get_url_source_by_url(url, user_id, ...)`、`delete_url_source(source_id, user_id, ...)`、`update_url_source_last_used(source_id, user_id, ...)`、`update_url_source_content(source_id, user_id, title, description, content, og_image=None, ...)`（user_id 提到 title 前）——全部 `WHERE ... AND user_id = ?`。

### uploaded_images（4 个）
- `add_uploaded_image(filename, url, storage, user_id: int, size=0, media_type='image', db_path=...)`：user_id 落在 storage 后。按 URL 去重改 `WHERE url = ? AND user_id = ?`；INSERT 列清单加 `user_id`。`uploaded_images.url` 的全局 UNIQUE 索引**有意保留**（迁移设计如此，未动 schema；列注释已说明文件名为 UUID，全局唯一是兜底）。
- `list_uploaded_images(user_id, ...)`、`delete_uploaded_image(image_id, user_id, ...)`、`get_uploaded_image(image_id, user_id, ...)`。

### prompt_library（5 个）
- `add_library_prompt(title, content, user_id: int, db_path=...)`：INSERT 列清单加 `user_id`。
- `list_library_prompts(user_id, ...)`、`get_library_prompt(prompt_id, user_id, ...)`、`update_library_prompt(prompt_id, user_id, title, content, ...)`（user_id 提到 title 前）、`delete_library_prompt(prompt_id, user_id, ...)`。

### generated_thumbnails（4 个）
- `add_generated_thumbnail(youtube_url, video_id, title, channel, aspect, style, prompt, image_relpath, user_id: int, db_path=...)`：user_id 落在 image_relpath 后；INSERT 列清单加 `user_id`。
- `list_generated_thumbnails(user_id, limit=50, offset=0, ...)`、`get_generated_thumbnail(thumbnail_id, user_id, ...)`、`delete_generated_thumbnail(thumbnail_id, user_id, ...)`。

### 签名约定说明
21 个函数的新签名中 user_id 均位于任何带默认值参数之前，语法上全部允许字面必填，因此**没有产生 Optional=None 落位**；全部为 `user_id: int` 必填 + `_require_user` 守卫（与 Task 2-4 的 `get_feed`/`add_social_post` 同款惯例）。

## TDD 过程

### RED（实现前，特征性失败）
命令（brief Step 2 脚本原文）：

```bash
venv/Scripts/python.exe -c "
import tempfile, os, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p)
a = database.create_user('a', 'x', db_path=p); b = database.create_user('b', 'x', db_path=p)
sa = database.add_url_source('http://s', 'T', 'D', 'C', a, db_path=p)
sb = database.add_url_source('http://s', 'T', 'D', 'C', b, db_path=p)
assert sa != sb and len(database.list_url_sources(a, db_path=p)) == 1
...
"
```

失败摘要（临时库，未碰 insights.db）：

```
Traceback (most recent call last):
  File "<string>", line 8, in <module>
AssertionError
EXIT=1
```

失败原因正确：旧签名下第二用户同 URL 的 `add_url_source` 命中无 user 作用域的 check-then-insert，返回与第一用户相同的行 id（`sa == sb`），正是待实现的作用域缺失特征。

### GREEN（实现后）
同一命令重跑：

```
MISC_CONTENT_SCOPING_OK
EXIT=0
```

覆盖点：同 URL 双用户各一行、list 按用户过滤、get_url_source_by_url 跨用户取对行、delete_url_source 跨用户拒绝、get_uploaded_image 跨用户为 None、update_library_prompt 跨用户 rowcount=0、delete_generated_thumbnail 跨用户 False。

### Step 3 冒烟
`venv/Scripts/python.exe -c "import database"` → EXIT=0。

## 既有 gate 影响（仅记录，未改——调用方属后续任务）

- `scripts/check_manual_posts.py` 的 route/wiring/targets/video 子命令**在本任务之前就失败**，失败点在 `insights_web.py:3024/4105` 对 Task 4 函数 `list_standalone_posts()`/`add_standalone_post()` 的调用（缺 user_id 实参），与本次改动无关；llm 子命令通过（`LLM_ADAPTERS_OK`）。
- 同类调用方缺口（本任务波及、待后续调用方任务修）：
  - `research_engine.py:328/357/385`（`get_url_source_by_url`/`add_url_source`/`list_url_sources`）
  - `scripts/check_manual_posts.py:197`（`add_url_source`）
  - `insights_web.py`（import 并调用 uploaded_images/prompt_library/generated_thumbnails 全部函数）
- 本任务未动 Task 1-4 已完成代码；未动 schema；工作区他人未暂存改动未纳入提交（`git add database.py` 精确暂存）。

## 提交核验
- `git log -1`：`9c83f21 l0lsec <51931886+l0lsec@users.noreply.github.com>`，无任何 attribution trailer。
- `git show --stat HEAD`：仅 `database.py` 一个文件。
