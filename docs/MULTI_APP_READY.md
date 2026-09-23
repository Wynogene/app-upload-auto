# 多 App 常态化

**只读**：检查配置 / 密钥 / ASC 可见性 / 盯盘登记，**不写** Google Play / App Store。

## 一键检查

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1
# Android 需本地代理（如 7892）；iOS 默认直连
python cli.py apps-ready
# 只看某个 App
python cli.py apps-ready --app-id blurams --app-id easelife --app-id boykeep
```

退出码：有 FAIL 项则为 `1`。

## 当前主体关系

| App | Play | Apple | 备注 |
|-----|------|-------|------|
| blurams | 共用 SA `google-play-sa-blurams-easelife.json` | `.env` 全局 | 与 easelife 同团队；iOS/Android 正式轨已真机验证 |
| easelife | 同上 | `.env` 全局 | 盯盘可用 |
| boykeep | 独立 SA `google-play-sa-boykeep.json` | `apps.yaml` 覆盖独立 `.p8` | 用 blurams 密钥查会 404 |
| 安欣看 | 禁用 | 禁用 | 不投入 |

## boykeep iOS（已配置，发版前复核）

凭据已在 `config/apps.yaml` 的 `boykeep.ios`（`key_id` / `issuer_id` / `private_key_path` / `app_store_app_id`）。

发版或换钥前再确认：

```powershell
python cli.py apple-check --app-id boykeep
python cli.py apps-ready --app-id boykeep
```

若换密钥：在 ASC 切到 **boykeep** 主体 → 用户与访问 → 密钥 → 下载 `.p8` 到 `secrets/` → 更新 `apps.yaml` 三行。详见 [IOS_ASC_SETUP.md](./IOS_ASC_SETUP.md)。

## 登记盯盘（只读轮询）

```powershell
# iOS（无需 version-code）；`--execute` 提审成功后会自动登记
python cli.py watch --app-id blurams --platform ios --once --heartbeat-hours 0 --no-notify
# Android（需线上 versionCode）；正式轨 upload-submit 成功后一般会自动登记
python cli.py watch --app-id blurams --platform android --version-code <码> --once --heartbeat-hours 0 --no-notify
```

`--once --no-notify`：只登记/查一次，不发飞书、不写商店。常驻仍靠 `serve` + `data/watch_targets.json`。

## IPA

有包后：

```powershell
python cli.py ipa-check --app-id blurams --ipa "F:\upload-test\xxx.ipa"
```
