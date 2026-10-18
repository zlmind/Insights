# Task 3 报告：数据层 articles/social_posts 作用域

- **Status:** DONE
- **Commit:** f3e5404 「数据层：articles/social_posts 按 user 作用域，批量替换限定归属」（仅 database.py，111+/65-，无 attribution）
- **对应 tasks.md:** 2.2（articles/social_posts 部分）、2.5

## TDD 过程

### RED

命令（brief Step 2 原文，临时库 tempfile，未碰 insights.db）：

```bash
venv/Scripts/python.exe -c "
import tempfile, os, database
p = os.path.join(tempfile.mkdtemp(), 't.db')
database.init_db(p)
a = database.create_user('a', 'x', db_path=p); b = database.create_user('b', 'x', db_path=p)
database.queue_episode('http://e', 'E', None, None, a, db_path=p)
eid = database.get_episode('http://e', a, db_path=p)['id']
aid = database.add_article(eid, 'T', 'blog', 'C', a, db_path=p)
assert database.get_article(aid, b, db_path=p) is None
assert database.update_article(aid, user_id=b, content='X', db_path=p) is None and database.get_article(aid, a, db_path=p)['content'] == 'C'
sp = database.add_social_post(aid, 'wechat', 'P', a, db_path=p)
assert database.get_social_post(sp, b, db_path=p) is None
assert database.delete_social_posts_bulk([sp], b, db_path=p) == 0
assert database.delete_social_posts_bulk([sp], a, db_path=p) == 1
n = database.bulk_replace_post_content('C', 'D', 'social', a, db_path=p)
assert n == 0 and database.get_article(aid, a, db_path=p)['content'] == 'C'
print('ARTICLES_SOCIAL_SCOPING_OK')
"
```

失败摘要（旧代码下特征性失败，非笔误）：

```
TypeError: add_article() got multiple values for argument 'db_path'
```

（旧 `add_article` 第 5 个位置参是 `db_path`，脚本第 5 位传 user id → 冲突；后续 `get_article(aid, b)` 等跨用户断言在旧实现下也必然失败。）

### GREEN

实现后同一命令通过：

```
ARTICLES_SOCIAL_SCOPING_OK
```

冒烟：`venv/Scripts/python.exe -c "import database"` → OK；`py_compile database.py` → OK。

## 实现明细（均在 database.py）

| 函数 | 新签名要点 | SQL 作用域 |
|------|-----------|-----------|
| `add_article` | `user_id: int` 字面必填，位置在 content 后、`db_path` 前 | INSERT 列清单加 `user_id` |
| `get_article(article_id, user_id, db_path=...)` | 必填 | `WHERE a.id = ? AND a.user_id = ?` |
| `update_article(article_id, user_id, topic=None, style=None, content=None, db_path=...)` | 必填 | `UPDATE ... WHERE id = ? AND user_id = ?` |
| `delete_article(article_id, user_id, db_path=...)` | 必填 | social_posts 先删，限定 `article_id IN (SELECT id FROM articles WHERE id = ? AND user_id = ?)`；articles 删除同样带 `AND user_id = ?` |
| `list_articles(episode_id, user_id, db_path=...)` | 见下方约束说明 | 两分支各加 `WHERE a.user_id = ?` / `AND a.user_id = ?` |
| `add_social_post` | `user_id: int` 字面必填，位置在 content 后、image_url 前 | INSERT 列清单加 `user_id` |
| `list_social_posts(article_id, user_id, db_path=...)` | 见下方约束说明 | 两分支各加 `WHERE sp.user_id = ?` / `AND sp.user_id = ?` |
| `get_social_post(post_id, user_id, ...)` | 必填 | `WHERE sp.id = ? AND sp.user_id = ?` |
| `delete_social_post(post_id, user_id, ...)` | 必填 | `WHERE id = ? AND user_id = ?` |
| `delete_social_posts_bulk(post_ids, user_id, ...)` | 必填 | `WHERE id IN (...) AND user_id = ?`（空列表仍返回 0） |
| `delete_social_posts_for_article(article_id, user_id, ...)` | 必填 | `WHERE article_id = ? AND user_id = ?` |
| `mark_social_post_used(post_id, used=True, user_id, ...)` | 见下方约束说明 | `WHERE id = ? AND user_id = ?` |
| `update_social_post(post_id, content, user_id, ...)` | 必填 | `WHERE id = ? AND user_id = ?` |
| `update_social_post_image(post_id, image_url, user_id, ...)` | 必填 | `WHERE id = ? AND user_id = ?` |
| `bulk_replace_post_content(find_text, replace_text, post_type, user_id, case_sensitive=False, whole_word=False, post_ids=None, excluded_matches=None, db_path=...)` | `user_id: int` 字面必填，位置在 post_type 后 | 两处 SELECT 与 UPDATE 均加 `AND user_id = ?`（`post_ids` 分支参数顺序 `(*post_ids, uid)`；无 post_ids 分支 `WHERE user_id = ?`） |

social_posts 系列的行级过滤统一用 `social_posts.user_id`（本表自身归属列，Task 1 已加），不依赖 JOIN；`list_social_posts` 的 JOIN 仅为取 `article_topic`。

## Global Constraint 落法说明（Optional=None 例外）

以下 3 个函数因 user_id 前面存在带默认值的参数（语法上非默认参不能跟在默认参后），无法字面必填，按约定落 `user_id: Optional[int] = None` + 函数体首行 `_require_user(user_id)`，语义不变（None 直接 ValueError）：

- `list_articles(episode_id: Optional[int] = None, user_id: Optional[int] = None, db_path=...)`（episode_id 有默认）
- `list_social_posts(article_id: Optional[int] = None, user_id: Optional[int] = None, db_path=...)`（同上）
- `mark_social_post_used(post_id: int, used: bool = True, user_id: Optional[int] = None, db_path=...)`（used 有默认）

其余 12 个函数 user_id 均为字面必填位置参。

## 复用与边界

- `_require_user(user_id)` 直接复用 Task 2 产出（database.py:834），未重定义/移动。
- 白名单函数未动；Task 1/2 已完成代码未改。

## 验证外观察（gate 现状，供协调者参考）

本任务无 tests/ 目录，项目级验证 = brief 脚本 + gates。抽跑两个直接调用目标函数的 gate：

- `scripts/check_channel_defaults.py` → FAIL：`TypeError: add_article() missing 1 required positional argument: 'user_id'`。属调用方未适配新签名（计划内，后续 task 修复），非本任务回归。
- `scripts/check_queue_groups.py group` → FAIL 于 `_accounts_gate.py:177`（`social_publisher` 为 None）：并行会话删除 social_publisher 造成的预存失败（MEMORY 已记录），早于本任务、与本次改动无关。

未发现任何指向 database.py 内部行为错误的失败模式。

## 调用方待适配清单（后续 task 范围，本任务未改动）

- `insights_web.py`：2090、2169（list_articles）、2576（add_article）、2595/2604/2677（get_article）、2614/2696（update_article）、2622（delete_article）、2656/3544（bulk_replace_post_content）
- `content_agent.py`：380、403（add_article）
- `scripts/check_channel_defaults.py:29`、`scripts/check_queue_groups.py:188-201,759`、`scripts/check_crosspost.py:182-183`
