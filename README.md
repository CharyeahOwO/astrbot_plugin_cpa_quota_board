# CPA 额度看板 / CPA Quota Board

`astrbot_plugin_cpa_quota_board` 是一个专门用于 CLIProxyAPI / CPAMC 额度查询、额度卡片展示和额度变化主动通知的 AstrBot 插件。

插件重点不是复杂使用统计，而是：手动查询额度图片、后台巡检额度、低额度 / 危险 / 耗尽 / 恢复状态通知，以及重启后仍能去重避免刷屏。

## 功能

- 通过 CLIProxyAPI Management API 查询账号额度。
- 使用 Pillow 渲染深色仪表盘风格图片卡片。
- 支持 `/额度`、`/额度简洁`、`/额度刷新`、`/cpa额度`、`/quota`。
- 支持 `/额度通知开启`、`/额度通知关闭`、`/额度测试通知`、`/额度状态`。
- 后台巡检使用 `last_quota_state.json` 做状态去重。
- 通知目标保存到 `notify_targets.json`。
- 可选开启 CLIProxyAPI 用量统计发布，但不依赖已移除的 `/v0/management/usage`，也不会读取会 pop 记录的 `/v0/management/usage-queue` 做常规统计。

## 安装

1. 将本仓库放入 AstrBot 的插件目录。
2. 安装依赖：`pip install -r requirements.txt`。
3. 在 AstrBot WebUI 中启用插件并填写配置。

## 配置

`cpa_url` 必须填写 CLIProxyAPI 根地址，例如：

```text
https://api.nyaovo.com
```

不要填写管理面板页面地址，例如不要填：

```text
https://api.nyaovo.com/management.html#/
```

插件内部会自动拼接：

- `/v0/management/auth-files`
- `/v0/management/api-call`

主要配置项：

- `cpa_url`：CLIProxyAPI 根地址。
- `cpa_password`：CLIProxyAPI Management Key / 管理密钥。
- `verify_ssl`：是否校验 SSL，默认 `true`。
- `request_timeout`：请求超时时间，默认 `30` 秒。
- `poll_interval_seconds`：后台巡检间隔，默认 `300` 秒。
- `warning_percent`：低额度阈值，默认 `20`。
- `critical_percent`：危险额度阈值，默认 `5`。
- `enable_quota_notify`：是否启用额度通知，默认 `false`。
- `enable_usage_statistics`：是否开启 CLIProxyAPI 用量统计发布，默认 `false`。插件只调用 `/v0/management/usage-statistics-enabled`，不会读取 `/usage-queue`。
- `render_high_resolution`：是否高清渲染图片，默认 `true`。
- `max_accounts_per_provider`：每类 provider 最多渲染账号数，默认 `20`。
- `compact_mode_default`：默认是否使用简洁模式，默认 `false`。

## 命令

- `/额度`：查询所有支持账号额度，返回图片卡片。
- `/额度简洁`：只显示低额度、危险、耗尽、异常账号，返回图片卡片。
- `/额度刷新`：跳过 10 秒短缓存，强制重新查询。
- `/额度通知开启`：保存当前会话的 `event.unified_msg_origin`。
- `/额度通知关闭`：移除当前会话通知目标。
- `/额度测试通知`：向当前会话发送测试告警图片。
- `/额度状态`：显示通知状态、巡检间隔、目标数量、上次巡检时间。
- `/cpa额度`：兼容命令，等同 `/额度`。
- `/quota`：英文兼容命令，等同 `/额度`。

## 截图

截图或示意图位置预留。

## 安全说明

插件不会主动打印 `cpa_password`、`access_token`、`refresh_token`、OAuth token。错误日志会尽量脱敏敏感字段。图片中只显示账号名称、额度状态和错误摘要，不显示完整 token 或密钥。

## 用量统计

如果你需要历史用量、价格、趋势图表，建议单独部署 `cpa-usage-keeper`：

https://github.com/Willxup/cpa-usage-keeper

`cpa-usage-keeper` 会消费 CLIProxyAPI 的 `/usage-queue` 并写入 SQLite。该队列读取是 pop 行为，同一个 CLIProxyAPI 实例应只保留一个消费者。本插件不会读取 `/usage-queue`，可以和 `cpa-usage-keeper` 并行使用。

## 参考与许可

本项目部分实现思路和少量代码结构参考 / 改编自：

https://github.com/muyouzhi6/astrbot_plugin_cliproxy_stats

参考项目许可证为 MIT License。如果使用或改编了参考项目代码，本项目已在 `NOTICE` 中保留原作者版权和许可声明。

本项目使用 MIT License，见 `LICENSE`。
