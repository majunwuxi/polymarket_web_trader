# 亚冠精英联赛（ACLE）球队三方对照资料

本文件把 **API-Football 英文名**、**Polymarket 英文名** 与 **中文名** 整合在同一张表，供 `football.json` 球队目录核对与补录使用。只用于页面显示与赛果绑定的精确别名扩展，不作为交易身份自动确认依据。

## 数据来源与核验口径

- API-Football：联赛 `league=17`（AFC Champions League Elite Cup）。免费套餐仅开放 2022–2024 赛季，下表 35 支球队的 `team_id` 均已通过 `GET /teams?id=` 逐条核实，队徽取自 `media.api-sports.io/football/teams/{id}.png`。
- Polymarket：Gamma `GET /teams?league=acle` 返回的 35 支现役球队英文名（2026-11 查询）。
- 中文名：按中文媒体通用译名整理，需管理员最终确认。

**重要：Polymarket 球队对象的 `providerId` 不是 API-Football 的 `team_id`**（例如英超 Crystal Palace 的 `providerId` 为 512，而 API-Football `team_id` 为 52），两者不能互相替代，必须按本表映射。

## 对照表（共 35 支）

| API-Football team_id | API-Football 英文名 | Polymarket 英文名 | 中文名 | 国家/地区 | 已在 football.json |
| ---: | --- | --- | --- | --- | :---: |
| 2929 | Al-Ahli Jeddah | Al Ahli Saudi FC | 吉达阿赫利 | Saudi-Arabia | — |
| 2932 | Al-Hilal Saudi FC | Al Hilal SFC | 利雅得新月 | Saudi-Arabia | — |
| 2933 | Al-Qadisiyah FC | Al Qadsiah | 卡迪西亚 | Saudi-Arabia | — |
| 2938 | Al-Ittihad FC | Al-Ittihad Club | 吉达联合 | Saudi-Arabia | — |
| 2939 | Al-Nassr | Al Nassr Club | 利雅得胜利 | Saudi-Arabia | — |
| 2865 | Al Ain | Al Ain FC | 艾因 | United-Arab-Emirates | — |
| 2870 | Shabab Al Ahli Dubai | Shabab Al Ahli Dubai | 迪拜国民 | United-Arab-Emirates | — |
| 2871 | Al-Jazira | Al Jazira (UAE) | 半岛 | United-Arab-Emirates | — |
| 2872 | Al-Wasl FC | Al Wasl FC | 瓦斯尔 | United-Arab-Emirates | — |
| 2895 | Al Sadd | Al Sadd SC | 萨德 | Qatar | — |
| 2903 | Al-Gharafa | Al Gharafa SC | 加拉法 | Qatar | — |
| 2916 | Al Shamal | Al-Shamal | 沙马尔 | Qatar | — |
| 2733 | Esteghlal FC | Esteghlal FC Tehran | 德黑兰独立 | Iran | — |
| 2737 | Tractor Sazi | Tractor Tabriz | 大不里士拖拉机 | Iran | — |
| 4217 | Neftchi | FK Neftchi Fargona | 费尔干纳纳夫奇 | Uzbekistan | — |
| 4220 | Pakhtakor | Pakhtakor | 塔什干棉农 | Uzbekistan | — |
| 4532 | Al Hussein | Al Hussein Irbid | 侯赛因伊尔比德 | Jordan | — |
| 8009 | Al Quwa Al Jawiya | Al Quwa Al Jawiya | 空军 | Iraq | — |
| 830 | Beijing Guoan | Beijing Guoan | 北京国安 | China | 是 |
| 836 | SHANGHAI SIPG | Shanghai Port FC | 上海海港 | China | — |
| 281 | Kashiwa Reysol | Kashiwa Reysol | 柏太阳神 | Japan | 是 |
| 289 | Vissel Kobe | Vissel Kobe | 神户胜利船 | Japan | 是 |
| 290 | Kashima | Kashima Antlers | 鹿岛鹿角 | Japan | 是 |
| 293 | Gamba Osaka | Gamba Osaka | 大阪钢巴 | Japan | 是 |
| 302 | Kyoto Sanga | Kyoto Sanga FC | 京都不死鸟 | Japan | 是 |
| 2746 | Gangwon FC | Gangwon FC | 江原FC | South-Korea | 是 |
| 2750 | Daejeon Citizen | Daejeon Citizen FC | 大田市民 | South-Korea | 是 |
| 2762 | Jeonbuk Motors | Jeonbuk FC | 全北现代 | South-Korea | 是 |
| 2764 | Pohang Steelers | FC Pohang Steelers | 浦项制铁 | South-Korea | 是 |
| 2776 | Ratchaburi | Ratchaburi FC | 叻丕 | Thailand | — |
| 2780 | Buriram United | Buriram United | 武里南联 | Thailand | 是 |
| 2789 | Port FC | Port FC | 泰港 | Thailand | — |
| 946 | Newcastle Jets | Newcastle United Jets | 纽卡斯尔喷气机 | Australia | — |
| 2523 | Johor Darul Takzim FC | Johor Darul Ta'zim | 柔佛达鲁塔克辛 | Malaysia | — |
| 16400 | Công An Nhân Dân | Cong An Ha Noi FC | 河内公安 | Vietnam | — |

