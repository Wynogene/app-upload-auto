# Android 三 App 对照表

速查 blurams / easelife / boykeep，避免混用 Play 账号或 `--app-id`。  
终端可执行：`python cli.py apps`（加 `--status` 会向 Play 拉取当前正式版）。

| 项 | blurams | easelife | boykeep |
|----|---------|----------|---------|
| `--app-id` | `blurams` | `easelife` | `boykeep` |
| 包名 | `com.blurams.ipc` | `com.vitec.easelifeEn` | `com.boykeep.ipc1` |
| Play / GCP | 共用 `sd-gcp-2026-7-15` | 同左 | 独立 `boykeep-f3983` |
| SA JSON | `secrets/google-play-sa-blurams-easelife.json` | 同左 | `secrets/google-play-sa-boykeep.json` |
| 调试时正式版码（约） | **1952** | **10420** | **2184** |
| 新 AAB 门槛 | **> 1952** | **> 10420** | **> 2184** |
| 默认轨道（yaml） | internal | internal | internal |

> 正式版 versionCode 以 `python cli.py apps --status` 或 Console 为准；上表为调试期快照。

## 版本说明（releaseNotes）

**默认值与运营手工上传时一致，无需每次传参；也可随时用 `--whats-new` 覆盖。**

| 配置项 | 位置 | 当前值 |
|---|---|---|
| 默认语言 | `apps.yaml` → `android.release_notes_locales`，或 `.env` 的 `RELEASE_NOTES_LOCALES` | `en-US` |
| 默认文案 | `apps.yaml` → `android.release_notes_default`，或 `.env` 的 `RELEASE_NOTES_DEFAULT` | `-General: Bug fixes and system optimizations.` |

优先级：**命令行 `--whats-new` > `apps.yaml` 里该 App 的配置 > `.env` 全局 > 无**

| 轨道 | 参数省略时的行为 |
|------|------------------|
| `production` | 用配置的默认文案；**若既没传参也没配默认文案，则阻断**（避免版本亮点空白） |
| `internal` / `alpha` / `beta` | 同上；若默认文案也为空，则不传该字段，商店不展示版本说明 |

结果里会明确标注本次用的是哪种来源，不会静默：

```
已将 versionCode=10422 发布到 `production`(status=completed)。
版本说明使用默认文案：-General: Bug fixes and system optimizations。
正式版可能进入 Google 审核，请用 status/watch 跟踪。
```

```powershell
# 平时：无需传参，自动用默认文案
python cli.py upload-submit --app-id blurams --platform android \
  --artifact "F:\upload-test\xxx.aab" --track production --allow-production

# 需要自定义时：覆盖默认文案
python cli.py upload-submit --app-id blurams --platform android \
  --artifact "F:\upload-test\xxx.aab" --track production --allow-production \
  --whats-new "1. 修复画面卡顿\n2. 优化设备连接速度"

# 多语言（可重复传入，各自指定语言）
python cli.py release --app-id easelife --platform android --version-code 10421 \
  --track production --allow-production \
  --whats-new zh-CN="修复设备离线问题" \
  --whats-new en-US="Fixed device offline issue"
```

约束与注意：

- 单语言上限 **500 字符**，超长会拦截
- 语言标签形如 `zh-CN` / `en-US`，**必须与商店实际支持的本地化语言一致**。
  目前配置为 `en-US`（与运营手工上传一致）；要改用中文，请先到 Play Console
  「商店设置 → 语言」确认支持 `zh-CN` 后再改
- 格式上不能混用「纯文本」与「语言=文本」；文案里出现的 `=`（如 `修复 a=b 崩溃`）不会被误判

> 说明：默认文案是**运营显式配置**的，与「工具自己编造」有本质区别。
> 历史实现曾硬编码 `upload via app-upload-auto to production` 作为兜底，
> 那会把工具文案直接展示给真实用户，已移除。

## 账号关系

- **blurams + easelife**：同一 Play Console、同一服务账号。  
- **boykeep**：另一套 Google / Play / GCP，**不能**复用 blurams 的 JSON。  
- **安欣看**：已禁用，不在本工具 Android 流程内。

## 常用命令

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

python cli.py apps
python cli.py apps --status

python cli.py status --app-id blurams --platform android
python cli.py upload --app-id easelife --platform android `
  --artifact "F:\upload-test\xxx.aab" --track internal --notify
python cli.py upload-submit --app-id boykeep --platform android `
  --artifact "F:\upload-test\xxx.aab" `
  --track production --allow-production --notify
```

上传前会校验 AAB 包名与 `--app-id` 是否一致；versionCode 已在轨道中会直接拒绝。  
提审后盯盘见 [AFTER_SUBMIT_WATCH.md](./AFTER_SUBMIT_WATCH.md)。boykeep 开通步骤见 [BOYKEEP_ANDROID_SETUP.md](./BOYKEEP_ANDROID_SETUP.md)。

## 产品约定（简）

- 工具**不检测、不依赖**「自管式发布」；是否开启由运营在 Console 控制。  
- 工具只做上传 / 送审 / 状态通知，不代替运营点「发布」。  
- 政策问题请看 Console → 政策状态 + 邮件（API 读不到该页）。
