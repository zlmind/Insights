# Task 11 报告：/activity 与 /usage 的 admin 视角收口

**Status: DONE_WITH_CONCERNS**（实现与验证全部完成、GREEN 达成；brief 验证脚本有三处无法原样运行的缺陷，测试侧最小修正后跑通，生产代码零多余改动，见「偏差与顾虑」）

**Commit:** `860cab4` 活动页收口：非 admin 固定本人视角，忽略 user_id 参数；全部视角仅 admin
（精确 `git add insights_web.py templates/activity.html`；2 files changed, +8/-3；作者 `l0lsec <51931886+l0lsec@users.noreply.github.com>`；message 无任何 attribution，`git log -1 --format=%B` 检索 attribution 计 0）

## 改动内容

| 文件 | 改动 |
|------|------|
| `insights_web.py` `_parse_activity_filters`（~699，按符号定位） | brief Step 1 代码逐字落入：`current_user()` 非 admin 一律 `user_id = user["id"]`（`?user_id=` 参数忽略）；admin（及保守的 user=None 分支）用 `requested`——按参数筛选、不带参数看全部 |
| `templates/activity.html` | 用户筛选下拉整列（label + select）包 `{% if current_user and current_user['is_admin'] %}…{% endif %}`；action/datalist/日期/导出等控件不动 |

调用面确认：`_parse_activity_filters` 仅有 `activity_page`、`activity_csv` 两个调用方，均被同一改动覆盖。`_usage_scope_user_id`（~832）未动，仅被验证步骤覆盖。

## TDD 过程

### RED（改造前，brief Step 3 双账号脚本）

命令：`venv/Scripts/python.exe -c "<双账号脚本>"`（脚本本体见下节修正说明；隔离环境来自 `scripts/_accounts_gate.py` 的 `isolated_app()`，'gate'=admin、'other'=非 admin，各播一条 activity：`mine`→gate、`theirs`→other）

失败摘要：

```
Traceback (most recent call last):
  File "<string>", line 12, in <module>
AssertionError: non-admin ?user_id= must be ignored, fixed to own view
```

第 12 行断言 = 非 admin 会话（other）请求 `/activity?user_id=<gate的id>` 时不得看到 `<code>mine</code>`（gate 的行）、必须看到自己的 `<code>theirs</code>`。改造前现状把参数当筛选条件用了——other 拿着 gate 的 id 看到了 gate 的活动，失败原因正确：正是本任务要关的越权口子。

### GREEN（改造后，同一脚本）

输出：

```
ACTIVITY_USAGE_SCOPE_OK
```

逐条覆盖：非 admin `/activity` 与 `/activity.csv` 带 `?user_id=<他人>` 只见本人行（HTML 用 `<code>动作名</code>` 标记、CSV 用裸字节）；admin 带 `?user_id=<other>` 恰好只筛出 other 的行（证明 admin 未被误钉到本人）；admin 不带参数见全部两行；`/usage?scope=all` admin 放行、非 admin 被忽略（既有 `_usage_scope_user_id` 行为回归确认）。

### 收尾验证

- gate 套件：`venv/Scripts/python.exe scripts/run_all_gates.py` 基线对比——**stash 我的改动前后同为 66 of 79 失败且失败清单逐项一致**。失败主因是并行会话已删除 `social_publisher`/instagram token API（`'NoneType' object has no attribute 'get_linkedin_client'`、`module 'database' has no attribute 'save_instagram_token'`），属既有失效（与 Task 10 报告记录的同一基线、同 66 项），非本任务引入。
- 提交后 `git show --stat HEAD` 确认只含两个目标文件；并行会话的 `.claude/skills/comet/scripts/comet-hook-guard.sh` 未暂存改动保持原样未被卷入。

## 偏差与顾虑（均系测试侧修正，生产代码零偏差）

brief Step 3 脚本有三处无法原样跑通的缺陷，做了最小修正（脚本结构、会话切换顺序、断言语义全部保留）：

1. **`create_user` 返回 int**：`other = database.create_user(...)` 返回 int uid，brief 的 `other['id']` 会 `TypeError: 'int' object is not subscriptable`，脚本跑不到任何断言。修正：两处直接用 `other`。（与 Task 10 报告记录的同款问题，建议 brief 模板统一。）
2. **session 塞了整行 Row**：brief 的 `s['user_id'] = me`，而 `get_user_by_username` 返回 `sqlite3.Row`，Flask session JSON 序列化直接抛 `Object of type Row is not JSON serializable`（RED 阶段因前序断言先失败而被掩盖，GREEN 首跑才暴露）。修正：`s['user_id'] = me['id']`。
3. **HTML 裸字节断言不可满足 + 断言方向与目标行为相反**：
   - `templates/activity.html` 的 action datalist 对所有用户渲染全部 distinct action（`distinct_activity_actions()` 不按用户过滤），`b'theirs' not in r` 在 HTML 页面上改造前后都不可能成立。修正：HTML 断言改用只出现在表格行里的 `<code>theirs</code>` / `<code>mine</code>` 标记；CSV 无 datalist，保持裸字节。
   - brief 原断言 `b'theirs' not in r and b'mine' in r`（非 admin 会话带 `?user_id=<gate>`）描述的恰是**改造前的越权行为**（参数被采纳、看到 gate 的行），改造后（固定本人、只见 theirs）它必然失败。按任务描述「非 admin 固定本人视角忽略参数」修正断言方向为「不得见他人、必见自己」，这才是能 RED→GREEN 的测试。
   - 顺带补了一条 brief 文字里要求但脚本没断言的用例：admin 不带参数看全部两行。
4. **顾虑（非阻塞）**：非 admin 的 `/activity` 响应中，`users=list_users()` 仍在服务端传入模板（只是不再渲染），且 action datalist 仍向非 admin 暴露全实例 distinct action 名。两者都非行级数据泄漏（brief 明确服务端拦截是硬保证、模板只是不渲染无效控件），如需收紧可由后续 task 决定。
