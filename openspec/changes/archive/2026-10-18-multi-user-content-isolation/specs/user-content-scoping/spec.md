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

#### Scenario: 非 admin 固定本人视角

- **WHEN** 非 admin 用户请求用量或活动页，无论是否附带 `user_id` 或 `scope=all` 参数
- **THEN** 页面只渲染其本人数据，请求参数被忽略

#### Scenario: admin 可用全部视角

- **WHEN** admin 请求用量或活动页并选择「全部」或指定某用户
- **THEN** 页面按所选范围渲染对应数据
