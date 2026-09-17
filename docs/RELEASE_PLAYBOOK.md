# 发版手册（固定命令收口）

目标：少手敲。默认 **个人调试**（`SAFETY_PERSONAL_ONLY=true`）→ 飞书**只私聊你**，不进业务群。  
本工具**不代点**商店「发布 / 全量」。政策页仍须人工看 Console。

> 真要动商店时：下面带 `upload` / `upload-submit` 的命令会写 Play；先 `-WhatIf` 预览。

## 0. 每次发版前（只读）

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

python cli.py apps-ready --app-id <app>          # 配置/密钥/盯盘覆盖
python cli.py status --app-id <app> --platform android --no-notify
python cli.py status --app-id <app> --platform ios --no-notify
```

版本门槛（须**严格大于**当前正式版码；以 `status` 为准）：

| app-id | Android versionCode 须 > | iOS 版本号建议 |
|--------|--------------------------|----------------|
| blurams | **1952** | > `5.1049.125`，构建号勿复用 |
| easelife | **10428** | > 当前 ASC 上架版本 |
| boykeep | **2184** | 须先配独立 Apple 密钥 |

## 1. Android：上传 → 提审 → 盯盘（一条脚本）

### 预览（不写商店）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\release-android.ps1 `
  -AppId easelife `
  -Artifact "F:\upload-test\xxx.aab" `
  -Track production `
  -WhatIf
```

### 内部测试轨（推荐先测）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\release-android.ps1 `
  -AppId easelife `
  -Artifact "F:\upload-test\xxx.aab" `
  -Track internal `
  -Notify
```

### 正式版送审（会写 Play production）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\release-android.ps1 `
  -AppId easelife `
  -Artifact "F:\upload-test\xxx.aab" `
  -Track production `
  -AllowProduction `
  -Notify
```

脚本等价于：

1. `upload-submit …`（成功会**自动登记** `data/watch_targets.json`）  
2. 打印建议的 `watch` / 说明常驻 `serve` 已会扫登记目标  

默认分批：正式版未传 `-Rollout` 时用配置默认（常为 5%）；全量加 `-Rollout 100`。

### 等价手敲（不跑脚本时）

```powershell
python cli.py upload-submit --app-id easelife --platform android `
  --artifact "F:\upload-test\xxx.aab" `
  --track production --allow-production --notify

# 终端会提示 versionCode；常驻 serve 已开则一般不必再开 watch。
# 临时加盯：
python cli.py watch --app-id easelife --platform android `
  --version-code <新码> --once --heartbeat-hours 0 --no-notify
```

## 2. iOS：上传 → 提审 → 分发（上线前必读）

**完整流程、与 Android 差异、发布方式/分批坑点、检查清单：**  
见 **[IOS_RELEASE.md](./IOS_RELEASE.md)**（请先读完再真传包）。

```powershell
python cli.py ipa-check --app-id blurams --ipa "F:\upload-test\xxx.ipa"   # 只读校验
python cli.py ios-whats-new --app-id blurams                              # 提审前预览补全 what's New（默认不写）
# python cli.py ios-whats-new --app-id blurams --version 5.1049.126 --apply  # 确认后再写
python cli.py status --app-id blurams --platform ios --no-notify
python cli.py watch --app-id blurams --platform ios --once --heartbeat-hours 0 --no-notify
```

要点（防再踩 Android 那种「默认全量」坑）：

- 传 IPA 只到 **TestFlight 构建**，不等于对用户上架。
- iOS **不能** `--rollout 5` 自定义比例；只有「是否 7 天分批」。
- 「过审后自动发布」且**未开分批** → 过审后接近全量（自动更新用户）。
- 要闸门：用 **手动发布**；工具**永不代点**「发布给用户 / 全量」。

上传 / 提审 API：Build Upload 与 `reviewSubmissions` 尚未接通，见 [IOS_ASC_SETUP.md](./IOS_ASC_SETUP.md) / [ROADMAP_NEXT.md](./ROADMAP_NEXT.md)。接通后命令形态对齐 Android，但发布方式/分批必须显式确认（见 IOS_RELEASE 第 3 节）。

## 3. 常驻盯盘（已装方式 B 则跳过）

```powershell
powershell -File .\scripts\windows\start-serve-watch.ps1
# 健康巡检（仅私聊你，见 OPS 文档）
powershell -File .\scripts\windows\check-serve-watch.ps1 -NotifyIfDown
```

详见 [SCHEDULE_WATCH.md](./SCHEDULE_WATCH.md)、[MULTI_APP_READY.md](./MULTI_APP_READY.md)、[SAFE_RUNBOOK.md](./SAFE_RUNBOOK.md)。
