# 配置指南

## `config.yaml`

`config.yaml` 只保存非敏感运行参数：

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

## `.env`

单钱包配置：

```bash
POLY_PRIVATE_KEY=0xYourPrivateKeyHere
POLY_FUNDER_ADDRESS=0xYourWalletAddressHere
POLY_SIGNATURE_TYPE=1
POLYGON_RPC_URL=https://polygon-bor-rpc.publicnode.com
DASHBOARD_PASSWORD=your_secure_password_here
DASHBOARD_2FA_SECRET=YOUR_BASE32_SECRET_HERE
FLASK_SECRET_KEY=your_random_secret_key_here
DASHBOARD_PORT=5000
DRY_RUN=false
```

多钱包推荐使用 JSON：

```bash
WALLETS_JSON=[{"id":"wallet1","name":"主钱包","private_key":"0x...","address":"0x...","signature_type":1},{"id":"wallet2","name":"备用钱包","private_key":"0x...","address":"0x...","signature_type":1}]
```

也可以使用编号格式：

```bash
WALLET_1_PRIVATE_KEY=0x...
WALLET_1_ADDRESS=0x...
WALLET_1_NAME=主钱包
WALLET_1_SIGNATURE_TYPE=1

WALLET_2_PRIVATE_KEY=0x...
WALLET_2_ADDRESS=0x...
WALLET_2_NAME=备用钱包
WALLET_2_SIGNATURE_TYPE=1
```

如果同时配置多钱包和旧版 `POLY_*` 单钱包变量，系统优先使用多钱包配置。

### 钱包与交易凭证

`POLY_FUNDER_ADDRESS`（多钱包中为 `address`）是 Polymarket 账户钱包地址，`POLY_PRIVATE_KEY` 是其签名者私钥。`signature_type` 必须与账户类型一致：`0` 为 EOA，`1` 为 Proxy，`2` 为 Safe，`3` 为 Deposit Wallet。EOA 地址必须与签名私钥对应；智能钱包应填写账户钱包地址，而不是签名者地址。可在 Polymarket 账户菜单查看钱包地址。

交易会话在首次下单或查询订单时建立，官方 SDK 会识别钱包类型并与配置核对。智能钱包必须已部署，且 `POLYGON_RPC_URL` 必须能访问 Polygon 主网；系统不会自动部署钱包或提交授权交易。首次交易前需通过 Polymarket 或官方流程完成资金、合约授权及相应 CLOB allowance 同步。

可选地设置 `POLY_CLOB_API_KEY`、`POLY_CLOB_API_SECRET`、`POLY_CLOB_API_PASSPHRASE`；要么三项全部填写，要么全部留空由 SDK 获取。多钱包可分别使用 JSON 中的 `clob_api_key`、`clob_api_secret`、`clob_api_passphrase`，或编号变量 `WALLET_1_CLOB_API_KEY` 等。凭证属于对应签名者，请勿混用。钱包状态中的凭证标记表示会话已初始化；惰性初始化之前显示未就绪不代表配置有误。

目前交易 SDK 仅支持生产 CLOB 地址。`DRY_RUN=true` 会验证实时订单簿，但不会初始化交易会话或发送订单，因此不验证钱包凭证和链上授权。


## 2FA

运行以下命令生成 `DASHBOARD_2FA_SECRET`：

```bash
python generate_2fa_secret.py
```

把输出的 Base32 secret 写入 `.env`，并用 Google Authenticator、Authy 或兼容 TOTP 的应用扫码绑定。

## 安全建议

- `.env` 不应提交到版本库。
- 生产环境必须使用强密码和 2FA。
- `FLASK_SECRET_KEY` 建议固定配置，避免每次重启后 session 全部失效。
- 私钥只用于本地签名下单，任何泄露都会导致钱包资产风险。
