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
