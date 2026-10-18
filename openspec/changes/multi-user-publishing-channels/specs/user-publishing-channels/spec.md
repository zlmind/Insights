# Delta Spec: user-publishing-channels

## Purpose

发布通道按用户隔离的行为契约：扩展配对 token、heartbeat 登录态、平台账号记录、agent job 的认领与读取、发布通道选用全部归属到用户；浏览器扩展协议保持零改动。调度时段与每日限额保持全局（沿用 multi-user-content-isolation §3 约束，不在本能力内）。

## ADDED Requirements

### Requirement: 配对 token 按用户签发与校验

扩展配对 token 必须归属到具体用户：配对码在已登录会话中铸造并绑定铸造者，匿名兑换继承该归属并签发绑定用户的专属 token；每个用户可持有多个 token（多浏览器实例），token 哈希后落库并可吊销；校验以查表解析用户身份的方式进行，无效或已吊销 token 一律拒绝。

#### Scenario: 谁配对 token 归谁

- **WHEN** 用户 A 在登录会话中生成配对码，任一浏览器用该码完成兑换
- **THEN** 签发的 token 绑定用户 A，之后的 heartbeat、claim、events 请求均以 A 的身份解析

#### Scenario: 无效或已吊销 token 被拒绝

- **WHEN** 请求携带不存在的 token、格式错误的凭证或已被吊销（revoked）的 token 访问任何 /agent/* 端点
- **THEN** 返回 401，且不产生任何心跳、认领或数据副作用

#### Scenario: 吊销立即生效

- **WHEN** 用户在账号页吊销自己名下的某个 token 后，持有该 token 的扩展再次请求
- **THEN** 该扩展的后续请求返回 401，同用户其他 token 不受影响

### Requirement: heartbeat 与登录态按用户隔离

扩展心跳上报的平台登录状态必须按 token 解析出的用户分桶存储与查询；任何用户只能看到自己的登录态快照，页面上的通道在线状态按当前用户呈现。

#### Scenario: 登录态互不可见

- **WHEN** 用户 A 的扩展离线（心跳过期）而用户 B 的扩展在线
- **THEN** A 登录后看到的通道状态为离线/未连接，B 看到的仍为在线，二者互不影响

#### Scenario: 心跳按用户归属

- **WHEN** 两个用户各自的扩展实例同时向服务端上报心跳
- **THEN** 每份心跳按其 token 解析出的用户落桶存储，查询任一用户的登录态只返回该用户自己的数据

### Requirement: 平台账号记录按用户隔离

`social_accounts` 表的行必须归属到用户（加 `user_id`，唯一键放宽为 platform + external_id + user_id），所有展示按当前用户过滤，不跨用户泄露；该表为遗留/展示数据，发布通道判定只依据本人 heartbeat 登录态。

#### Scenario: 遗留账号记录不跨用户展示

- **WHEN** 非 admin 用户打开账号页，而库中存在划归 admin 的存量平台账号记录
- **THEN** 该用户看不到 admin 的记录；admin 仅能看到自己名下的记录

#### Scenario: 同平台同账号可分属两用户

- **WHEN** 用户 A 与用户 B 各自的记录涉及同一平台同一 external_id
- **THEN** 两行均可存在（唯一键含 user_id），互不冲突

### Requirement: agent job 按用户认领与读取

发布任务必须归属帖子所属用户：入队时写入该用户标识；扩展认领只返回 token 所属用户本人的任务；任务事件回报与任务读取（队列页、单任务详情）均校验/过滤用户归属，他人的任务不可认领、不可回报、不可见。

#### Scenario: 只认领本人任务

- **WHEN** 用户 A 的扩展以 A 的 token 请求认领任务，队列中同时存在 A 与 B 的待处理任务
- **THEN** 只返回 A 的任务；B 的任务保持待处理，由 B 的扩展认领

#### Scenario: 事件回报校验归属

- **WHEN** 持用户 A token 的扩展对属于用户 B 的任务上报事件（进度/完成/失败）
- **THEN** 服务端拒绝该回报（404），B 的任务状态不受影响

#### Scenario: 任务读取按用户过滤

- **WHEN** 用户打开发布任务队列页或请求单个任务详情
- **THEN** 仅能看到自己名下的任务；访问他人任务返回 404

### Requirement: 发布严格按帖主通道

发布入队与执行必须按帖子的归属用户选用该用户自己的通道（本人 heartbeat 登录态）；他人通道不可见、不可用、不得兜底；通道不可用时任务保持排队，原因在展示层按本人当前登录态派生标注，不入库。

#### Scenario: 只用本人通道发出

- **WHEN** 用户 A 发布帖子且 A 的目标平台登录态在线
- **THEN** 任务入队归属 A，由 A 的扩展使用 A 浏览器内的登录态完成发布

#### Scenario: 通道不可用不误发不降级

- **WHEN** 用户 A 发布目标平台时 A 的该平台登录态异常，而用户 B 同平台在线
- **THEN** A 的任务保持排队不发送，页面展示派生原因（平台未连接/登录态异常）；绝不借 B 的通道发出

### Requirement: 存量数据划归 admin 且迁移幂等

启用用户归属时，既有全局共享数据一次性划归 admin（第一个账号）：旧全局扩展 token 保留有效并映射为 admin 名下 token（已配对扩展不断线）；存量 social_accounts 行与存量 agent job 回填 admin 归属；迁移具备幂等标志守护，重复启动不重复回填。

#### Scenario: 旧全局 token 不断线

- **WHEN** 服务升级启用 token 表后，持有升级前全局 token 的既有扩展实例上报心跳或认领任务
- **THEN** 该 token 校验通过并解析为 admin 身份，行为与升级前一致

#### Scenario: 迁移幂等

- **WHEN** 服务携带已完成迁移的数据库二次启动
- **THEN** 不重复回填、不重复建映射，存量数据归属保持 admin 不变
