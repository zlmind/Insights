# 验证报告：self-host-static-assets

- Date: 2026-10-18
- verify_mode: full（comet-state scale：4 任务 / 1 capability / 15 文件）
- 范围: 45f6d20..HEAD（9 个实现文件 + 6 个 OpenSpec 产物，+282/−22，4 提交）

## Summary

| 维度 | 结果 |
|------|------|
| Completeness | tasks.md 4/4 勾选（0 未勾） |
| Correctness | delta spec 3 场景全部由新鲜证据覆盖（见映射表） |
| Coherence | design.md 4 项决策逐项落实，无偏差 |

## 新鲜验证证据（本报告当日运行）

1. **tasks.md**：unchecked = 0。
2. **gate**：`venv/Scripts/python.exe scripts/check_vendor_assets.py` → `VENDOR_ASSETS_OK`（vendor 三文件存在、模板零 jsdelivr、五模板编译通过、静态路由 200、登录页断言）。
3. **在线实测**（jsdelivr 在本机当前不可达，即离线场景本身）：`/static/vendor/*` 三 URL 均 200（232914 / 80421 / 47956 字节）；`/login` 渲染引用 `vendor/bootstrap.min.css` 且零 jsdelivr。
4. **供应链**：bootstrap css/js 双镜像 sha256 一致（npmmirror vs unpkg），sha384 与 base.html 原有官方 SRI 值完全一致；marked 18.1.1 UMD 经 node 实测 `marked.parse()` 输出正确。
5. **安全**：diff 无密钥类字符串；故意移除的 integrity/crossorigin 属性仅对第三方 CDN 有意义（已在模板注释与 design.md 记录）。

## delta spec ↔ 验证映射

| Requirement / Scenario | 证据 |
|---|---|
| 关键前端资源本地提供 / CDN 不可达时页面完整可用 | 本机 jsdelivr 实际不可达条件下，页面资源全部本地 200（证据 3）；bootstrap JS 本地可用 → 下拉/弹层/折叠恢复 |
| 关键前端资源本地提供 / vendor 资源可经应用获取 | 三 URL 200 且字节与下载一致（证据 2、3） |
| 模板不依赖境外 CDN 关键资源 / 无残留 | gate 断言 grep=0（证据 2） |

## 轻量 6 项（与 full 并行核对）

| # | 检查 | 结果 |
|---|------|------|
| 1 | tasks 全勾 | PASS |
| 2 | 改动文件与 tasks 一致 | PASS（9 实现文件全部在 tasks 范围内） |
| 3 | 构建通过 | PASS（build_command = gate 脚本，guard 实跑） |
| 4 | 相关测试通过 | PASS（gate 即本项目模板层测试；无其他测试框架） |
| 5 | 无安全问题 | PASS |
| 6 | 代码审查 | 跳过——hotfix preset `review_mode: off`，机械性引用切换+哈希三重校验，无逻辑分支 |

## 已接受的偏差 / 备注

- design.md 决策 2 要求「版本号记录在 tasks/提交信息」：marked 18.1.1 已记录于提交 `4b07558` 与文件头横幅，无独立 README。
- gate 脚本未注册进 `run_all_gates.py`（控制 hotfix 范围）；如需总闸覆盖可作 follow-up。
- LF→CRLF 警告：git 会对 vendor 文件做行尾归一，对 CSS/JS 无功能影响（integrity 属性已移除，无 SRI 失效风险）。
- 5001 dev server 在构建途中曾死亡（PID 30828 消失，非模板改动所致——无 .py 变更、reloader 不因模板重启）；已重启并确认扩展心跳正常。

## 结论

**全部检查 PASS，无 CRITICAL / IMPORTANT 未决项。Ready for archive.**
