# 后续扩展清单（boykeep / iOS）

在 blurams / easelife Android 上传 + 盯盘可用之后，按材料到位再扩展。

## boykeep Android

| 需要 | 状态 |
|------|------|
| 独立 Play Console + **独立 GCP 项目**（与 `sd-gcp-2026-7-15` 分离） | 待操作 |
| 服务账号 JSON → `secrets/google-play-sa-boykeep.json` | 待提供 |
| 确认 package `com.boykeep.ipc1` 与 SA 权限（测试轨 + 正式版） | 待提供 |
| [`config/apps.yaml`](../config/apps.yaml) 已预留 `boykeep` 条目 | 已预留 |

**完整逐步方案（另一套登录账号）：** [BOYKEEP_ANDROID_SETUP.md](./BOYKEEP_ANDROID_SETUP.md)

到位后：与 easelife 相同走 `upload` / `upload-submit` / `watch`，无需改核心逻辑（确认 SA 路径即可）。

## iOS（App Store Connect）

| 需要 | 状态 |
|------|------|
| Issuer ID | ✅ 已提供（个人 API 密钥，blurams/easelife 主体） |
| Key ID + `.p8` → `secrets/ApiKey_ABCDE12345.p8` | ✅ 已提供 |
| `.env`：`APPLE_KEY_ID` / `APPLE_ISSUER_ID` / `APPLE_PRIVATE_KEY_PATH` | ✅ 已填 |
| 鉴权连通性（blurams / easelife） | ✅ 已打通（`cli.py apple-check`） |
| 按 App 覆盖凭据（多主体支持） | ✅ 已支持（`apps.yaml` 的 `ios.key_id/issuer_id/private_key_path`） |
| 状态查询 / 盯盘（真实状态映射） | ✅ 已可用（`cli.py status --platform ios`） |
| 上传前校验（`ipa-check`） | ✅ 已可用 |
| IPA 上传到 TestFlight（Build Upload API） | ⏳ 下一步（**Windows 可直传，不需要 Mac**） |
| 提审（`reviewSubmissions` API） | ⏳ 待接入 |
| boykeep（独立主体）的 `.p8` + Issuer ID | ⏳ 待生成后填入 |

**接入与自检说明：** [IOS_ASC_SETUP.md](./IOS_ASC_SETUP.md)

⚠️ 三个必须注意的点：

1. 现有账号用的是**个人 API 密钥**（`ApiKey_*.p8`），JWT 必须带 `sub="user"`，否则恒定 401。工具已自动处理。
2. **API 密钥按 Apple 开发者团队隔离，`Issuer ID` 是团队级的。** 已确认 blurams + easelife 同属一个主体、
   boykeep 是另一个主体（同一 Apple ID 下切换主体可见），因此 boykeep **必须单独申请一套**。
3. 用 A 主体的密钥查 B 主体的 App 会返回 **404 而非 403**（App 对该密钥不可见），极易误判；
   `apple-check --app-id <id>` 现在会显式提示。

计划顺序：先 TestFlight 上传 → 再 ASC 提审；复用同一套 `watch` / 飞书通知。本工具不代替人工在商店后台点「发布给用户」。

## 已完成（本轮）

- **状态查询真实化**：`appStoreState`/`appVersionState` + build `processingState` → `ReviewState` 映射
- **上传前校验**：`ipa-check` 拦截 bundle id 不符 / 版本号未递增 / 构建号重复，传包前就失败
- **多主体凭据**：`apps.yaml` 的 `ios.key_id/issuer_id/private_key_path` 可按 App 覆盖，缺省回退 `.env`

## 下一步（等 IPA）

1. **Build Upload API 上传到 TestFlight**：`POST /v1/buildUploads` → `POST /v1/buildUploadFiles`
   → 分片 PUT → `PATCH uploaded=true` → 轮询至 `VALID`。已实测 `CREATE` 允许，**Windows 可直传**
2. **提审**：`reviewSubmissions` + `reviewSubmissionItems` + 写 `whatsNew`；含「已在审核中则拒绝重复提审」防呆
3. **盯盘接入 iOS**：`watch` / `serve` 心跳通知（Apple 用版本号而非 versionCode）

## 明确不做

- 检测或强制「自管式发布」开关（由运营在 Play Console 自行控制）
- 代替运营在商店后台点「发布给用户」
- 在 `SAFETY_PERSONAL_ONLY=true` 时向业务群发通知或改现网 webhook
