# 球队资料快照

两份 JSON 从用户指定的公开仓库提取数据部分，不执行来源 JavaScript。仅用于页面显示；需要更新时重新审查差异、冲突与许可证。

- `football.json`：`majunwuxi/sportsmatch` 的 `teams-map.js`，Git blob `6fada8800630c0fd3dae24456f2dde4eaae9c712`。
- `basketball.json`：同仓库的 `basketball-teams.js`，Git blob `d2b9b229300d999f24ed08a9a6c7b488f71a846e`；本项目仅使用 `nba` 项。

中文名补充：用户提供的 `football_league_teams_中文名称补充版.xlsx`（API-Football 2024 赛季 12 个联赛共 236 支球队），按 `team_id` 与 `football.json` 的键对照。其中 28 支阿根廷甲级联赛球队此前不在目录中，已按该文件补入英文标准名、中文名、国家与队徽（`leagues` 记为 `ARG_A`）；已有球队的中文名保持原样，不做覆盖。
