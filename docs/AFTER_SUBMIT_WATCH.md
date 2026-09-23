# 新包提审之后：状态轮询与飞书通知

## 版本门槛（再低于此码会 403 已使用）

**以当天 `status` 为准**；下表为近期已知水位：

| app-id | Android versionCode | iOS（营销版本） |
|--------|---------------------|-----------------|
| blurams | **> 1959** | > `5.1049.127`，构建号勿复用 |
| easelife | **> 10428** | > 当前 ASC 上架版本 |
| boykeep | **> 2184** | > 当前 ASC 上架版本 |

> 完整固定命令见 [RELEASE_PLAYBOOK.md](./RELEASE_PLAYBOOK.md)。

## 阶段 0：有新包时立刻执行

### Android

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

# 1) 上传并送审正式版（成功会自动登记 watch_targets）
python cli.py upload-submit --app-id easelife --platform android `
  --artifact "F:\upload-test\新包.aab" `
  --track production --allow-production --notify

# 2) 若未自动登记，或要临时加盯：
python cli.py watch --app-id easelife --platform android `
  --version-code <新versionCode> --once --heartbeat-hours 0 --notify
```

blurams / boykeep 把 `--app-id` 换成对应 id 即可。

### iOS

```powershell
# 须 --execute 才写 ASC；成功后建议手动登记盯盘
python cli.py upload-submit --app-id blurams --platform ios `
  --artifact "F:\upload-test\新包.ipa" --execute --notify

python cli.py watch --app-id blurams --platform ios `
  --once --heartbeat-hours 0 --notify
```

飞书收到「状态变化」或「心跳提醒」后：到 Play Console / ASC 核对进度即可。  
请同时打开 **监控与改进 → 政策和计划 → 政策状态**，查看是否有待办与期限；原因与倒计时以 Console 和邮件为准（**工具读不到该页，盯盘无法覆盖「仅政策待办」**）。  
过审但自管式/手动发布未点、放量比例变化、被拒、iOS 合规/构建卡住等，会尽量用专用标题通知。  
**是否对用户自动上线**由运营在控制台发布设置决定；本工具不代点「发布」。

## API 局限

Tracks API 常把正式版 release 标成 `completed`，**难以精确区分**「审核中 / 已可发布 / 已对用户上线」。通知仅作提示，以 Console 为准。

## 常驻定时（可选）

见 [SCHEDULE_WATCH.md](./SCHEDULE_WATCH.md)。开机/重启后若 serve 异常：`check-serve-watch.ps1` → 必要时再 `start-serve-watch.ps1`。
