# 新增城市规范

> 从调研到可玩，一座城的完整流程。

## 总览

```
调研 → 生成 → 校验 → 照片 → 试玩 → 入库
```

新增一座城有两条路径：

| 路径 | 方法 | 适用场景 |
|---|---|---|
| **自动生成** | `uv run play.py build --city <城市>` | 快速出包，DeepSeek 一键完成 |
| **手写/精修** | 参考京都包手写 pack.json | 品质标杆，需要逐字打磨 |

两条路径最终产出同一结构：`content/<slug>/pack.json`（+ 可选照片目录）。

---

## 一、调研阶段

### 自动调研

```bash
# 纯 LLM 调研（默认）
uv run play.py build --city 泉州

# 加网络参考（维基导游/维基百科，部分网络环境不可用）
WOYOU_WEB=1 uv run play.py build --city 泉州
```

自动调研由 `woyou/research.py` 驱动，产出 `content/<slug>/research.md`。

### 调研报告结构（research.md）

| 章节 | 内容要求 |
|---|---|
| 城市概览 | 三五句话：气质与历史脉络 |
| 城区结构 | 3-5 个真实片区，每片一两句气质 |
| 必访地点（12-16 处） | 名称（含当地语言写法）、所在片区、类型、门票、开放时间、两三句真实细节 |
| 深巷（3-4 处） | 游客地图上不显眼但本地人珍视的真实去处 |
| 吃什么（8-10 样） | 名称、价格、在哪吃、来历或吃法 |
| 会遇到的人（5-6 位） | 本地人原型 + 真实的文化掌故素材（150-250 字/位） |
| 风物与节令 | 每月天气、节庆、时间感 |
| 实用信息 | 货币、物价量级、交通方式 |

### 真实性铁律

- 只写真实存在的地点，给出当地语言写法
- 历史与掌故必须属实，或明确标注「民间说法」
- 价格用当地货币真实量级
- **禁止发明不存在的店名、寺名、传说**——拿不准的宁可写得泛

### 手动调研（精修路线）

如果要做手写标杆包，建议额外做以下功课：

1. 翻阅维基导游（Wikivoyage）该城条目
2. 查看真实游记、本地博客、Google Maps 评论
3. 核实每个地名的当地语言写法
4. 确认门票、营业时间等信息的时效性
5. 收集 2-3 个「只有常住者才知道」的去处

---

## 二、生成管线

`uv run play.py build` 自动跑完以下 7 个阶段：

| 阶段 | 函数 | 产出 | 调用次数 |
|---|---|---|---|
| 1. 调研 | `build_research()` | research.md | 1 次 |
| 2. 城市元数据 | `gen_meta()` | meta（货币/物价/intro/outro/天气） | 1 次 |
| 3. 骨架 | `gen_skeleton()` | 片区 + 地点名录 + 隐藏点路线 | 1 次 |
| 4. 细写 | `gen_details()` | 每个地点的 look/sounds/explore/photo/join/revisit/mastered/shop | 每片区 1 次 |
| 5. 人物 | `gen_npcs()` | NPC：初见/认人/话题/压箱底故事 | 1 次 |
| 6. 杂项 | `gen_extras()` | dishes/events/wander/wishes/trivia | 1 次 |
| 7. 搭子 | `gen_moments()` | 三位旅伴的地点触发小剧场 | 1 次 |

总计约 9-11 次 LLM 调用，4-6 万 token。以 deepseek-chat 计不到一元人民币。

生成后自动运行 `sanitize_pack()` 程序化修补（id 规整、引用悬空兜底、数值范围修正），
再由 `validate_pack()` 校验结构完整性。

---

## 三、内容包结构（pack.json）

### 数量基准（以京都手写包为标杆）

