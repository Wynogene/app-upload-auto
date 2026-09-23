# 提审作业队列（最小自愈）

> **硬约束：零影响成功写路径。**  
> 不改 `apple_build_upload` / `google.upload` / `execute_review_submit` 的成功逻辑；  
> 只在外层增加「可落盘作业 + 重试/半成功分流」，调用现有 `upload_and_submit` / `submit`。

## 产品目标（对齐）

```text
飞书「提审」→ 入队（落盘）→ 本机 worker / serve 推进
  → 下载选型 → 上传 → 提审 → 登记盯盘
  → 进程崩溃后 serve 可按阶段恢复（半成功只跑 submit/release）
```

## 阶段（stage）

| stage | 含义 |
|-------|------|
| `queued` | 已受理，待执行 |
| `running` | 正在执行（防并发；超时可回收） |
| `await_retry` | 失败，等待退避后重试 |
| `submit_only` | 上传已成功（已有 build_id / versionCode），只补提审/推进 |
| `wait_valid` | iOS：buildUploads 已 COMPLETE，轮询等到 VALID 后再转 `submit_only` |
| `succeeded` | 终态成功 |
| `failed` | 终态失败（超过 max_attempts） |
| `cancelled` | 人工取消（预留） |

## 落盘

- 路径：`data/submit_jobs.json`（已在 `.gitignore` 的 `data/` 下）
- 每条含：卡片 `value` 快照、`app_id` / `platform`、`build_id` / `version_name` / `version_code`、attempts、last_error、next_run_at

## 推进规则（自愈）

1. **首次 / 无 build_id**：调用现有 
un_upload_submit_job(value)（完整上传+提审）。
2. **结果里上传成功且有 uild_id（iOS）或 ersion_code（Android 半成功）但整体未全成功** → stage=submit_only。
3. **iOS COMPLETE 但尚无 uild_id** → stage=wait_valid（轮询 VALID，禁止整包重传）→ 有 uild_id 后转 submit_only。
4. **submit_only**：只调 AppReleaseService.submit(...)，**不再 upload**。
5. **同 app+platform 去重**：已有非终态作业则复用（orce_new_job 可强制新开）。
6. **可重试失败**：ttempts < max_attempts → wait_retry，指数退避。
7. **
unning 超时回收**（默认 2h）：收回为 wait_retry / queued / wait_valid / submit_only。

## 谁来推进

| 触发 | 行为 |
|------|------|
| 飞书点「提审」 | **先入队**，再在本进程后台线程 `process_submit_job(id)`（即时体验不变） |
| `serve` 定时 | 扫描可运行作业并推进（**崩溃后自愈**依赖此项） |
| CLI `submit-jobs --once` | 手动扫一轮（调试） |

开关：`.env` `SUBMIT_JOBS_ENABLED=true`（默认 true）。为 false 时回退为「仅线程、不落盘」（旧行为）。

## 明确不做（本最小版）

- 跨机器分布式锁 / 多 worker  
- iOS 分片断点续传  
- 改商店 API 成功路径  
- 代替运营点「发布给用户」

## 与包装脚本关系

Windows/Mac 包装脚本仍可作 CLI 整命令重试；**飞书无人值守以本队列为准**。
