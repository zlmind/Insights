# Tasks: admin-user-management

<!-- review_mode=standard：最终轻量审查 PASS（0 CRITICAL/IMPORTANT，5 MINOR）。
     MINOR-1 接受：POST-only admin 端点对错方法探针 405/OPTIONS 与不存在路径 404 可分辨——Flask 框架默认、全站一致、零数据暴露，spec 要求响应体一致而非方法侧信道消除，修法属全站路由姿态变更。
     MINOR-2/3/4/5 已修（2979184）：审计去身份化覆盖 login_failed 行、onclick 用户名 tojson、缩略图路径前缀检查加固、e2e 播种名 uuid 防撞；scoped re-review CLEAN。 -->

## 1. 数据层

- [x] 1.1 用户删除级联函数：单事务逐表清理该用户全部内容数据（表清单与 multi-user-content-isolation 对齐）+ agent_tokens + users 行；activity_log/usage_events 保留但去身份化
- [x] 1.2 密码更新函数（werkzeug 哈希）；最后一个 admin 保护与"不可删除自己"的判定函数

## 2. Web 层

- [x] 2.1 用户管理页路由（列表/重置密码/删除用户），admin 门控对外 404（不动 /settings 的 403 行为）
- [x] 2.2 删除前置校验：禁止删除自己、禁止删除最后一个 admin；删除二次确认（输入用户名）+ 将删除的数据量级提示

## 3. UI

- [x] 3.1 用户管理模板（沿用 .pd 组件库）+ base.html 侧边栏 admin 区块入口（仅 admin 渲染）+ zh.json 词条

## 4. 验证

- [x] 4.1 端到端验收：admin 可列表/重置/删除；非 admin 访问管理页与接口均 404；删除后该用户数据在库中不可见、其扩展 token 失效（若 ② 已落地）；删除自己/最后一个 admin 被拒绝
