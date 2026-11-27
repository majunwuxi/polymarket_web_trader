# PolySports MCP 安装与使用手册

在 Codex 桌面聊天里查询比赛、盘口和钱包额度，计算投注额并生成预填的 PolySports 组合草稿。最终由用户在 PolySports 网页核对并提交。

## 在新 Windows 电脑安装

从本仓库下载 [PolySports MCP Windows 独立安装包](./PolySports-MCP-Windows.zip)。新电脑无需克隆仓库，现有 VPS 无需重新部署。首次安装需要联网下载 Python 和锁定的依赖。

### 1. 检查 Codex 和 uv

如果电脑已经安装 Codex 桌面版，继续使用即可。打开 PowerShell 检查：

```powershell
codex --version
uv --version
```

- 能运行 `codex --version`，就可以用安装脚本注册 MCP。
- 能打开桌面版但 PowerShell 找不到 `codex` 时，可以按文末官方文档安装 Codex CLI，再运行脚本。日常使用仍在桌面聊天里。
- 找不到 `uv` 时，执行 `winget install --id astral-sh.uv -e`，然后重新打开 PowerShell。

### 2. 解压并安装

将 ZIP 解压到计划长期保留的目录，例如 `C:\Tools\PolySports-MCP`。Codex 每次启动 MCP 时都会使用该目录。

在解压目录打开 PowerShell，执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1 -SetToken
```

等隐藏输入提示出现后，粘贴与 VPS 相同的完整 `POLYSPORTS_AGENT_READ_TOKEN` 并回车。Windows MCP 可以读取用户级环境变量。已保存有效令牌时可省略 `-SetToken`。如需用独立安装包替换已有 `polysports` 注册，再加上 `-Replace`。

安装成功后，脚本会显示 `enabled: true` 和 `transport: stdio`。

### 3. 验证

完全退出并重启 Codex 桌面版，开新对话输入：

> 调用 `polysports.project_wallets`，然后用 `polysports.project_wallet_budget` 读取「我的钱包」的可用余额和今日买入剩额。

看到余额和剩额即验证通过。打开草稿前先在同一浏览器登录 [PolySports](https://example.invalid/)。

## 日常使用

按比赛分析并生成方案：

> 用 PolySports MCP 分析北京时间今晚的布雷斯特 vs 巴黎 FC。核实赛程、近期表现和实时盘口，从同一场比赛的不同全场盘口各选一项。使用「我的钱包」，按我的投注规则算金额。核验成功后生成并打开组合草稿，由我在网页核对后提交。

已确定选项时：

> 用「我的钱包」为布雷斯特 vs 巴黎 FC 生成组合草稿。选择「布雷斯特胜 · No」和「全场 O/U 4.5 · Under」，按最新余额和今日剩额填入金额，然后打开草稿。

同一场比赛可以选不同盘口各一项。示例中「布雷斯特胜 · No」要求巴黎 FC 胜或打平，「全场 O/U 4.5 · Under」要求全场最多 4 球。两项都命中才算组合命中。组合价格以正式报价为准。

### 投注额规则

取实际可用余额的 **5%**，最低 **1 pUSD**。用钱包可用余额和今日剩额封顶，任一可用额度低于 1 pUSD 就跳过。未设置每日上限时，以钱包余额封顶。金额向下保留 6 位小数。

| 钱包可用余额 | 今日买入剩额 | 草稿金额 |
|---:|---:|---:|
| 24.848926 pUSD | 8.68888 pUSD | 1.242446 pUSD |
| 100 pUSD | 3.5 pUSD | 3.5 pUSD |
| 10 pUSD | 3.5 pUSD | 1 pUSD |
| 100 pUSD | 0.8 pUSD | 跳过 |

单场买入和组合买入共用同一钱包的今日额度。网页把计算结果填入「最高预算（含费用）」。提交前由你核对钱包、选项和金额。

### 工具速查

| 工具 | 用途 |
|---|---|
| `polysports.project_games` | 按运动和带时区的时间范围查赛事及项目 ID |
| `polysports.project_combo_options` | 查已确认、仍接单的组合盘口和选项 ID |
| `polysports.project_wallets` | 查项目钱包 |
| `polysports.project_wallet_budget` | 查实际可用余额、今日剩额和重置时间 |
| `polysports.combo_stake` | 用给定余额和剩额试算金额 |
| `polysports.combo_draft` | 重读额度、核验选项并生成预填草稿链接 |

## 常见问题

- **网页显示盘口，MCP 找不到选项。** 网页可直接展示实时盘口。草稿链接需要项目内部选项 ID。先在赛事详情用「+组合」核验并加入对应选项，再让 Codex 查询并生成带金额的草稿。网页「+组合」本身只添加选项。
- **市场查询或市场核验失败。** 盘口展示和「+组合」核验是两次请求。记录出错时间和比赛名，便于在 VPS 日志中查对应错误。两项都出现在草稿后才算准备成功。
- **提示尚未配置只读令牌。** 检查令牌是否保存到当前 Windows 用户，以及是否与 VPS 一致。完全退出并重开 Codex，再试 `polysports.project_wallets`。
- **草稿链接跳到登录页。** 先登录，再重新打开原链接。看到「已载入组合草稿，请核对钱包、选项和金额」并核对预填内容。

[Codex MCP 文档](https://developers.openai.com/codex/mcp) · [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)
