# 后续扩展清单

对照当前代码与真机验证进度（以 README 为准）。

## boykeep Android

| 需要 | 状态 |
|------|------|
| 独立 Play Console + **独立 GCP 项目** | ✅ 已配（`boykeep-f3983`） |
| 服务账号 JSON → `secrets/google-play-sa-boykeep.json` | ✅ 已配 |
| package `com.boykeep.ipc1` + SA 权限 | ✅ 已在 `apps.yaml`；发版前用 `apps-ready` / 测轨复核 |
| [`config/apps.yaml`](../config/apps.yaml) `boykeep` 条目 | ✅ 已启用 |

发版命令与 easelife 相同：`upload` / `upload-submit` / `watch`。逐步说明见 [BOYKEEP_ANDROID_SETUP.md](./BOYKEEP_ANDROID_SETUP.md)。

## iOS（App Store Connect）

| 需要 | 状态 |
|------|------|
| Issuer ID / Key ID / `.p8`（blurams/easelife） | ✅ |
| `.env` 全局 Apple 凭据 | ✅ |
| 鉴权连通（`apple-check`） | ✅ |
| 按 App 覆盖凭据（多主体） | ✅ |
| 状态查询 / 盯盘 | ✅（`status` / `watch` / `serve`） |
| 上传前校验（`ipa-check`） | ✅ |
| Build Upload（Windows 直传） | ✅ 默认 dry-run；`--execute` 才写 |
| 提审（`reviewSubmissions` + what's New + 出口合规） | ✅ 默认 dry-run；`--execute` 才写 |
| 提审默认 7 天分批 | ✅ |
| boykeep 独立主体 `.p8` | ✅ 已在 `apps.yaml` |
| 真机验证 | ✅ blurams：更高版本 IPA 上传 + 提审已跑通 |

**接入与自检：** [IOS_ASC_SETUP.md](./IOS_ASC_SETUP.md)  
**上线前必读：** [IOS_RELEASE.md](./IOS_RELEASE.md)

⚠️ 注意：

1. 个人 API 密钥（`ApiKey_*.p8`）JWT 须带 `sub="user"`；工具按文件名自动处理。
2. 密钥按 Apple 团队隔离；blurams + easelife 同主体，boykeep 另一主体，须各自一套。
3. 用错主体密钥查 App 常返回 **404**（不是 403）；`apple-check --app-id <id>` 会提示。

本工具不代替人工在 ASC 点「发布给用户」。

## 已完成（近期）

- Build Upload + reviewSubmissions（`--execute` 闸门）
- ASC JWT 长传自动刷新（约 20 分钟过期）
- 群晖分享链下载（`SYNOLOGY_*`，不改 ai_support）
- Android 正式轨默认约 5% 分批；上传卡住可整包重试
- serve 启动轮询不阻塞 `/health`；重启后可用 `check-serve-watch.ps1` 恢复

## 下一步

1. iOS 提审成功后**自动登记盯盘**（对齐 Android 正式轨）
2. 与 `ai_support` 合并：按契约接发卡按钮 / 群晖（本仓已可独立用 `SYNOLOGY_*`）
3. 运营化：稳定后再按 [OPS_PERSONAL_ONLY.md](./OPS_PERSONAL_ONLY.md) 评估是否关个人模式
4. 可选：iOS 提审中途续跑指引、挂构建防覆盖等防呆

## 明确不做

- 检测或强制「自管式发布」开关（由运营在 Play Console 自行控制）
- 代替运营在商店后台点「发布给用户」
- 在 `SAFETY_PERSONAL_ONLY=true` 时向业务群发通知或改现网 webhook
