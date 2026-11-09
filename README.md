# astrbot_plugin_github_notify

轮询 GitHub 仓库，把 **Issue / Release / Push** 推送到指定 QQ 群或 QQ 号。无公网、不需要 GitHub Actions 或 Webhook，装上插件配好 Token 就能用。

- 每个仓库可以配置**不同的群号 / QQ 号 / UMO 会话**
- Release 自动用 LLM 总结更新日志（字数、提示词、provider 均可配置）
- Release 自动发送附件，优先 `*.apk` / `*.exe`，优先 64 位 / `arm64-v8a`
- 事件去重、首次运行基线、ETag 缓存、限流退避
- 不打印 GitHub token

## 安装

1. 把 `astrbot_plugin_github_notify` 目录放进：

```
AstrBot/data/plugins/astrbot_plugin_github_notify/
```

2. 重启 AstrBot，或在 WebUI 插件页点「重载插件」。
3. 建议先在 WebUI 插件配置里填写 `github_token` 和仓库目标，再用 `/github_notify_test` 测试通道。

要求 AstrBot >= 4.10.4（使用 `template_list` 配置与插件 KV 存储）。无额外第三方依赖，HTTP 使用 AstrBot 自带的 `aiohttp`。

## 配置项

### 全局

| 名称 | 默认值 | 说明 |
|---|---|---|
| `github_token` | 空 | 必填建议：提高速率限制；私有仓库、私有 Release 附件必须填写 |
| `repositories` | `[]` | 仓库列表，每项可单独配置事件、目标、分支、附件规则 |
| `poll_interval` | `300` | 轮询间隔秒数，建议 120–600 |
| `platform_id` | 空 | aiocqhttp 平台实例 ID；留空自动选第一个 aiocqhttp |
| `group_ids` | `[]` | 全局默认 QQ 群号 |
| `user_ids` | `[]` | 全局默认 QQ 号（私聊） |
| `umo_targets` | `[]` | 全局默认会话 UMO，可在 WebUI 选择 |
| `issue_actions` | `opened` | Issue 通知动作，可多选 `opened` / `closed` / `reopened` / `labeled` |
| `issue_body_chars` | `200` | Issue 正文摘要字数，0 为不展示 |
| `push_max_commits` | `5` | Push 最多展示几条提交 |
| `include_prerelease` | `true` | 是否通知预发布 Release |
| `notify_on_first_run` | `false` | 首次运行是否补发当前事件；默认只建立基线，避免刷屏 |
| `summary_provider` | 空 | 摘要使用的 LLM，空则用 AstrBot 默认模型 |
| `summary_max_chars` | `100` | Release 摘要默认字数 |
| `summary_instruction` | 内置 | 摘要提示词，支持 `{max_chars}` |
| `summary_input_chars` | `6000` | 交给 LLM 的发布说明最大字符数 |
| `send_assets` | `true` | 是否发送 Release 附件 |
| `asset_patterns` | `*.apk`, `*.exe` | 默认附件匹配，靠前的规则优先 |
| `asset_prefer_64bit` | `true` | 优先 64 位 / v8a |
| `asset_prefer_keywords` | arm64-v8a 等 | 64 位优先关键字 |
| `asset_avoid_keywords` | armeabi-v7a 等 | 32 位降权关键字 |
| `asset_max_count` | `1` | 每个 Release 最多发几个附件，0 表示全部 |
| `asset_max_mb` | `50` | 单附件大小上限，超过改发下载链接 |
| `asset_fallback_all` | `false` | 没有匹配附件时是否发送全部 |
| `timeout` | `30` | GitHub 请求超时秒数 |

### 单个仓库

| 名称 | 说明 |
|---|---|
| `repo` | `owner/repo`，也可粘贴 GitHub 链接 |
| `enabled` | 是否启用 |
| `issue` / `release` / `push` | 该仓库监听哪些事件 |
| `branches` | Push 监听分支；留空全部；支持 `release/*` |
| `group_ids` | 该仓库专用 QQ 群号 |
| `user_ids` | 该仓库专用 QQ 号 |
| `umo_targets` | 该仓库专用会话 UMO |
| `asset_patterns` | 该仓库专用附件匹配 |
| `summary_max_chars` | 该仓库专用摘要字数，0 继承全局 |

> 只要仓库里配置了任意一个目标（群号 / QQ 号 / UMO），就**只用该仓库的目标**；三项都为空才继承全局目标。这样可以把不同仓库推到不同群。

## 配置推送目标

- QQ 群：填群号，例如 `123456789`，插件会生成 `平台ID:GroupMessage:123456789`
- QQ 私聊：填 QQ 号，例如 `987654321`，生成 `平台ID:FriendMessage:987654321`
- UMO：在 WebUI 的「推送到会话（UMO）」里选择已有会话，或手动粘贴 `平台ID:GroupMessage:群号`
- 如果 AstrBot 同时运行多个 aiocqhttp 实例，需要填写 `platform_id`；只有一个时可以留空自动识别。

