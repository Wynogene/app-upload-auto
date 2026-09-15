# 多 App 常态化（真上传前）

**只读**：检查配置 / 密钥 / ASC 可见性 / 盯盘登记，**不写** Google Play / App Store。

## 一键检查

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1
# Android 需本地代理（如 7892）；iOS 默认直连
python cli.py apps-ready
# 只看某个 App
python cli.py apps-ready --app-id blurams --app-id easelife
```

退出码：有 FAIL 项则为 `1`。

## 当前主体关系

| App | Play | Apple | 备注 |
|-----|------|-------|------|
| blurams | 共用 SA `google-play-sa-blurams-easelife.json` | `.env` 全局 | 与 easelife 同团队 |
| easelife | 同上 | `.env` 全局 | 盯盘已跑 |
| boykeep | 独立 SA `google-play-sa-boykeep.json` | **需独立 .p8** | 用 blurams 密钥查会 404 |
| 安欣看 | 禁用 | 禁用 | 不投入 |

## boykeep iOS 缺口（人工）

1. ASC 左上角切到 **boykeep** 主体  
2. 用户与访问 → 密钥 → 生成 API 密钥，下载 `.p8` 放到 `secrets/`  
3. 在 `config/apps.yaml` 的 boykeep.ios 填写：

```yaml
key_id: "……"
issuer_id: "……"
private_key_path: secrets/AuthKey_xxxx.p8   # 或 ApiKey_xxxx.p8
app_store_app_id: "在 boykeep 主体下核对的数字 ID"
```

4. 再跑：`python cli.py apple-check --app-id boykeep` / `python cli.py apps-ready --app-id boykeep`

详见 [IOS_ASC_SETUP.md](./IOS_ASC_SETUP.md)。

## 登记盯盘（只读轮询）

```powershell
# iOS（无需 version-code）
python cli.py watch --app-id blurams --platform ios --once --heartbeat-hours 0 --no-notify
# Android（需线上 versionCode）
python cli.py watch --app-id blurams --platform android --version-code <码> --once --heartbeat-hours 0 --no-notify
```

`--once --no-notify`：只登记/查一次，不发飞书、不写商店。常驻仍靠 `serve` + `data/watch_targets.json`。

## IPA

`F:\upload-test` 无 IPA 时跳过 `ipa-check`；有包后再：

```powershell
python cli.py ipa-check --app-id blurams --ipa "F:\upload-test\xxx.ipa"
```
