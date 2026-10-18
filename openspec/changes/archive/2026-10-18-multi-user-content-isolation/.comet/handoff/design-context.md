# Comet Design Handoff

- Change: multi-user-content-isolation
- Phase: design
- Mode: compact
- Context hash: c978933dcdee4b54dcfa61c347dda052acf3d5933581e8e082adc6215565b569

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/multi-user-content-isolation/proposal.md

- Source: openspec/changes/multi-user-content-isolation/proposal.md
- Lines: 1-33
- SHA256: 70b84598508434b8382993ed075bde60515881f5d70b6a813961e658da5ddaba

```md
# Proposal: multi-user-content-isolation

## Why

平台已有账号体系（users 表 + 登录门槛 + session），但所有业务数据共用一个池：任何账号（如新注册的 layne 与原有 demo）登录后都能看到全部 RSS 源、文章、帖子、素材、提示词、briefs 和定时帖子。需要按用户隔离内容数据，让每个用户只看到自己的内容；同时把存量无主数据划给第一个账号，避免升级后数据"消失"。

## What Changes

- **BREAKING**：内容域数据表（feeds、episodes、articles、standalone_posts、url_sources、uploaded_images、generated_thumbnails、prompt_library、recent prompts、content_briefs、content_brief_runs、scheduled_posts）增加 `user_id` 归属，所有读写按当前登录用户过滤；跨用户 ID 直访返回 404。
- 数据层 `database.py` 的 list/get/create/update/delete 函数增加用户归属参数与过滤。
- Web 层 `insights_web.py` 全部内容路由注入 `current_user`，按用户过滤列表、校验资源归属。
- 后台 worker（episode 处理、content_agent 跑批、brief 定时跑、YouTube 频道回填）在入队/生成时记录归属用户。
- 一次性数据迁移：现有全部无主内容数据划给第一个注册账号（admin）。
- 用量/活动页的「全部」视角仅对 admin 开放，普通用户只能看自己的。
- 平台账号（social_accounts）、浏览器扩展配对、调度时段/每日限额、`app_settings` 本次**不**隔离（后续 change 处理）。

## Capabilities

### New Capabilities

- `user-content-scoping`: 内容数据按用户隔离的行为契约——归属、可见性、跨用户访问拒绝、后台任务归属、存量迁移。

### Modified Capabilities

<!-- 项目尚无任何 spec，全部为新增能力。 -->

## Impact

- `database.py`（4365 行）：~12 张表 schema + 全部 list/get/create/update/delete 函数签名与 SQL。
- `insights_web.py`（7064 行，~100 路由）：内容相关路由的查询过滤与归属校验。
- `content_agent.py` / `insights.py`：后台 worker 的归属传递。
- SQLite 迁移：`ALTER TABLE ADD COLUMN user_id` + 回填脚本（应用启动时自动执行，沿用现有 in-place 升级模式）。
- 不影响：MultiPost 扩展协议、agent 配对、发布通道（仍共享）、调度时段配置。
```

## openspec/changes/multi-user-content-isolation/design.md

- Source: openspec/changes/multi-user-content-isolation/design.md
- Lines: 1-48
- SHA256: 8277cd36cb7217ea8b016ea401cb1e5bf10b5702d9ca9f8174112f2cbbf1d276

