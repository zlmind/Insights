# Task 0 Report: 提交计划与 change 产物

## Status: DONE_WITH_CONCERNS

## What I implemented

按 brief Step 1 精确 stage 了 6 个文件并提交，commit message 使用 brief 原文：

```
多用户内容隔离：proposal/design/tasks/spec 与实施计划
```

### 偏差说明（关注点 1）

首次 `git add` 时，4 个 openspec md 文件被 `.gitignore` 第 10 行的全局规则 `*.md` 拒绝
（该文件仅白名单 `!CLAUDE.md`、`!docs/superpowers/specs/*.md`、`!docs/superpowers/plans/*.md`）。
openspec/ 历史上只入过 `.gitkeep` 和 `config.yaml`，从未跟踪过 change 的 md 文件。

处理方式：对这 4 个明确路径使用 `git add -f` 强制添加。理由：
- brief 明确要求提交这 6 个文件，意图是让 change 产物进入基线供后续 16 个任务引用；
- `-f` 只作用于列出的 4 个路径，不修改 `.gitignore`、不引入任何额外文件，仍满足"精确 stage 6 个文件"约束；
- 替代方案（往 .gitignore 加白名单）会引入第 7 个文件变更并持久改变 ignore 行为，超出任务授权。

建议后续由 owner 决定是否在 `.gitignore` 增加 `!openspec/changes/**/*.md` 类白名单正规化。

另外：`openspec/changes/multi-user-content-isolation/` 下的 `.comet.yaml`、`.comet/`、
`.openspec.yaml` 未提交（不在 brief 文件清单内，属本地流程状态）。

## Files committed (6)

- `openspec/changes/multi-user-content-isolation/proposal.md`
- `openspec/changes/multi-user-content-isolation/design.md`
- `openspec/changes/multi-user-content-isolation/tasks.md`
- `openspec/changes/multi-user-content-isolation/specs/user-content-scoping/spec.md`
- `docs/superpowers/specs/2026-10-17-multi-user-content-isolation-design.md`
- `docs/superpowers/plans/2026-10-17-multi-user-content-isolation.md`

## Verification output (Step 2)

`git show --stat HEAD`（commit `6ed6c27`）：

```
 .../2026-10-17-multi-user-content-isolation.md     | 988 +++++++++++++++++++++
 ...26-10-17-multi-user-content-isolation-design.md | 169 ++++
 .../changes/multi-user-content-isolation/design.md |  48 +
 .../multi-user-content-isolation/proposal.md       |  33 +
 .../specs/user-content-scoping/spec.md             |  90 ++
 .../changes/multi-user-content-isolation/tasks.md  |  31 +
 6 files changed, 1359 insertions(+)
```

- 恰好 6 个文件，全部为新增，无其他文件混入。
- `git log -1 --format=%B` 仅含 brief 原文一行；attribution grep exit 1，无命中。
- 作者与 committer 均为 `l0lsec <51931886+l0lsec@users.noreply.github.com>`（提交前已确认，未改动 config）。
- 未 push。

---

# 评审修复追加（控制器批次审查反馈）

## Fix 1 — Important：proposal.md 三处旧「回填」方案 + recent prompts 表述

`openspec/changes/multi-user-content-isolation/proposal.md` 原三处仍是被推翻的「把存量
无主数据划给第一个账号（admin）」旧方案，与 spec.md（SHALL NOT 做任何数据回填或自动划归）
和 design.md Decisions 3 / Goals / Migration Plan 矛盾。已按 2026-10-17 用户改定方案改写四处：

1. **Why 段**：「同时把存量无主数据划给第一个账号，避免升级后数据"消失"」→
   「存量无主数据不迁移，升级后对所有用户（含 admin）不可见（2026-10-17 改定）」。
2. **What Changes 表清单**：移除「recent prompts」独立表表述，改为在该条末尾注明
   「最近提示词」视图是 standalone_posts 的派生查询，无独立表，随该表过滤自然隔离
   （与 spec.md 表清单一致，该表本就无 recent prompts）。
3. **What Changes 迁移条**：「一次性数据迁移：现有全部无主内容数据划给第一个注册账号
   （admin）」→「存量数据不迁移：只加列，无回填、无标志位；存量行 `user_id` 保持 NULL，
   对所有用户（含 admin）不可见，数据保留在库中、可后续手工 SQL 划归（2026-10-17 改定）」
   （措辞对齐 design.md Goals 第 3 条与 Migration Plan 第 2/3 条）。
4. **Impact 迁移条**：「+ 回填脚本（应用启动时自动执行）」→「无回填、无标志位
   （应用启动时自动执行，沿用现有 in-place 升级模式）（2026-10-17 改定）」。

只改了 proposal.md，未动 spec.md / design.md / tasks.md。

## Fix 2 — Minor：database.py 迁移注释 FK 前提

`database.py` 三表重建循环（`for table in ("feeds", "episodes", "url_sources"):`，原 665 行）
之前新增一行英文注释，说明：重建假设连接层 `PRAGMA foreign_keys` 保持 OFF
（项目惯例从未开启）；若未来开启 FK，此迁移前需先执行 `PRAGMA foreign_keys=OFF`。
只加注释，未改任何逻辑。

## 修复提交与验证

- Commit：`b9bf73f` 评审修正：proposal 同步无回填改定，迁移注释 FK 前提
- `git show --stat`：恰好 2 文件（database.py +1、proposal.md 8 行改动），无其他文件混入
- attribution grep：exit 1，无命中；作者仍为 `l0lsec <51931886+l0lsec@users.noreply.github.com>`
- 冒烟：`venv/Scripts/python.exe -c "import database, insights_web"` → `smoke OK`
- staging 用了 `git add -f openspec/changes/multi-user-content-isolation/proposal.md`
  （同关注点 1 的 `*.md` 全局忽略，此前已向控制器说明）+ 普通 `git add database.py`。

## 残留关注点（不阻塞）

1. `.gitignore` 的 `*.md` 全局规则未正规化，后续 openspec md 修改仍需 `-f`。
2. `comet-hook-guard.sh` 的 fallback 分支会取「第一个活跃 change」的 phase 管控
   `.superpowers/sdd/` 等非 change 目录路径，多 change 并行时可能被其他 change 的
   design phase 误拦（本任务首份报告曾因此被拦，控制器已改用 change 目录内路径规避）。

## 环境备注

初始基线提交 `6ed6c27` 之上，HEAD 前一个提交为并行会话产生的 `3d6cece`（"66"）；
两处修复提交前确认工作区无其他暂存内容。