## 使用

配置好仓库和目标后，等待轮询即可。推送示例：

```text
[新 Issue] AieXile/AnimeTrack #128
标题：搜索页在深色模式下闪白
作者：@someone
标签：bug, ui
摘要：深色模式下进入搜索页会闪白…
https://github.com/AieXile/AnimeTrack/issues/128
```

```text
[Release] AieXile/AnimeTrack v2.1.0
• 新增季度番剧自动追踪
• 修复 Android 14 通知权限问题
https://github.com/AieXile/AnimeTrack/releases/tag/v2.1.0
```

随后会单独发送 Release 附件（如 `AnimeTrack-arm64-v8a.apk`）。

管理命令（仅 AstrBot 管理员）：

- `/github_notify`：查看仓库数、目标数、轮询间隔、上次轮询与最近错误
- `/github_notify_test`：向当前所有目标发送一条测试消息
- `/github_notify_replay`：重放最近一条 Push（不会改变去重状态）
- `/github_notify_replay AieXile/AnimeTrack push`：指定仓库和类型（`push` / `release` / `issue`）

### 为什么刚装好没有通知？

插件第一次轮询时会**建立基线**：把当时已经存在的历史事件标记为已处理，但不会推送，避免安装后一次性刷出几十条旧通知。

- 日志会出现 `GitHub 通知首次建立基线：...（跳过 N 条历史事件）`
- 新发生的 Push / Issue / Release 才会正常通知
- 想立刻验证已有事件：用 `/github_notify_replay`
- `notify_on_first_run` 只在“尚未建立基线”时生效；已经建立基线后，请用 replay 命令补发

## 附件选择规则

1. 先按 `asset_patterns` 过滤，规则越靠前优先级越高（默认 APK 优先于 EXE）；
2. 开启 `asset_prefer_64bit` 时，文件名包含 `arm64-v8a`、`arm64`、`x86_64`、`x64`、`win64`、`amd64` 等关键字加分；
3. 包含 `armeabi-v7a`、`armv7`、`x86`、`win32`、`i386` 等关键字降权；
4. 按 `asset_max_count` 截取；
5. 超过 `asset_max_mb` 或发送失败时，改为发送下载链接。

QQ 群文件上传能力取决于协议端和机器人权限；失败会自动回退为链接。

## 常见问题

| 现象 | 排查 |
|---|---|
| 一直没有推送 | 首次运行默认只建立基线；等新事件，或用 `/github_notify_replay` 重放最近一条 |
| 已经建立基线后才打开 `notify_on_first_run` 没效果 | 该开关只对首次基线生效；用 `/github_notify_replay` |
| 安装前的 Push 没通知 | 正常，首次基线会跳过历史事件；用 replay 补发，或等下一次 Push |
| 提示「未配置推送目标」 | 填群号/QQ 号；或填 `platform_id`；或使用 UMO 目标 |
| 提示 HTTP 401 | `github_token` 无效或过期 |
| 提示 HTTP 403 | 触发速率限制；填 token、加大 `poll_interval`，插件会按重置时间退避 |
| 提示 HTTP 404 | 仓库名写错，或私有仓库没填 token |
| Push 没有提交明细 | Events API 本身不带 commits，插件会用 compare API 补齐；compare 失败时只发链接 |
| Release 没有附件 | 附件名不匹配 `asset_patterns`、超过大小上限，或平台不支持文件 |
| 摘要没有走 AI | `summary_provider` 为空且 AstrBot 没有默认模型；会自动退化为发布说明截断 |
| 改了配置没生效 | 重载插件；轮询循环本身每轮会重新读取配置 |

## 安全与限制

- token 只保存在插件配置中；代码不打印 token、不打印请求头。
- 无公网时只能轮询，通知延迟约为一个 `poll_interval`。
- **Push 不使用 GitHub Events API**：插件每轮读取各分支最新 head，发现变化后用 compare API 补齐提交明细，避免 Events API 漏报 Push。
- 分支列表接口单页最多 100 个分支；`branches` 留空时监听前 100 个分支。
- Issue / Release 仍使用 Events API，单页最多 100 条；仓库事件特别密集时建议把间隔调到 120 秒。
- Release 附件会下载到 `data/plugin_data/astrbot_plugin_github_notify/downloads/`，发送后自动清理。
- 私有仓库与私有 Release 附件必须配置 token。
- 预发布 Release 可关闭；草稿 Release 不会通知。

## 自测

```bash
python tests/test_pure.py
python tests/test_github_client.py
python tests/test_service.py
```

覆盖：配置解析、分仓库目标、Issue/Release/Push 解析、分支过滤、预发布过滤、64 位附件优先、附件数量/大小上限、链接回退、ETag 304、403/429 退避、附件下载重定向、事件去重、首次基线、LLM 摘要与失败兜底、消息格式化。
