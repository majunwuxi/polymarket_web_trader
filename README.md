# Polymarket Dashboard

一个用于 Polymarket 钱包持仓监控和手动交易的本地 Dashboard。

## 功能

- 多钱包配置和切换
- 密码和 TOTP 2FA 登录保护
- 通过 Data API v2 分页查询当前钱包持仓、均价、当前价、盈亏、现金余额和持仓市值
- 按 25%、50%、75%、全仓比例卖出已有持仓
- 通过 Polymarket URL 拉取市场 outcome 和盘口价格后下单
- 按日期查找 SPX daily close up/down 市场并快速下单
- 从 Polygon RPC 查询 pUSD 余额；无法读取时显示未知

## 快速开始

```bash
pip install -r requirements.txt
copy config.sample.yaml config.yaml
copy .env.sample .env
python main.py
```

默认监听 `http://localhost:5000`。可以通过 `.env` 中的 `DASHBOARD_PORT` 修改端口。

## 主要文件

- `main.py`：服务入口
- `dashboard_api.py`：Flask API、认证、钱包切换、持仓和交易接口
- `dashboard/index.html`：Dashboard 主页面
- `dashboard/login.html`：登录页面
- `polymarket_autotrader/config.py`：配置加载
- `polymarket_autotrader/clients.py`：Polymarket 和 Polygon 数据访问
- `polymarket_autotrader/execution.py`：CLOB 下单执行
- `generate_2fa_secret.py`：生成 TOTP 2FA 密钥
- `verify_wallet.py`：验证私钥并显示对应 EOA 地址

## 配置

`config.yaml` 只保留 Dashboard 当前使用的服务端点和下单精度：

```yaml
gamma_base_url: "https://gamma-api.polymarket.com"
data_api_base_url: "https://data-api.polymarket.com"
clob_host: "https://clob.polymarket.com"
server_name: "polymarket-dashboard"
enable_websocket_price_service: true
min_price: 0.01
price_precision: 4
size_precision: 4
```

敏感配置放在 `.env`。签名类型必须与账户钱包一致（0 EOA、1 Proxy、2 Safe、3 Deposit Wallet）；详见 [配置指南](CONFIG_GUIDE.md)。例如：

```bash
POLY_PRIVATE_KEY=0x...
POLY_FUNDER_ADDRESS=0x...
POLY_SIGNATURE_TYPE=1
POLYGON_RPC_URL=https://polygon-bor-rpc.publicnode.com
DASHBOARD_PASSWORD=your_secure_password
DASHBOARD_2FA_SECRET=YOUR_BASE32_SECRET
FLASK_SECRET_KEY=your_random_session_secret
```

多钱包可以使用 `WALLETS_JSON` 或 `WALLET_1_*`、`WALLET_2_*` 形式配置，详见 `CONFIG_GUIDE.md`。

## 安全提示

- 不要提交 `.env`、私钥、API key 或真实钱包地址。
- 生产环境建议固定设置 `FLASK_SECRET_KEY`，否则服务重启会使 session 失效。
- 建议始终启用 `DASHBOARD_2FA_SECRET`。
- 公开访问时请放在 HTTPS 反向代理后面。