## 覆盖情况

- 已在 `football.json` 的 **11 支**：Beijing Guoan（830）、Kashiwa Reysol（281）、Vissel Kobe（289）、Kashima（290）、Gamba Osaka（293）、Kyoto Sanga（302）、Gangwon FC（2746）、Daejeon Citizen（2750）、Jeonbuk Motors（2762）、Pohang Steelers（2764）、Buriram United（2780）
- 尚缺条目的 **24 支**：Al-Ahli Jeddah（2929）、Al-Hilal Saudi FC（2932）、Al-Qadisiyah FC（2933）、Al-Ittihad FC（2938）、Al-Nassr（2939）、Al Ain（2865）、Shabab Al Ahli Dubai（2870）、Al-Jazira（2871）、Al-Wasl FC（2872）、Al Sadd（2895）、Al-Gharafa（2903）、Al Shamal（2916）、Esteghlal FC（2733）、Tractor Sazi（2737）、Neftchi（4217）、Pakhtakor（4220）、Al Hussein（4532）、Al Quwa Al Jawiya（8009）、SHANGHAI SIPG（836）、Ratchaburi（2776）、Port FC（2789）、Newcastle Jets（946）、Johor Darul Takzim FC（2523）、Công An Nhân Dân（16400）

补充说明：

- `football.json` 中另有 `833 Shanghai Shenhua`（上海申花，`leagues: ACLE,CSL`），但不在本次 Polymarket 的 35 支现役名单内，属历史条目，保留不动。
- 上海海港在 API-Football 的标准名是 `SHANGHAI SIPG`（`team_id` 836），Polymarket 使用 `Shanghai Port FC`；两者是同一支球队，补录时须写入 `aliases`。
- 下列球队 API-Football 名与 Polymarket 名差异较大，补录时须写入 `aliases`：
  - `836` SHANGHAI SIPG → Polymarket `Shanghai Port FC`
  - `2733` Esteghlal FC → Polymarket `Esteghlal FC Tehran`
  - `2737` Tractor Sazi → Polymarket `Tractor Tabriz`
  - `290` Kashima → Polymarket `Kashima Antlers`
  - `302` Kyoto Sanga → Polymarket `Kyoto Sanga FC`
  - `946` Newcastle Jets → Polymarket `Newcastle United Jets`
  - `2523` Johor Darul Takzim FC → Polymarket `Johor Darul Ta'zim`
  - `2750` Daejeon Citizen → Polymarket `Daejeon Citizen FC`
  - `2762` Jeonbuk Motors → Polymarket `Jeonbuk FC`
  - `2764` Pohang Steelers → Polymarket `FC Pohang Steelers`
  - `2776` Ratchaburi → Polymarket `Ratchaburi FC`
  - `16400` Công An Nhân Dân → Polymarket `Cong An Ha Noi FC`
  - `2916` Al Shamal → Polymarket `Al-Shamal`
  - `2929` Al-Ahli Jeddah → Polymarket `Al Ahli Saudi FC`
  - `2932` Al-Hilal Saudi FC → Polymarket `Al Hilal SFC`
  - `2933` Al-Qadisiyah FC → Polymarket `Al Qadsiah`
  - `2938` Al-Ittihad FC → Polymarket `Al-Ittihad Club`
  - `2939` Al-Nassr → Polymarket `Al Nassr Club`
  - `2865` Al Ain → Polymarket `Al Ain FC`
  - `2871` Al-Jazira → Polymarket `Al Jazira (UAE)`
  - `2895` Al Sadd → Polymarket `Al Sadd SC`
  - `2903` Al-Gharafa → Polymarket `Al Gharafa SC`
  - `4217` Neftchi → Polymarket `FK Neftchi Fargona`
  - `4532` Al Hussein → Polymarket `Al Hussein Irbid`

## 数据来源

- [API-Football teams](https://www.api-football.com/documentation-v3#tag/Teams)
- [Polymarket Gamma teams](https://docs.polymarket.com/api-reference/sports/get-teams)