```md
# Design: multi-user-content-isolation

## Context

认证层已就绪（`users` 表、session 门槛、`current_user()`），`activity_log`/`usage_events` 已带 `user_id`，`content_briefs` 有列但查询不过滤。其余内容表完全无归属概念；`database.py` 全部函数以 `db_path` 为唯一作用域参数，Web 层路由直接按 ID 读写。SQLite 单文件、启动时 in-place 迁移是既有惯例（`PRAGMA table_info` → `ALTER TABLE`）。

## Goals / Non-Goals

**Goals:**

- 内容域数据严格按登录用户隔离；列表只见自己的，单资源直访他人的返回 404。
- 后台生成/跑批的内容归属到触发它的用户。
- 存量数据不迁移：旧数据 NULL 归属对所有用户（含 admin）不可见，视为不可见孤儿而非公共数据；数据保留在库中，可手工 SQL 划归。
- admin 在内容页面与其他用户完全同权（纯隔离）；仅用量/活动页保留「全部」视角。

**Non-Goals:**

- 不隔离 social_accounts、扩展配对、登录态（后续 change `multi-user-publishing-channels`）。
- 不拆分调度时段/每日限额（保持全局）。
- 不做用户管理页（后续 change `admin-user-management`）。
- 不引入 ORM 或改用其他数据库；保持 raw sqlite3。

## Decisions

1. **归属列统一命名 `user_id`**，与 `content_briefs`/`usage_events` 现状一致，可空（FK `SET NULL`）。不用 `owner_id` 等新命名，避免同库两套词汇。
2. **作用域下沉到数据层函数签名**：`list_*` 增加 `user_id` 必填过滤；`get_*/update_*/delete_*` 增加归属校验，不匹配时按"不存在"处理。路由层仍需传参校验，但不依赖路由层作为唯一防线（避免遗漏某条路由即泄漏）。
3. **迁移 = 启动时 in-place 加列，无回填、无标志位**（2026-10-17 用户改定）。加列沿用现有 `PRAGMA table_info` 检测模式；episodes 的 url 全局唯一索引改为 `(url, user_id)` 复合唯一（先探测 drop 旧索引）。
4. **后台 worker 归属在入队时固化**：episode/content 行创建时写入触发用户的 `user_id`，worker 只按行上归属行事；brief 跑批沿用现有 `brief["user_id"]` 归属（补上查询过滤）。不尝试在 worker 线程里恢复"当前用户"上下文。
5. **跨用户访问一律 404**，不用 403——不向探测者泄露资源存在性。
6. **上传文件不迁移目录结构**：继续统一存 `static/uploads/`（UUID 文件名），归属以 DB 行为准。避免文件搬移和 URL 断链。
7. **「全部」视角收口**：usage/activity 的 scope=all 仅 admin 可选；非 admin 请求带该参数时按"我的"处理。

## Risks / Trade-offs

- [查询路径遗漏导致数据泄漏] → 任务清单按表逐张覆盖；收尾增加一个 gate 脚本扫描 `database.py` 中内容表的 `SELECT` 是否带 `user_id` 过滤，并入 `run_all_gates.py`。
- [后台 worker 忘记带归属，产出 NULL 孤儿数据] → 入队函数（唯一写入口）强制要求 `user_id`；gate 脚本同时检查 `INSERT` 是否含 `user_id` 列。
- [episodes 索引切换影响 INSERT OR IGNORE 去重] → 迁移内显式 drop 旧 url 唯一索引再建 (url, user_id) 复合唯一；E2E 覆盖同 URL 重入队。
- [签名改动量大，一次性 PR 难以审查] → tasks 按表分批提交，每批一个 commit。

## Migration Plan

1. 部署前手动备份 `insights.db`（回滚 = 还原备份）。
2. 启动时自动加列（幂等），不做回填。
3. 存量行 `user_id` 保持 NULL，对所有用户不可见（按不存在处理）；数据保留在库中，可手工 SQL 划归或清理。

## Open Questions

- 无（「全部」视角、存量不迁移均已由用户确认）。
```

## openspec/changes/multi-user-content-isolation/tasks.md

- Source: openspec/changes/multi-user-content-isolation/tasks.md
- Lines: 1-31
- SHA256: 0a16a5af3f6bb30c09e62e881957d1fd952d3f9826ae6eabb957ce84d671ba50

```md
# Tasks: multi-user-content-isolation

## 1. 数据层迁移

- [ ] 1.1 内容域数据表 in-place 加 `user_id` 列（feeds、episodes、articles、social_posts、standalone_posts、url_sources、uploaded_images、generated_thumbnails、prompt_library、content_brief_runs、scheduled_posts；content_briefs 列已存在；幂等，沿用 `PRAGMA table_info` 检测）；episodes 的 url 全局唯一索引改为 (url, user_id) 复合唯一；无回填、无标志位
- [ ] 1.2 存量数据不迁移（2026-10-17 改定）：旧数据 NULL 归属对所有用户不可见（含 admin），数据保留在库中，可后续手工 SQL 划归或清理；核查无任何代码路径自动为 NULL 归属赋值

## 2. 数据层作用域

- [ ] 2.1 feeds/episodes：list 按 user 过滤，get/update/delete 归属校验（不匹配按不存在处理）
- [ ] 2.2 articles/social_posts/standalone_posts：同上（含 bulk 操作的归属限定）
- [ ] 2.3 url_sources/uploaded_images/generated_thumbnails/prompt 库与最近 prompts：同上
- [ ] 2.4 content_briefs/content_brief_runs：补齐查询过滤（列已存在）；scheduled_posts：归属 + 查询过滤
- [ ] 2.5 写入口强制归属：内容域 create/import/enqueue 函数签名要求 user_id，缺失即失败

## 3. Web 层作用域

- [ ] 3.1 源与文章路由（/、/feed/*、/articles、/article/*、episode 操作）按 current_user 过滤与校验，跨用户 404
- [ ] 3.2 发布工作台路由（/compose 及其全部子接口）按 current_user 过滤与校验，跨用户 404
- [ ] 3.3 briefs 与 schedule 路由按 current_user 过滤与校验，跨用户 404
- [ ] 3.4 用量/活动页「全部」视角仅 admin 可用，非 admin 请求按"我的"处理

## 4. 后台与通道

- [ ] 4.1 episode 处理 / content_agent 跑批 / YouTube 频道回填 worker：按行上归属执行，产物写归属
- [ ] 4.2 brief 定时跑批：查询过滤补齐，跑批产物归属 brief 的 user_id（沿用现有用量归属链路）

## 5. 验证与门禁

- [ ] 5.1 gate 脚本：扫描内容表 SELECT 是否带 user 过滤、内容域 INSERT 是否带归属列，并入 run_all_gates.py
- [ ] 5.2 端到端验收：双账号（admin + 新账号）对照——新账号全页空列表、直访他人资源 404、双账号互不可见、存量 NULL 数据对双方均不可见、后台产物归属正确
```

