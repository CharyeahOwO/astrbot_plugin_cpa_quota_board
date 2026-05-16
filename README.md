# CPA 额度看板

用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 CLIProxyAPI / CPAMC 额度看板插件。

## ✨ 功能特性

- 查询 CLIProxyAPI / CPAMC OAuth 账号额度。
- 以图片卡片展示每个账号的额度窗口。
- 自动合并 Gemini Flash / Pro / Flash Lite 系列额度。
- 支持低额度、危险、异常、恢复状态预警。
- 支持管理员在会话中开启或关闭 CPA 额度预警白名单。
- 默认使用插件依赖安装的 Noto Sans SC 字体，适配 Docker / Linux 环境。

## 🖼️ 截图

### 效果演示

| 聊天效果 | 面板效果 |
| :---: | :---: |
| ![1](assets\image.png) | ![2](assets\image2.png) |


## 安装

在 AstrBot 插件市场搜索 `CPA 额度看板` 或 `astrbot_plugin_cpa_quota_board` 安装。

## ⚙️ 配置

必要配置：

- `cpa_url`：CLIProxyAPI / CPAMC 根地址，例如 `https://api.example.com`。
- `cpa_password`：CLIProxyAPI Management Key。

常用配置：

- `response_format`：返回格式，默认 `image`。
- `enable_quota_notify`：是否启用后台额度预警。
- `notify_whitelist`：预警白名单，会被 `/开启cpa预警` 和 `/关闭cpa预警` 同步更新。
- `warning_percent`：低额度阈值，默认 `20`。
- `critical_percent`：危险阈值，默认 `5`。
- `poll_interval_seconds`：后台巡检间隔，默认 `300` 秒。

## 使用说明

| 指令 | 说明 |
| :--- | :--- |
| `/额度` | 查询额度看板 |
| `/cpa` | 查询额度看板 |
| `/cpa额度` | 查询额度看板 |
| `/quota` | 查询额度看板 |
| `/额度刷新` | 跳过短缓存并重新查询 |
| `/额度详情分页` | 分页查看完整额度明细 |
| `/额度状态` | 查看预警状态和白名单数量 |
| `/额度测试通知` | 发送一条测试预警 |
| `/开启cpa预警` | **[管理员]** 将当前会话加入预警白名单 |
| `/关闭cpa预警` | **[管理员]** 将当前会话移出预警白名单 |

## 适用平台

- aiocqhttp / OneBot v11
- 其他支持 AstrBot 图片消息的平台

## 说明

`cpa_url` 请填写根地址，不要填写管理面板页面地址，例如不要填写：

```text
https://api.example.com/management.html#/
```

本插件不会主动打印 Management Key、OAuth token、access token 或 refresh token。

## 许可证

MIT License