| 组件 | 京都实际 | 生成目标 | 说明 |
|---|---|---|---|
| 片区 districts | 3 | 3-5 | 真实城区划分 |
| 地点 locations | 12 | 14-17 | 含 2-4 处隐藏点 |
| 其中 starter | 10 | 11-13 | 游客地图上可见 |
| 其中 hidden | 2 | 2-4 | 需 NPC 指点或 explore 引出 |
| NPC npcs | 5 | 4-6 | 分布在不同地点 |
| 吃食 dishes | 8 | 8-10 | 全部真实 |
| 事件 events | 7 | 6-9 | 含雨天/夜晚限定 |
| 心愿 wishes | 17 | 12-16 | 难度错落，含 listen/join/postcard 型 |
| 漫步 wander | 15 | 10-14 | 无名的日常小景 |
| 小知识 trivia | 9 | 6-10 | 每条不超 60 字 |
| 搭子时刻 | 3 人 × 3-4 个 | 同左 | 挑和人设来电的地点 |

### 地点类型（type 字段）

`temple` `shrine` `market` `street` `river` `park` `path` `viewpoint`
`museum` `shop` `nightlife` `cafe` `landmark` `square`

一座城的类型应尽量多样。

### 关键文本字段规范

| 字段 | 字数 | 要求 |
|---|---|---|
| meta.intro | 180-260 | 第二人称抵达叙事，有这座城独有的气味与声音 |
| meta.outro_shell | 30-50 | 离城画面，不提任何具体活动或地点 |
| meta.outro_closing | 40-60 | 城市性格收尾，不提玩家做过的事 |
| look.default | 60-130 | 第二人称现在时，写实、克制 |
| sounds.default | 60-120 | **只写听觉**，禁止视觉描写 |
| explore 每层 | 80-150 | 层层深入，真实细节 |
| photo.default | 60-100 | 按下快门时取景框里的画面 |
| npc.meet | 80-130 | 初见场景 |
| npc.story.text | 150-260 | 第一人称口述，真实掌故 |
| npc.story.echo | — | **必须逐字出现在 story.text 里**（程序校验） |
| join.text | 100-160 | 动作写清楚到读者能照做 |
| revisit | 60-100 | 第二次同一时辰来时的场景，不抒情 |
| mastered | 60-100 | 逛尽的完成感，从客人变熟人 |
| wander.text | 60-120 | 无名、非景点、日常的东西 |

### 时段变体（look/sounds）

key 约定：`default`（必有）、`morning`、`forenoon`、`afternoon`、`dusk`、`night`、`rain`。
每个地点至少 default + 2 个时段变体。有雨情味的地方加 `rain`。

### 心愿判定 DSL（wishes.check）

```
{"type":"look",    "loc":可选, "slot":可选, "weather":可选}
{"type":"listen",  "loc":可选, "slot":可选, "weather":可选}
{"type":"visit",   "loc":"地点id"}
{"type":"eat",     "dish":"吃食id"} 或 {"type":"eat","tag":"local"}
{"type":"photo",   "loc":可选, "slot":可选, "weather":可选}
{"type":"buy",     "tag":"gift"}
{"type":"join",    "loc":可选}
{"type":"postcard"}
{"type":"story",   "count":1}
{"type":"gem",     "count":1}
{"type":"explore", "loc":"地点id", "level":2}
{"type":"rest",    "loc_type":"cafe"}
```

心愿的说法要和判定一致。必须包含 1-2 条 listen 型、1 条 join 型。

---

## 四、照片（可选）

照片给 `report` 的拍立得功能用。没有照片也完全可玩（拍立得退化为纯文字描述）。

### 目录结构

```
content/<slug>/photos/
  manifest.json    照片清单
  <id>.jpg         照片文件（建议 1200px 长边）
```

### manifest.json 格式

```json
{
  "<地点id>": {
    "file": "<id>.jpg",
    "title": "地点中文名",
    "author": "拍摄者",
    "license": "CC BY-SA 4.0",
    "source": "来源URL（Wikimedia Commons 等）",
    "scene_note": "画面描述（给引擎匹配用）",
    "compatible_slots": ["上午", "午后"],
    "weather_hint": "晴" 或 null,
    "season_months": [3, 4] 或 null
  }
}
```

