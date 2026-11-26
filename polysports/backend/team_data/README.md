# 球队资料快照

两份 JSON 从用户指定的公开仓库提取数据部分，不执行来源 JavaScript。仅用于页面显示；需要更新时重新审查差异、冲突与许可证。

- `football.json`：`majunwuxi/sportsmatch` 的 `teams-map.js`，Git blob `6fada8800630c0fd3dae24456f2dde4eaae9c712`。
- `basketball.json`：同仓库的 `basketball-teams.js`，Git blob `d2b9b229300d999f24ed08a9a6c7b488f71a846e`；本项目仅使用 `nba` 项。

中文名补充：用户提供的 `football_league_teams_中文名称补充版.xlsx`（API-Football 2024 赛季 12 个联赛共 236 支球队），按 `team_id` 与 `football.json` 的键对照。28 支阿根廷甲级联赛球队此前不在目录中，已按该文件补入英文标准名、中文名、国家与队徽（`leagues` 记为 `ARG_A`）；其余已匹配球队的中文名同样以该文件为准（2026-11 按 Excel 修正 23 处名称差异）。

亚冠精英联赛：`acle_teams.md` 是 API-Football（`league=17`）、Polymarket（Gamma `league=acle`）与中文名的三方对照表，共 35 支现役球队，`team_id` 均经 `GET /teams?id=` 逐条核实。注意 Polymarket 的 `providerId` 不是 API-Football `team_id`，不能互相替代。35 支已全部补录进 `football.json`：`name_en`/`name_zh` 用 API-Football 名与国内媒体通行译名，`aliases` 写入 Polymarket 队名，`leagues` 标注 `ACLE`。

欧协联与欧罗巴：`uecl_uel_teams.md` 是 API-Football（欧协联 `league=848`、欧罗巴 `league=3`，2023/2024 赛季）、Polymarket 与中文名的三方对照表，共 28 支球队（欧协联 18、欧罗巴 10），`team_id` 均经 `GET /teams?id=` 逐条核实。28 支已全部补录进 `football.json`：`name_en` 用 API-Football 原名，`name_zh` 用国内媒体通行译名，`aliases` 写入 Polymarket 队名，`leagues` 标注 `UECL`/`UEL`。

Polymarket 别名：赛事中心展示的是 Polymarket 的球队名（如 `FC Bayern München`、`Manchester United FC`、`CA River Plate`），与 API-Football 简称不同；已按真实 Gamma 数据把 12 个联赛的 Polymarket 队名写入对应条目的 `aliases`（Football 名称精确匹配与赛果绑定的别名扩展都会使用），并补入两支此前缺失的挪超球队（326 Vålerenga、334 IK Start，ID 经官方队徽核对）。2026-11 补齐意甲 20 支球队的 Polymarket 别名，例如 `FC Internazionale Milano`→国际米兰、`SSC Napoli`→那不勒斯、`US Sassuolo Calcio`→萨索洛。原缺条目的 Aalesunds FK、Málaga、Deportivo A Coruña、Marítimo、Académico de Viseu、Degerfors IF、Örgryte IS、PK-35、TPS、Aldosivi 已按 API-Football `team_id` 与官方队徽核实补录，并写入 Polymarket 队名别名。

美职联（MLS）：补录 API-Football 联赛 253 在 2022–2024 赛季出现的 29 支球队，使用 API-Football 球队 ID、英文原名与队徽。不同拼写的 Polymarket MLS 球队目录名称写入别名，中文采用球天下体育 2024 美职联赛程用名。
