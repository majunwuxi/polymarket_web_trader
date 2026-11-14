# PolySports 最终验收记录

验收日期：2026-11-14。范围为仓库中的独立 PolySports 和原 Dashboard 回归；以下交易及赛事上游均使用明确标识的模拟数据，未发送真实资金订单。

## 已执行

- PolySports 后端与临时 PostgreSQL：`uv run --locked pytest -q`，110 项通过。测试数据库仅绑定本机，包含迁移、任务、交易意图、备份与恢复等集成检查。
- 原 Dashboard：独立临时依赖环境中运行 `python -m pytest tests -q`，25 项及 13 个子测试通过。
- 前端：`npm test` 3 项通过，`npm run build` 成功；`npm run test:browser` 使用本机 Edge，模拟 API，在 1440×900 和 390×844 视口通过。
- [Ubuntu 24.04 隔离部署验收](https://github.com/majunwuxi/polymarket_web_trader/actions/runs/43538150858)：数据库迁移、systemd API/worker 启动与任务重启、备份及空库恢复、Nginx 和自签名 HTTPS 检查通过。该记录对应应用代码提交 `446236d`；后续提交仅更新验收文档和任务状态。
- `openspec validate build-polysports --strict --no-interactive` 通过；该校验不替代应用测试。

## 仍需在目标环境核验

- API-Football 和 BallDontLie 的真实密钥、套餐、覆盖范围及只读同步；未提供凭据，未在本次验收中验证。
- 实际 Polymarket 钱包、地区、余额、授权、Builder/Combo 权限、报价及真实资金交易；自动测试的模拟结果不证明上述能力在真实账户可用。
- 目标 VPS 的安装、域名、正式 TLS 证书、密钥配置、运维和恢复。操作步骤见 [DEPLOY.md](DEPLOY.md)，隔离 CI 不代表目标服务器已部署。

Python 测试存在上游依赖弃用提示。前端生产依赖审计未报告漏洞；此前完整开发依赖审计报告 4 项中等风险，部署前应重新检查。
