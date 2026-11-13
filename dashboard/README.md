# Dashboard 使用说明

## 页面功能

- 登录页：密码和 2FA 验证。
- 钱包选择器：在已配置的钱包之间切换。
- 快速交易：通过 SPX 日期或 Polymarket 链接获取行情，并按输入金额下单。
- 持仓列表：展示市场、outcome、均价、建仓成本、当前价、盈亏。
- 平仓按钮：对单个持仓执行 25%、50%、75%、全仓卖出。

## 后端接口

- `GET /api/auth/check`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/status`
- `GET /api/wallets`
- `POST /api/wallets/switch`
- `GET /api/positions`
- `POST /api/market/fetch`
- `POST /api/market/spx`
- `POST /api/trade/place`
- `POST /api/trade/sell`

## 刷新逻辑

前端每 5 秒刷新一次钱包和持仓数据。后端对持仓接口做短缓存，缓存过期时会先返回旧数据并后台刷新，避免页面长时间阻塞。
