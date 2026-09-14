# easelife Android 发布 / 提审流程（个人调试）

与 blurams **同一 Play Console、同一服务账号**（`secrets/google-play-sa-blurams-easelife.json`）。

| 项 | 值 |
|----|-----|
| app-id | `easelife` |
| package | `com.vitec.easelifeEn` |
| 当前正式版（已验证） | versionCode **10420** / `5.1054.4.420` |

## 推荐命令

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

# 查各轨道
python cli.py status --app-id easelife --platform android --notify

# 上传到内部测试（versionCode 必须 > 当前库中已用码）
python cli.py upload --app-id easelife --platform android `
  --artifact "F:\upload-test\xxx.aab" --track internal --notify

# 上传并推进正式版送审（需新包；显式允许 production）
python cli.py upload-submit --app-id easelife --platform android `
  --artifact "F:\upload-test\xxx.aab" `
  --track production --allow-production --notify

# 已有 versionCode 仅推进轨道（若已在目标轨道且 status 相同会防呆跳过）
# python cli.py release --app-id easelife --platform android `
#   --version-code 10421 --track production --allow-production --notify

# 提审后盯盘
python cli.py watch --app-id easelife --platform android `
  --version-code 10421 --interval 30 --heartbeat-hours 12 --notify
```

提审后流程见 [AFTER_SUBMIT_WATCH.md](./AFTER_SUBMIT_WATCH.md)；常驻定时见 [SCHEDULE_WATCH.md](./SCHEDULE_WATCH.md)。
## 已验证（2026-09-07）

- `status`：API / 代理 / 服务账号权限正常；飞书私信正常
- 现有 AAB `…5.1054.4.420…` 上传 → `403 Version code 10420 has already been used`（包已上过正式版，属预期）

## 注意

- 重复上传已用过的 versionCode 会失败；要新一轮送审需 **> 10420** 的新 AAB
- 正式版必须加 `--track production --allow-production`
- **关于自管式发布：** 调试期可开，避免试传立刻对用户可见；**工具不检测、不依赖该开关**，是否开启由运营在 Play Console 自行控制。本工具只做上传/送审，不代替运营点「发布」。