## openspec/changes/multi-user-content-isolation/specs/user-content-scoping/spec.md

- Source: openspec/changes/multi-user-content-isolation/specs/user-content-scoping/spec.md
- Lines: 1-90
- SHA256: 7366cf2bea18c9ee7854de09b903e6e05aa36c434b77e380098b22646e80b5d9

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: 内容数据按用户归属

内容域数据表（feeds、episodes、articles、social_posts、standalone_posts、url_sources、uploaded_images、generated_thumbnails、prompt_library、content_briefs、content_brief_runs、scheduled_posts）SHALL 携带可空 `user_id` 归属列；所有写路径 SHALL 在落库时固化触发用户的归属，不允许产生 NULL 归属的新数据。

#### Scenario: 新写入内容归属当前用户

- **WHEN** 已登录用户创建、导入或入队任何内容数据（订阅源、URL 源、帖子、上传图、prompt、brief、定时任务等）
- **THEN** 新行的 `user_id` 为当前用户，即使该数据由后台 worker 实际生成

#### Scenario: recent prompts 随帖子自动隔离

- **WHEN** 用户查看发布工作台的最近 prompts
- **THEN** 仅看到自己 `standalone_posts`（freeform）派生的 prompts，无独立存储需要单独隔离

#### Scenario: 同一 URL 多用户互不相干

- **WHEN** 两个用户各自添加同一 RSS 源或同一 URL 源
- **THEN** 系统为每个用户保留独立行，互不去重、互不共享抓取结果

### Requirement: 列表仅显示当前用户的数据

所有内容域列表查询 SHALL 以当前登录用户过滤；无归属（`user_id` 为 NULL）的存量行 SHALL 对任何用户不可见。

#### Scenario: 新注册账号看到空内容

- **WHEN** 新注册用户登录并浏览订阅源、文章、发布工作台、briefs、定时任务页
- **THEN** 所有列表为空，不显示其他用户或存量的任何数据行

#### Scenario: 双账号数据互不可见

- **WHEN** 用户 A 与用户 B 各自创建内容后分别浏览同一页面
- **THEN** 各自只见自己创建的数据

### Requirement: 跨用户单资源访问被拒绝

对不归属当前用户的单资源访问（详情、编辑、删除、下载等）SHALL 按资源不存在处理，返回 404 而非 403。

#### Scenario: 直访他人资源返回 404

- **WHEN** 用户直接以 URL 访问他人创建的文章、帖子、上传图等资源的详情或操作接口
- **THEN** 返回 404，响应内容与资源确实不存在时一致，不泄露存在性

### Requirement: 后台任务沿用触发者归属

后台 worker（episode 处理、content_agent 跑批、频道回填、brief 定时跑批）SHALL 按数据行上的 `user_id` 归属执行并把产物写入同一归属，不得在 worker 线程中恢复"当前用户"。

#### Scenario: episode 处理产物归属触发者

- **WHEN** 用户入队一个 episode 且后台 worker 完成生成
- **THEN** 产生的文章/帖子行的归属为入队用户，用量记录归属同一用户

#### Scenario: brief 跑批产物归属 brief 主人

- **WHEN** brief 定时跑批命中某用户的 brief
- **THEN** 跑批产物归属该 brief 的 `user_id`，用量沿用该归属

### Requirement: 存量内容数据保留但不再对任何用户可见

升级加列时 SHALL NOT 做任何数据回填或自动划归；存量行 `user_id` 保持 NULL，对所有用户（含 admin）不可见，但数据保留在库中。

#### Scenario: 升级后旧数据对所有人不可见

- **WHEN** 部署本变更后任何用户（含 admin）登录
- **THEN** 升级前产生的无归属数据不出现在任何列表与详情中，系统亦不会将其自动划归任何账号

#### Scenario: 存量数据可手工划归

- **WHEN** 运维者希望找回某条旧数据
- **THEN** 可通过手工 SQL 将该行 `user_id` 设置为某用户使其可见；产品功能不提供自动迁移入口

#### Scenario: 用量与活动历史不受影响

- **WHEN** 升级后用户查看自己的用量与活动记录
- **THEN** 升级前已带 `user_id` 的 usage/activity 历史照常显示，不因内容加列而变化

### Requirement: 用量与活动的「全部」视角仅限管理员

用量页与活动页 SHALL 对非 admin 强制本人视角（忽略任何 `user_id`/`scope` 请求参数）；「全部」及按用户筛选 SHALL 仅 admin 可用。
```

Full source: openspec/changes/multi-user-content-isolation/specs/user-content-scoping/spec.md

