# 欧协联（UECL）与欧罗巴（UEL）球队三方对照资料

把 **API-Football 英文名**、**Polymarket 英文名** 与 **中文名** 整合在同一张表，并已全部补录进 `football.json`。仅用于页面显示与赛果绑定的精确别名扩展，不参与交易身份自动确认。

## 数据来源与核验口径

- API-Football：`GET /teams?league=848&season=2023|2024`（欧协联）与 `GET /teams?league=3&season=2023|2024`（欧罗巴）取得队名、国家与队徽；2023/2024 两季名录未覆盖的球队另用 `GET /teams?search=` 与 `GET /teams?id=` 逐条核实 `team_id`。当前密钥为免费套餐，2025 赛季不开放。
- Polymarket：赛事中心展示的队名（Gamma 数据），逐条写入对应条目的 `aliases`。
- 中文名：采用国内媒体通行译名（百度百科、新浪、懂球帝、中文维基等）。

**注意：Polymarket 的 `providerId` 不是 API-Football 的 `team_id`**，两者不能互相替代，必须按本表映射。

## 对照表（欧协联 18 支）

| API-Football team_id | API-Football 英文名 | Polymarket 英文名 | 中文名 | 国家/地区 |
| ---: | --- | --- | --- | --- |
| 406 | Aarhus | Aarhus GF | 奥胡斯 | Denmark |
| 400 | FC Copenhagen | FC København | 哥本哈根 | Denmark |
| 398 | FC Nordsjaelland | FC Nordsjælland | 北西兰 | Denmark |
| 1012 | FC Thun | FC Thun | 图恩 | Switzerland |
| 1122 | FK Jablonec | FK Jablonec | 亚布洛内茨 | Czech-Republic |
| 3872 | Kauno Žalgiris | FK Kauno Žalgiris | 考纳斯扎尔吉里斯 | Lithuania |
| 608 | HNK Hajduk Split | HNK Hajduk Split | 哈伊杜克斯普利特 | Croatia |
| 254 | Heart Of Midlothian | Heart of Midlothian FC | 哈茨 | Scotland |
| 3342 | Inter Club d'Escaldes | Inter Club d'Escaldes | 伊斯卡尔德斯国际 | Andorra |
| 735 | St. Truiden | K. Sint-Truidense VV | 圣图尔登 | Belgium |
| 631 | Gent | KAA Gent | 根特 | Belgium |
| 853 | CSKA Sofia | PFK CSKA Sofia | 索菲亚中央陆军 | Bulgaria |
| 617 | Panathinaikos | Panathinaikós AO | 帕纳辛奈科斯 | Greece |
| 3403 | Pafos | Páfos FC | 帕福斯 | Cyprus |
| 664 | Kairat Almaty | Qairat FK | 阿拉木图凯拉特 | Kazakhstan |
| 10124 | Riga | Rīga FC | 里加FC | Latvia |
| 3502 | FC Iberia 1999 | SK Iberia 1999 | 第比利斯伊比利亚1999 | Georgia |
| 632 | Universitatea Craiova | Universitatea Craiova CS | 克拉约瓦大学 | Romania |

## 对照表（欧罗巴 10 支）

| API-Football team_id | API-Football 英文名 | Polymarket 英文名 | 中文名 | 国家/地区 |
| ---: | --- | --- | --- | --- |
| 3402 | Omonia Nicosia | AS Omónoia Leukosías | 奥莫尼亚 | Cyprus |
| 3683 | Ararat-Armenia | Ararat-Armenia FA | 阿拉拉特亚美尼亚 | Armenia |
| 567 | Plzen | FC Viktoria Plzeň | 比尔森胜利 | Czech-Republic |
| 347 | Lech Poznan | KKS Lech Poznań | 波兹南莱赫 | Poland |
| 563 | Hapoel Beer Sheva | MH Hapoel Be'er Sheva | 贝尔谢巴工人 | Israel |
| 1124 | OFI | OFI | OFI克里特 | Greece |
| 553 | Olympiakos Piraeus | Olympiakós SFP | 奥林匹亚科斯 | Greece |
| 646 | Levski Sofia | PFK Levski Sofia | 索菲亚列夫斯基 | Bulgaria |
| 554 | Anderlecht | RSC Anderlecht | 安德莱赫特 | Belgium |
| 4799 | Torreense | SC União Torreense | 托伦斯 | Portugal |

## 覆盖情况

- 欧协联 18 支、欧罗巴 10 支，共 **28 支** 均已补录进 `football.json`。
- 每条记录的 `name_en` 用 API-Football 原名、`name_zh` 用中文译名、`leagues` 标注 `UECL` 或 `UEL`，`aliases` 写入 Polymarket 队名，因此赛事中心的 Polymarket 名称可显示中文，赛果绑定的 API-Football 名称也能反查同一支球队。

## 数据来源

- [API-Football Teams](https://www.api-football.com/documentation-v3#tag/Teams)
