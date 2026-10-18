# 终审修复报告（final-review fix）

Branch: multi-user-content-isolation（build 阶段，终审五项修复一次提交）
Date: 2026-10-17
Executor: final-review fix agent（subagent-driven-development 协调者派出）

## 总览

| Finding | 级别 | 结论 |
|---|---|---|
| C1 episodes.channel 重建被丢弃 | Critical | 已修 + gate 盲区补齐 |
| I1 auto_queue 缺 user_id 静默失效 | Important | 已修 + e2e 场景补齐 |
| I2 /episodes/backfill-channels 跨用户读写 | Important | 已加 admin 门 |
| M1 四表重建四步各自提交 | Minor | 已套 SAVEPOINT 事务 |
| M2 task_queue.put 兜底 user_id 未收口 | Minor | 已改用路由内 uid |

---

## C1（Critical）：episodes.channel 在旧库重建时被静默丢弃

### 改动点
- `database.py:46` — `_NEW_DDL["episodes"]` 补 `channel TEXT`（放 user_id 前，与升级分支列序一致：channel 的 ALTER（database.py:561）先于 user_id 的 ALTER（database.py:667）执行）。
- `database.py:82-83` — `_COPY_COLS["episodes"]` 列清单补 `channel`。

### gate 盲区修复（scripts/check_content_isolation.py）
- `scripts/check_content_isolation.py:186` — `_OLD_DDL` episodes fixture 补 `channel TEXT` 列。
- `scripts/check_content_isolation.py:232-233` — 旧库种子补一条非 NULL channel（`'Legacy Channel'`）。
- `scripts/check_content_isolation.py:290-312` — 升级断言补四条：channel 列在、`'Legacy Channel'` 值保留、`get_youtube_episodes_missing_channel` 可运行（命中 0 行）、`set_episode_channel` 可写入。
- `scripts/check_content_isolation.py:328-330` — schema 节总结 print 补「episodes channel 列与值跨重建保留且回填函数可跑」。

### 验证 1：独立旧库升级脚本（临时脚本 /tmp/verify_c1_channel.py，不入库）

构造带 channel 数据的旧库（url TEXT UNIQUE 形状）跑 `database.init_db(p)`：

```
重建前 episodes 行数: 1 | channel 值: 老频道 Legacy
重建后 episodes 列: ['id', 'feed_id', 'url', 'title', 'transcript', 'summary', 'action_items', 'status', 'published', 'processed_at', 'channel', 'user_id']
重建后行数: 1 | channel 值: 老频道 Legacy
重建后建表 SQL 仍含 url UNIQUE: False
get_youtube_episodes_missing_channel 可运行, 命中 0 行（旧种子非 YouTube 应为 0）
set_episode_channel 写入后 channel 值: 回填频道
C1_VERIFY_OK
```

断言全过：行数不变、channel 值保留、两个白名单函数不再抛 no such column。

### 验证 2：gate 内断言
`venv/Scripts/python.exe scripts/check_content_isolation.py schema` → `CONTENT_ISOLATION_SCHEMA_OK`（新增 channel 断言在内）。

## I1（Important）：auto_queue 的 get_standalone_post 缺 user_id

### 改动点
- `content_agent.py:189` — `database.get_standalone_post(pid)` → `database.get_standalone_post(pid, brief["user_id"])`（_auto_queue 域内 brief 主人取自 `brief["user_id"]`，与同函数 add_scheduled_post 的 user_id 来源一致）。修前 TypeError 被 `run_brief` 的 auto_queue except（content_agent.py:437-441）吞掉，自动排期静默失效。

### e2e 场景（scripts/check_content_isolation.py:855-888）
复用 Task 16 的假 generate 形态（patch `research_engine.run_research` / `insights.generate_posts_from_prompt`，不联网）：
- 清空时段后放一枚 `(今天+3)%7` 星期位 23:00 的远端格（下一次出现必在 2-3 天后，稳过默认 24h 审查窗）；
- `content_agent.SCHEDULABLE` 生产上刻意为空，内存临时放开 `{"wechat"}`，finally 还原；
- 创建 `auto_queue=True` 的 brief（B 主人）跑 `content_agent.run_brief`，断言 `summary["queued"] == 1`、`scheduled_posts` 恰 1 行且 `user_id == uid_b`、`platform == "wechat"`。

