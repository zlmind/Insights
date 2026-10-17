# 验证报告：multi-user-content-isolation

- Date: 2026-10-17（verify 执行于 2026-10-18）
- verify_mode: full（comet-state scale：15 任务 / 1 capability / 34 文件）
- 范围: 3d6cece..HEAD（34 文件，+4283/−1017，26+ 提交）
- SDD 账本（裁定与延后项全录）: .superpowers/sdd/2026-10-17-multi-user-content-isolation/progress.md

## Summary

| 维度 | 结果 |
|------|------|
| Completeness | tasks.md 15/15 勾选（0 未勾）；plan 17 任务全部 Step 勾选 |
| Correctness | delta spec 6 requirement 全部由 gate 钉住（见映射表）；四 isolation gate 全绿（本次验证全新运行） |
| Coherence | 三道防线与 Design Doc 一致；4 处偏差已记录于 Design Doc「Implementation Divergence」节 |

## 全新验证证据（本报告当日运行）

1. **tasks.md**：total=15，unchecked=0（`grep -c` 实测）。
2. **isolation 四 gate**：`CONTENT_ISOLATION_SCHEMA_OK` / `DATA_OK` / `SCAN_OK` / `E2E_OK` 全部 PASS（独立命令逐节运行）。
3. **全量 run_all_gates**：83 gate，60 失败——失败清单与 build 期基线逐名一致，**零新增失败**；四 isolation gate 在全量跑中亦 PASS。
4. **冒烟**：`import database, insights_web, content_agent, research_engine, insights, agent_hub, usage_meter` → OK。
5. **build 守卫**：`comet-guard build --apply` ALL CHECKS PASSED（build_command = 全模块导入冒烟）。
6. **final whole-branch review**（fable，26 commits 全量 diff）：1 Critical + 2 Important + 4 Minor → 修复波 `0a4e7df`（episodes.channel 重建保留、auto_queue 归属接线、backfill-channels admin 门、重建 SAVEPOINT 事务化、task_queue 兜底收口）→ 定向 re-review 5/5 ADDRESSED、无新破坏。

## delta spec ↔ 验证映射

| Requirement | 场景覆盖 |
|---|---|
| 内容数据按用户归属 | schema（12 表列 + 复合索引 + 双用户同 URL 各一行）+ data（写入口缺 user_id 抛 ValueError，15 入口）+ scan（12 表 INSERT 带归属列，白名单防腐） |
| 列表仅显示当前用户 | e2e 场景 2（五页全空 + admin 正对照）/ 4（同名 prompt 各见各帖） |
| 跨用户单资源 404 | e2e 场景 3（四路由 × 与不存在 id 逐字节同响应体）+ data 节两代表路由 |
| 后台任务沿用触发者归属 | e2e 场景 6（episode 归操作者、brief 产物归主人、auto_queue 排期归主人、无孤儿） |
| 存量保留但不可见 | schema（旧库升级行数不变、channel 列与值跨重建保留）+ e2e 场景 5（NULL 对 A/B/独立 admin 三视角不可见且 COUNT 仍在库） |
| 「全部」视角仅 admin | e2e 场景 7（非 admin 参数忽略、admin 全景）+ data 节 |

## 60 个遗留失败归因（非本 change 范围）

全部源于并行会话的功能退役/删除：social_publisher 模块退役、5 个平台客户端模块与 save_*_token 函数删除、/schedule/add 路由删除、article 页渠道选择器与 compose Reel 控件移除。build 期已用 stash/临时 worktree 基线对照证明：本 change 前这些 gate 即红，期间无一基线绿 gate 被弄红；Task 14 的 218/218 调用点 AST 核验 + 终审 202 处全量核查确认无签名类失败残留。**复绿取决于并行会话功能去留，建议 owner 单独处置。**

## 已接受的偏差 / 遗留项（均已记录）

- Design Doc「Implementation Divergence」4 条（三表重建、uploaded_images 去 UNIQUE、usage 无 per-user 下拉、白名单 7 项）。
- follow-up 候选：uploaded_images (url,user_id) 复合索引（M3）、NULL-owner legacy brief 限频（M4）、/compose/list-images 磁盘扫描归属（D②，待 owner 决定）、.gitignore 补 `!openspec/changes/**/*.md` 白名单。
- 60 个遗留 gate 失败（见上节）。

## 结论

**无 CRITICAL / IMPORTANT 未决项。Ready for archive.**（分支处理见下方记录。）
