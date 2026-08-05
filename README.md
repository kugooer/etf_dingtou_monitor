# ETF 定投监测脚本

多 ETF 定投监测工具，支持定时执行、价格监控、MA250 偏离度计算，以及 Bark / Telegram 推送。

## 功能特性

- 多 ETF 监控：通过环境变量配置多个 ETF 代码
- 四层数据源：腾讯 → 新浪 → 东方财富 → baostock（自动降级，不依赖 akshare）
- MA250 均线：计算 250 日移动平均线
- 自动取名：优先读取环境变量，缺失时自动从腾讯接口获取 ETF 名称
- 偏离度通知：无论高于还是低于均线都发送通知
- 双通道推送：支持 Bark 与 Telegram，可逐条或汇总推送
- GitHub Actions：外置 Cloudflare 定时 + 手动触发

## 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|-----|------|--------|------|
| `ETF_CODES` | 否 | `512890` | ETF 代码，多个用逗号分隔 |
| `ETF_NAMES` | 否 | - | ETF名称映射（可选），格式：`512890:红利低波ETF,159919:创业板ETF`；缺失的名称自动从腾讯补名 |
| `PROXY_URL` | 否 | - | 东方财富 API 中转地址（Cloudflare Worker） |
| `PUSH_MODE` | 否 | `digest` | 推送模式：`per_item`(逐条) 或 `digest`(汇总)，空值按 digest 处理 |
| `BARK_URL` | 否 | - | Bark 推送 URL（配置后启用） |
| `BARK_GROUP` | 否 | - | Bark 分组名称 |
| `TELEGRAM_BOT_TOKEN` | 否 | - | Telegram 机器人 Token（与 CHAT_ID 同时配置后启用） |
| `TELEGRAM_CHAT_ID` | 否 | - | Telegram 接收者 ID |
| `TELEGRAM_GROUP` | 否 | - | Telegram 分组名称（可选） |

### 推送自动检测

- 有 `BARK_URL` → 自动启用 Bark 推送
- 有 `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` → 自动启用 Telegram 推送
- `PUSH_MODE=digest`：执行结束后汇总发送所有通知，每个 ETF 只出现一次
- `PUSH_MODE=per_item`：每条通知立即发送

### 汇总格式（digest 模式)

汇总通知以 `------------------------` 分隔线包裹：首块之前、块之间、末块之后各一条分隔线，块首带 ETF 标题（含代码）与定投提醒。

## 市场代码规则

| ETF 代码开头 | 市场 | secid |
|--------------|------|-------|
| 5 开头 | 上海 | `1.{code}` |
| 其他 | 深圳 | `0.{code}` |

## 使用方式

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export ETF_CODES="512890,159919"
export ETF_NAMES="512890:红利低波ETF,159919:创业板ETF"
export BARK_URL="https://api.day.app/xxx"
export BARK_GROUP="ETF监控"

# 运行
python etf_monitor.py
```

### GitHub Actions

在仓库 Settings → Variables / Secrets 中配置。变量读取优先级：**Variables 优先，Secrets 兜底**。

| 名称 | 示例 |
|------|------|
| `ETF_CODES` | `512890,159919` |
| `ETF_NAMES` | `512890:红利低波ETF,159919:创业板ETF` |
| `PROXY_URL` | 东方财富中转地址（可选） |
| `PUSH_MODE` | `digest` |
| `BARK_URL` | `https://api.day.app/xxx` |
| `BARK_GROUP` | `ETF监控` |
| `TELEGRAM_BOT_TOKEN` | 机器人 Token |
| `TELEGRAM_CHAT_ID` | 接收者 ID |
| `TELEGRAM_GROUP` | 分组名称（可选） |

> ⚠️ 安全提醒：若仓库为 public，`TELEGRAM_BOT_TOKEN`、`BARK_URL` 等敏感信息务必放在 **Secrets**，不要放进 Variables（Variables 对仓库可见）。

## 通知内容

每次执行会发送通知，包含：
- 当前价格
- MA250 均线值
- 偏离度（百分比）
- 建议（买入/观望）

## 定时任务

- 由外置 Cloudflare 定时触发（也可在 GitHub Actions 页面手动触发）
- workflow 当前仅 `workflow_dispatch` 手动触发；如需内置定时，可在 `.github/workflows/etf-monitor.yml` 中启用注释掉的 `schedule` 配置

## 文件结构

```
etf_monitor.py          # 主脚本
requirements.txt        # Python 依赖
.github/workflows/    # GitHub Actions 配置
cloudflare-worker.js  # Cloudflare Worker 中转/定时脚本（可选）
```

## 依赖

- baostock >= 0.8.8

## 许可证

MIT