### 验证
`venv/Scripts/python.exe scripts/check_content_isolation.py e2e` → `CONTENT_ISOLATION_E2E_OK`，INFO 行：
```
INFO e2e 6/9 episode/brief 产物按主人归属，auto_queue 真落排期归主人，backfill-channels 非 admin 404、admin 可用
```

负对照（临时脚本）：把 `database.get_standalone_post` 包回旧单位参形状后调 `_auto_queue`，精确复现修前异常：
```
负对照复现修前异常: TypeError: <lambda>() takes 1 positional argument but 2 were given
NEGATIVE_CONTROL_OK
```

## I2（Important）：/episodes/backfill-channels 缺 admin 门

### 改动点
- `insights_web.py:2567-2579` — 路由入口加门：`user = current_user(); if not (user and user["is_admin"]): abort(404)`。非 admin（含匿名）与不存在路由同响应体，admin 照常可用；两个白名单函数 `get_youtube_episodes_missing_channel` / `set_episode_channel` 本身未改。

### e2e 断言（scripts/check_content_isolation.py:890-900）
- B（非 admin）POST → 404，且与 POST 一个不存在的路由逐字节一致；
- admin POST → 200 且 `success: true`（空库 0 行，不联网）。

### 验证
包含在 e2e gate（同上 `CONTENT_ISOLATION_E2E_OK`）。

## M1（Minor）：四表重建四步各自独立提交

### 改动点
- `database.py:688-709` — 每表四步（DROP _new / CREATE / COPY / DROP 旧表 / RENAME）套 `SAVEPOINT {table}_rebuild` … `RELEASE`，异常时 `ROLLBACK TO` + `RELEASE` 后重抛。选 SAVEPOINT 而非裸 BEGIN IMMEDIATE：连接用 legacy 隐式事务模式，到达此处时可能已带外层事务（上文 users 的 is_admin 回填 UPDATE），SAVEPOINT 在有无外层事务两种情形下语义一致（无外层时 RELEASE 即提交，有外层时并入）。

### 验证
schema gate 的旧库升级路径（`check_content_isolation.py schema`）真实走过 SAVEPOINT 分支（旧库含 url UNIQUE 触发重建），含二次 init_db 幂等 → `CONTENT_ISOLATION_SCHEMA_OK`。

## M2（Minor）：两处 task_queue.put 兜底 user_id 未收口

### 改动点
- `insights_web.py:2066`（enqueue_episode）、`insights_web.py:2534`（reprocess_episode）— `'user_id': _user['id'] if _user else None` → `'user_id': uid`，与 transcribe_source（insights_web.py:6526）同款；username 仍走 `_user['username'] if _user else None`（展示字段，非归属）。

### 验证
- `/enqueue` 归属断言在 e2e 场景 6 已有（admin/B 各自入队 episode 归各自），继续 PASS；
- 冒烟 import 通过（见下）。

---

## 汇总验证

1. isolation 四 gate（`venv/Scripts/python.exe scripts/check_content_isolation.py {schema,data,scan,e2e}`）：
```
CONTENT_ISOLATION_SCHEMA_OK
CONTENT_ISOLATION_DATA_OK
CONTENT_ISOLATION_SCAN_OK
CONTENT_ISOLATION_E2E_OK
```
2. C1 独立旧库升级验证：`C1_VERIFY_OK`（输出见上）。
3. I1 auto_queue e2e：`summary.queued == 1` 且排期行 `user_id == uid_b`（INFO e2e 6/9）。
4. 冒烟：`venv/Scripts/python.exe -c "import database, insights_web, content_agent, research_engine"` → `SMOKE_IMPORT_OK`。
5. `venv/Scripts/python.exe scripts/run_all_gates.py` 修复前后对照：
   - 修复前：60 of 83 failed，23 passed；
   - 修复后：60 of 83 failed，23 passed；
   - 失败集合去掉耗时后缀后逐项 diff：**完全一致，零新增零消失**（既有 ~60 遗留失败为并行会话删除 accounts/schedule/social_publisher 所致，与本次无关）；
   - isolation 四 gate 在全量跑中 PASS（run_gate 内部校验 token，FAIL 名单无 check_content_isolation 条目）。

## 提交
- 单 commit（五项均为小改，合为一个逻辑单元）：「终审修复：episodes 重建保留 channel，auto_queue 归属接线，backfill-channels admin 门，重建事务化」
- 精确 stage：`git add database.py scripts/check_content_isolation.py content_agent.py insights_web.py`（无 attribution trailer）。
