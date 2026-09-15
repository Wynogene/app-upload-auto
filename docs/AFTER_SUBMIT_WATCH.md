# 新包提审之后：状态轮询与飞书通知

## 版本门槛（再低于此码会 403 已使用）

| app-id | 需 versionCode |
|--------|----------------|
| blurams | **> 1952** |
| easelife | **> 10420** |

## 阶段 0：有新 AAB 时立刻执行

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

# 1) 上传并送审正式版
python cli.py upload-submit --app-id easelife --platform android `
  --artifact "F:\upload-test\新包.aab" `
  --track production --allow-production --notify

# 2) 立刻盯该 versionCode（成功后终端也会打印建议命令）
python cli.py watch --app-id easelife --platform android `
  --version-code <新versionCode> --interval 30 --heartbeat-hours 12 --notify
```

blurams 把 `--app-id` 换成 `blurams` 即可。

飞书收到「状态变化」或「心跳提醒」后：到 Play Console / ASC 核对进度即可。  
请同时打开 **监控与改进 → 政策和计划 → 政策状态**，查看是否有待办与期限；原因与倒计时以 Console 和邮件为准（**工具读不到该页，盯盘无法覆盖「仅政策待办」**）。  
过审但自管式/手动发布未点、放量比例变化、被拒、iOS 合规/构建卡住等，会尽量用专用标题通知。  
**是否对用户自动上线**由运营在控制台发布设置决定；本工具不代点「发布」。

## API 局限

Tracks API 常把正式版 release 标成 `completed`，**难以精确区分**「审核中 / 已可发布 / 已对用户上线」。通知仅作提示，以 Console 为准。

## 常驻定时（可选）

见 [SCHEDULE_WATCH.md](./SCHEDULE_WATCH.md)。
