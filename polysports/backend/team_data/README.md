# 球队资料快照

两份 JSON 从用户指定的公开仓库提取数据部分，不执行来源 JavaScript。仅用于页面显示；需要更新时重新审查差异、冲突与许可证。

- `football.json`：`majunwuxi/sportsmatch` 的 `teams-map.js`，Git blob `6fada8800630c0fd3dae24456f2dde4eaae9c712`。
- `basketball.json`：同仓库的 `basketball-teams.js`，Git blob `d2b9b229300d999f24ed08a9a6c7b488f71a846e`；本项目仅使用 `nba` 项。

中文名补充：用户提供的 `football_league_teams_中文名称补充版.xlsx`（API-Football 2024 赛季 12 个联赛共 236 支球队），按 `team_id` 与 `football.json` 的键对照。28 支阿根廷甲级联赛球队此前不在目录中，已按该文件补入英文标准名、中文名、国家与队徽（`leagues` 记为 `ARG_A`）；其余已匹配球队的中文名同样以该文件为准（2026-11 按 Excel 修正 23 处名称差异）。

Polymarket 别名：赛事中心展示的是 Polymarket 的球队名（如 `FC Bayern München`、`Manchester United FC`、`CA River Plate`），与 API-Football 简称不同；已按真实 Gamma 数据把 12 个联赛的 Polymarket 队名写入对应条目的 `aliases`（Football 名称精确匹配与赛果绑定的别名扩展都会使用），并补入两支此前缺失的挪超球队（326 Vålerenga、334 IK Start，ID 经官方队徽核对）。尚无 API-Football ID、因而仍缺条目的球队：Le Mans FC、Aalesunds FK、Elche、Málaga、Deportivo A Coruña、Racing Santander、Marítimo、Académico de Viseu、Degerfors IF、Örgryte IS、PK-35、TPS、Aldosivi、Gimnasia Mendoza。
