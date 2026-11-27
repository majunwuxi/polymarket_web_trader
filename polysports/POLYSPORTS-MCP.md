# PolySports MCP 安装与使用手册

在 Codex 桌面聊天里查询比赛、盘口和钱包额度，计算投注额并生成预填的 PolySports 组合草稿。最终由用户在 PolySports 网页核对并提交。

## 在新 Windows 电脑安装

已有 VPS 正常运行时，新电脑只需配置本地 MCP 和同一条只读令牌。

### 1. 安装依赖并下载项目

安装 Codex 桌面端，并确认 PowerShell 可以运行 `codex --version`。Git 或 uv 已安装时，跳过对应命令。

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
```

重新打开 PowerShell，执行：

```powershell
$repo = Join-Path $env:USERPROFILE 'polymarket_web_trader'
git clone https://github.com/majunwuxi/polymarket_web_trader.git "$repo"
$project = Join-Path $repo 'polysports'

uv python install 3.12
uv --directory "$project" sync --locked --extra agent --python 3.12
```

已有仓库时，跳过克隆，将 `$repo` 改为实际目录。若 GitHub 要求登录，请使用有仓库访问权限的账号。

### 2. 注册 MCP

在同一个 PowerShell 窗口执行：

```powershell
$uvExe = (Get-Command uv -CommandType Application).Source
codex mcp add polysports -- "$uvExe" --directory "$project" run --locked --extra agent --python 3.12 python -m backend.agent_tools
codex mcp get polysports
```

看到 `enabled: true` 和 `transport: stdio` 表示注册成功。

### 3. 保存只读令牌

使用 VPS 上配置的同一条 `POLYSPORTS_AGENT_READ_TOKEN`。等提示出现后粘贴完整令牌并回车，输入会隐藏。

```powershell
$agentToken = Read-Host '输入与 VPS 相同的 PolySports 只读令牌' -AsSecureString
if ($agentToken.Length -ge 32) {
    [Environment]::SetEnvironmentVariable(
        'POLYSPORTS_AGENT_READ_TOKEN',
        [System.Net.NetworkCredential]::new('', $agentToken).Password,
        'User'
    )
    Write-Output '令牌已保存'
} else {
    Write-Output '未保存，请输入完整令牌'
}
Remove-Variable agentToken
```

令牌无需发到聊天里。Windows MCP 可以直接读取用户级环境变量。

### 4. 验证

完全退出并重启 Codex，开新对话输入：

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