### 照片来源要求

- **只使用 Creative Commons 许可的照片**（CC0、CC BY、CC BY-SA）
- 优先从 Wikimedia Commons 获取
- 必须标注作者、许可协议、来源 URL
- 不使用版权不明的照片

---

## 五、校验与试玩

### 程序化校验

生成完成后引擎会自动校验。手写包也应通过校验：

```bash
uv run python -c "from woyou.content import load_pack; load_pack('<slug>'); print('OK')"
```

### 校验项

`validate_pack()` 检查的主要项目：

- meta 必要字段完整（city, country, currency, hotel_rate, intro 等）
- 每个地点有 id, name, district, type, look.default
- district 引用存在
- starter 至少 3 个
- 隐藏点有 reveal 通路（explore/npc/event）
- NPC 的 loc 引用存在
- dish 的 locs 引用存在
- wish 的 check 引用存在

### 试玩检查清单

用 demo 模式跑一遍：

```bash
uv run play.py demo --city <slug> --repl
```

检查项：

- [ ] 开场 intro 读起来自然，有城市特色
- [ ] 各地点 look 在不同时段有变化
- [ ] listen 只有声音，没有视觉描写
- [ ] explore 层层深入，最深处有发现
- [ ] NPC 初见自然，聊两三轮后讲出故事
- [ ] story.echo 确实是 story.text 的原文
- [ ] 隐藏地点能通过指定方式解锁
- [ ] 心愿能通过合理游玩达成
- [ ] join 的动作足够具体
- [ ] 吃食价格合理、有画面感
- [ ] wander 是日常小景，不是景点介绍
- [ ] 搭子时刻符合人设（阿满→吃、砚秋→历史、小柒→光影）
- [ ] `end trip` 后 outro 不提及具体活动
- [ ] `report` 能正常生成
- [ ] `share` 手帐页能正常生成

---

## 六、跨城旅行注意事项

玩家可以 `fly <城市>` 跨城。新城在首次 fly 时现场生成（如果没缓存）。

跨城相关的 meta 字段：

- `train_cost_hint`：城际火车/巴士票价（同国内跨城用）
- `flight_cost_hint`：飞往邻国主要城市的经济舱票价
- `cny_rate`：1 元人民币约合多少当地货币（跨国时自动按汇率换钱）

这些字段应反映真实物价量级。

---

## 七、常见生成问题与修复

| 问题 | 原因 | 修复 |
|---|---|---|
| NPC 的 story.echo 校验失败 | echo 不是 story.text 的原文子串 | sanitize 自动移除；手动修复：从 text 中逐字摘出 |
| 隐藏点无通路 | reveal_via 指向的地点没在 explore/npc 里实际写 reveal | sanitize 自动在最近的 starter 的 explore 末尾补一段 |
| 心愿的 join 指向没写 join 的地点 | 生成器把 join 写给了别的地点 | sanitize 自动改为不限地点 |
| sounds 里混入视觉描写 | LLM 没遵守「只写听觉」指令 | 手动修正或重新生成该地点 |
| 地点类型不在允许列表 | LLM 发明了新类型 | sanitize 自动改为 street |
| 价格量级离谱 | LLM 对当地物价不熟 | 手动修正 pack.json 里的数值 |

---

## 八、入库清单

最终提交前确认：

- [ ] `content/<slug>/pack.json` 通过 `load_pack()` 校验
- [ ] 已完成至少一轮完整试玩（3 天以上）
- [ ] 照片（如有）全部是 CC 许可，manifest.json 格式正确
- [ ] research.md 不提交到 git（已在 .gitignore）
- [ ] 不提交 pack.debug.json（已在 .gitignore）

提交：

```bash
git add content/<slug>/pack.json content/<slug>/photos/
git commit -m "feat: 新增<城市>内容包"
git push
```
