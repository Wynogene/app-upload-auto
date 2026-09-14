# boykeep Android：独立账号操作方案

boykeep 与 blurams / easelife **不在同一套 Google 登录 / Play 开发者账号**下，必须单独建 GCP 项目、单独开 API、单独服务账号，**不能**复用 `sd-gcp-2026-7-15` 或 `secrets/google-play-sa-blurams-easelife.json`。

本仓库已预留：

| 项 | 值 |
|----|-----|
| CLI `--app-id` | `boykeep` |
| package | `com.boykeep.ipc1` |
| SA 路径 | `secrets/google-play-sa-boykeep.json` |
| 配置 | [`config/apps.yaml`](../config/apps.yaml) 中 `play_console_account: boykeep_separate` |

---

## 账号关系（务必分清）

```text
【账号 A】blurams + easelife
  Play Console（共用）
  GCP 项目：sd-gcp-2026-7-15
  SA：play-publisher@sd-gcp-2026-7-15... → google-play-sa-blurams-easelife.json

【账号 B】boykeep（另一套登录）
  Play Console（独立）
  GCP 项目：需新建（或确认 Owner 是账号 B）
  SA：新建 → google-play-sa-boykeep.json
```

- 在 [Cloud Console](https://console.cloud.google.com/) 操作时，用 **boykeep 对应的 Google 账号**登录。
- 顶栏项目不要切到 `sd-gcp-2026-7-15`。
- 若曾看到项目 `boykeep-f3983` 但报 `serviceusage.services.enable`：说明该项目存在，但**当前登录账号不是 Owner/Editor**。要么换有权限的账号，要么让 Owner 授权/代为启用，要么用账号 B **新建一个你拥有的项目**（推荐，权责清晰）。

---

## 阶段 1：用 boykeep 账号创建 GCP 项目

用 **boykeep 的 Google 账号**登录 Cloud Console：

1. [创建项目](https://console.cloud.google.com/projectcreate)  
   - 名称示例：`boykeep-play`  
   - 记下 **项目 ID**（如 `boykeep-play-xxxxxx`）
2. 确认你在该项目 IAM 中是 **所有者 Owner**  
   - [IAM](https://console.cloud.google.com/iam-admin/iam)

> 若公司已有 `boykeep-f3983` 且必须用它：请该项目 Owner 把你加成 **编辑者** 或 **Service Usage 管理员**，再继续阶段 2；不要用不属于账号 B 的项目硬开 API。

---

## 阶段 2：启用 Google Play Android Developer API

在**刚建好的（或已获权的）boykeep GCP 项目**下：

1. 打开 API 库（把 URL 里的项目换成你的项目 ID）：  
   `https://console.cloud.google.com/apis/library/androidpublisher.googleapis.com?project=你的项目ID`
2. 点击 **启用**
3. 到 **API 和服务 → 已启用的 API** 确认列表中有  
   **Google Play Android Developer API**（亦称 Android Publisher API）

此时若再出现 `serviceusage.services.enable`，说明当前账号对该项目仍无启用权限，回到阶段 1 处理 IAM。

---

## 阶段 3：创建服务账号并下载 JSON

仍在同一 GCP 项目：

1. **IAM 和管理 → 服务账号 → 创建服务账号**  
   - 名称示例：`play-publisher-boykeep`  
   - 角色：可先不配 GCP 角色（Play 发布权限在 Play Console 里授，不靠 GCP Editor）
2. 进入该服务账号 → **密钥 → 添加密钥 → 创建新密钥 → JSON** → 下载
3. 将文件保存到本仓库（勿提交 git）：

```text
f:\app-upload-auto\secrets\google-play-sa-boykeep.json
```

4. 打开 JSON 确认：
   - `project_id` = 你的 boykeep GCP 项目 ID（**不是** `sd-gcp-2026-7-15`）
   - `client_email` = `某名称@你的项目ID.iam.gserviceaccount.com`  
   记下 **client_email**，下一步要用。

---

## 阶段 4：在 boykeep 的 Play Console 授权服务账号

用 **boykeep 的 Play 开发者账号**登录 [Play Console](https://play.google.com/console)：

1. 确认当前开发者账号下能看到 App **Boykeep**（`com.boykeep.ipc1`）
2. **用户和权限 → 邀请新用户**
3. 邮箱填服务账号的 **client_email**（整段 `@….iam.gserviceaccount.com`）
4. 权限建议（与 blurams 对齐，按你们规范勾选）：
   - 至少能查看应用信息、管理测试轨道发布
   - 若要正式版提审：勾选 **发布为正式版** 等正式轨相关权限
5. 应用权限里勾选 **Boykeep / com.boykeep.ipc1**（不要勾到别的账号下的 App）
6. 发送邀请并确认生效（服务账号通常即时可用）

可选：调试期可在 Play Console 临时开**自管式发布**，避免试传包立刻对用户可见；**本工具不检测、不依赖该开关**，后续由运营自行决定是否开启。

**已确认（2026-09-08）：** API `status` 正式版为 versionCode **2184**；现有 AAB 试传在上传前校验阶段即拒绝（versionCode 已在轨道中）。

## 上传增强（全应用通用）

- 上传前解析 AAB：`packageName` / `versionCode`，与 `apps.yaml` 不一致则立即失败
- 若 versionCode 已在各轨道出现，跳过实际上传并提示改用 `release`
- 大包经代理 SSL/超时会自动重试；飞书文案区分「版本已使用」与「网络/代理中断」

---

## 阶段 5：本仓库侧确认

1. 文件存在：`secrets/google-play-sa-boykeep.json`
2. [`config/apps.yaml`](../config/apps.yaml) 中 boykeep 已指向该路径（已预留，一般不用改）
3. `.env` 里的 `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` 可继续指向 blurams 共用文件——**boykeep 以 apps.yaml 每应用路径为准**，互不覆盖
4. 代理：`HTTP_PROXY` / `HTTPS_PROXY` 与现网一致（访问 Google API 需要）

---

## 阶段 6：连通性验证（先 status，再上传）

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

# 1) 只查状态，确认 SA + API + Play 权限打通
python cli.py status --app-id boykeep --platform android --notify

# 2) 有新 AAB 后再上传（建议先 internal）
python cli.py upload --app-id boykeep --platform android `
  --artifact "F:\upload-test\com.boykeep.ipc1_xxx.aab" `
  --track internal --notify

# 3) 正式版需显式允许（--allow-production）
# python cli.py upload-submit --app-id boykeep --platform android `
#   --artifact "F:\upload-test\xxx.aab" `
#   --track production --allow-production --notify
```

成功标准：

- `status` 能列出 production / internal 等轨道（或空轨但无 401/403 权限错误）
- 飞书私聊能收到结果（个人模式）

常见失败：

| 现象 | 原因 |
|------|------|
| API 未启用 / 403 Service Disabled | 阶段 2 未在正确项目启用 |
| 权限不足 / 403 | Play Console 未邀请 SA 或未勾选该 App/轨道 |
| 找不到 JSON | 路径或文件名与 apps.yaml 不一致 |
| 误用 blurams 的 SA | JSON 的 `project_id` 仍是 `sd-gcp-2026-7-15` |

提审后盯盘同其它 App：见 [AFTER_SUBMIT_WATCH.md](./AFTER_SUBMIT_WATCH.md)。

---

## 检查清单（可打勾）

- [ ] 使用 **boykeep Google 账号**登录 Cloud Console  
- [ ] 新建（或获权）GCP 项目，本人为 Owner/可启用 API  
- [ ] 已启用 **Google Play Android Developer API**  
- [ ] 已创建 SA 并下载 JSON → `secrets/google-play-sa-boykeep.json`  
- [ ] JSON 的 `project_id` **不是** `sd-gcp-2026-7-15`  
- [ ] 在 **boykeep Play Console** 邀请 `client_email` 并勾选 `com.boykeep.ipc1`  
- [ ] （可选，仅调试）临时自管式发布——非工具前提，运营自行控制  
- [ ] `cli.py status --app-id boykeep` 通过  

---

## 不要做的事

- 不要把 blurams 的 SA JSON 复制改名当成 boykeep 用（Play 账号不通）
- 不要在 `sd-gcp-2026-7-15` 上「顺便」给 boykeep 开权限指望跨账号发布
- 不要把 SA JSON 提交进 git 或发到群聊

材料齐后告诉我，可在本机帮你跑 `status` / 试传。
