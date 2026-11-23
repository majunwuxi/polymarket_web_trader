# 亚冠精英联赛（ACLE）球队三方对照资料

把 **API-Football 英文名**、**Polymarket 英文名** 与 **中文名** 整合在同一张表，并已全部补录进 `football.json`。仅用于页面显示与赛果绑定的精确别名扩展，不参与交易身份自动确认。

## 数据来源与核验口径

- API-Football：联赛 `league=17`（AFC Champions League Elite Cup）。当前密钥为免费套餐，仅开放 2022–2024 赛季；下表 35 个 `team_id` 均已用 `GET /teams?id=` 逐条核实，英文名、国家、队徽均取接口原值。
- Polymarket：Gamma `GET /teams?league=acle` 返回的 35 支现役球队英文名（2026-11 查询），写入各条目的 `aliases`。
- 中文名：采用国内媒体通行译名（百度百科、新浪、懂球帝、中文维基等），见各条目的 `name_zh`。

**注意：Polymarket 的 `providerId` 不是 API-Football 的 `team_id`**（例如英超 Crystal Palace 的 `providerId` 为 512，API-Football `team_id` 为 52），两者不能互相替代，必须按本表映射。

## 对照表（共 35 支，均已补录）

| API-Football team_id | API-Football 英文名 | Polymarket 英文名 | 中文名 | 国家/地区 |
| ---: | --- | --- | --- | --- |
| 2929 | Al-Ahli Jeddah | Al Ahli Saudi FC | 吉达国民 | Saudi-Arabia |
| 2932 | Al-Hilal Saudi FC | Al Hilal SFC | 利雅得新月 | Saudi-Arabia |
| 2933 | Al-Qadisiyah FC | Al Qadsiah | 胡拜尔库迪西亚 | Saudi-Arabia |
| 2938 | Al-Ittihad FC | Al-Ittihad Club | 吉达联合 | Saudi-Arabia |
| 2939 | Al-Nassr | Al Nassr Club | 利雅得胜利 | Saudi-Arabia |
| 2865 | Al Ain | Al Ain FC | 艾因 | United-Arab-Emirates |
| 2870 | Shabab Al Ahli Dubai | Shabab Al Ahli Dubai | 迪拜青年国民 | United-Arab-Emirates |
| 2871 | Al-Jazira | Al Jazira (UAE) | 阿布扎比半岛 | United-Arab-Emirates |
| 2872 | Al-Wasl FC | Al Wasl FC | 迪拜瓦斯尔 | United-Arab-Emirates |
| 2895 | Al Sadd | Al Sadd SC | 萨德 | Qatar |
| 2903 | Al-Gharafa | Al Gharafa SC | 加拉法 | Qatar |
| 2916 | Al Shamal | Al-Shamal | 沙马尔 | Qatar |
| 2733 | Esteghlal FC | Esteghlal FC Tehran | 德黑兰独立 | Iran |
| 2737 | Tractor Sazi | Tractor Tabriz | 大不里士拖拉机 | Iran |
| 4217 | Neftchi | FK Neftchi Fargona | 费尔干纳纳夫奇 | Uzbekistan |
| 4220 | Pakhtakor | Pakhtakor | 塔什干棉农 | Uzbekistan |
| 4532 | Al Hussein | Al Hussein Irbid | 伊尔比德侯赛因 | Jordan |
| 8009 | Al Quwa Al Jawiya | Al Quwa Al Jawiya | 巴格达空军 | Iraq |
| 830 | Beijing Guoan | Beijing Guoan | 北京国安 | China |
| 836 | SHANGHAI SIPG | Shanghai Port FC | 上海海港 | China |
| 281 | Kashiwa Reysol | Kashiwa Reysol | 柏太阳神 | Japan |
| 289 | Vissel Kobe | Vissel Kobe | 神户胜利船 | Japan |
| 290 | Kashima | Kashima Antlers | 鹿岛鹿角 | Japan |
| 293 | Gamba Osaka | Gamba Osaka | 大阪钢巴 | Japan |
| 302 | Kyoto Sanga | Kyoto Sanga FC | 京都不死鸟 | Japan |
| 2746 | Gangwon FC | Gangwon FC | 江原FC | South-Korea |
| 2750 | Daejeon Citizen | Daejeon Citizen FC | 大田市民 | South-Korea |
| 2762 | Jeonbuk Motors | Jeonbuk FC | 全北现代 | South-Korea |
| 2764 | Pohang Steelers | FC Pohang Steelers | 浦项制铁 | South-Korea |
| 2776 | Ratchaburi | Ratchaburi FC | 叻丕府 | Thailand |
| 2780 | Buriram United | Buriram United | 武里南联 | Thailand |
| 2789 | Port FC | Port FC | 泰港 | Thailand |
| 946 | Newcastle Jets | Newcastle United Jets | 纽卡斯尔喷气机 | Australia |
| 2523 | Johor Darul Takzim FC | Johor Darul Ta'zim | 柔佛新山 | Malaysia |
| 16400 | Công An Nhân Dân | Cong An Ha Noi FC | 河内公安 | Vietnam |

## 覆盖情况

- 全部 **35 支** 均已在 `football.json` 中（本轮新增 24 支，并为原有 5 支补上 Polymarket 别名）。
- 每条记录的 `name_en`/`name_zh` 用 API-Football 名与中文译名，`aliases` 写入 Polymarket 队名，因此赛事中心的 Polymarket 名称可显示中文，赛果绑定的 API-Football 名称也能反查同一支球队。

## 译名调整说明

按国内媒体通行用法，以下译名与此前版本不同：

- `2929` Al-Ahli Jeddah：吉达阿赫利 → **吉达国民**（2025 亚冠精英冠军，国内媒体通用“吉达国民”）
- `2871` Al-Jazira：半岛 → **阿布扎比半岛**（又称亚吉拉）
- `2870` Shabab Al Ahli Dubai：迪拜国民 → **迪拜青年国民**
- `2872` Al-Wasl FC：瓦斯尔 → **迪拜瓦斯尔**（又称迪拜祈祷）
- `2776` Ratchaburi：叻丕 → **叻丕府**（又译拉查武里）
- `2789` Port FC：港FC → **泰港**
- `8009` Al Quwa Al Jawiya：空军 → **巴格达空军**
- `4532` Al Hussein：侯赛因伊尔比德 → **伊尔比德侯赛因**
- `2933` Al-Qadisiyah FC：卡迪西亚 → **胡拜尔库迪西亚**
- `2523` Johor Darul Takzim FC：柔佛达鲁塔克辛 → **柔佛新山**（简称柔佛DT）
- `2780` Buriram United：布里兰 → **武里南联**（旧名保留在 `aliases`）
- `2762` Jeonbuk Motors：全北现代汽车 → **全北现代**（旧名保留在 `aliases`）

## 数据来源

- [API-Football Teams](https://www.api-football.com/documentation-v3#tag/Teams)
- [Polymarket Gamma teams](https://docs.polymarket.com/api-reference/sports/get-teams)
