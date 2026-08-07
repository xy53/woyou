# 卧游 (Woyou)

> 澄怀观道，卧以游之。——宗炳

**真实城市的文字旅行模拟。** 选一个国家、一座城市，旅行者用文字命令在真实的
街巷里旅行：看时段与天气流转的风景、和本地人聊出压箱底的故事、吃真实的地方菜、
找到地图上没有的深巷去处——最后带回一本自动写成的游记。

交互模式借鉴 [fox-river-valley](https://github.com/eckkk/fox-river-valley)：
**盲玩、旁观**。每条命令返回叙事文本 + 一行 `STATE {...}`，旅行者只凭输出决策。

```
〉 look
夕阳把河面烧成一条金红的带子，东山的棱线变成深蓝色的剪影。堤上的人
不知不觉都停了手里的事，朝着同一个方向坐着。

✦ 心愿达成：「在鸭川边看一次黄昏」

STATE {"day":2,"days":5,"t":7,"slot":"黄昏","city":"京都","loc":"鸭川河岸",...}
```

## 三分钟上手（零依赖，只需 uv 或 Python 3.10+）

```bash
# 离线示范：京都（手写内容包，不需要任何 API）
uv run play.py demo --mate aman --repl     # 带上吃货发小阿满，交互游玩

# 盲玩（单发命令接口，进度自动保存）
uv run play.py cmd look
uv run play.py cmd 去锦市场

# 浏览器观战页（旅行手帐风，实时刷新）
uv run play.py watch
```

## 全程零 API

游玩这一步完全离线——`demo` 的京都是手写内容包，任何 `new --city` 打开的城市也是
建城时就写死进缓存的内容包，引擎只读本地文件，不联网、不需要任何 API key。
`DEEPSEEK_API_KEY` **只有想当"建城人"的开发者、往内容库里加新城市时才需要**，
旅行者本身不用配置任何东西。

## 用 DeepSeek 建一座新城（开发者向）

1. 复制 `.env.example` 为 `.env`，填入你的 `DEEPSEEK_API_KEY`
2. 然后：

```bash
uv run play.py build --city 泉州        # 预先建城（调研+生成，约1-3分钟）
uv run play.py new --city 泉州 --days 5 --mate yanqiu
```

旅途中 `fly 奈良` 可以跨城——前提是已经用 `build` 生成过该城市的内容包。

**「真正的调研」是怎么做的：** 每座城生成时先跑一个**调研阶段**，DeepSeek 以研究者
身份产出一份事实性调研报告（`content/<城市>/research.md`：真实地名与当地写法、
历史掌故、物价量级、节令天气、本地人原型），再由第二阶段把报告转译成结构化内容包
（`pack.json`）。生成一次永久缓存，之后游玩零 token。可选 `WOYOU_WEB=1` 会在调研时
尝试抓取维基导游/维基百科作参考（部分网络环境不可用，失败自动跳过）。

一座城的生成成本：约 9-11 次调用、4-6 万 token，以 deepseek-chat 计不到一元人民币。
DeepSeek 只在建城时当写手，写完就退场——**建城之后，游玩全程零 API**，包括旅程
结束时的结算：引擎只读建城时写死的缓存，一步都不联网。

## 玩法设计与项目结构

详见 [DESIGN.md](DESIGN.md)——包含游戏机制的细节，盲玩的话建议先不看。

## 两种玩法

1. **命令行盲玩**（推荐）：`uv run play.py demo --mate aman` 开始，之后逐回合
   `uv run play.py cmd <命令>`——能执行命令的 agent（Claude Code / Codex 等）
   clone 下来就能直接玩，游戏会告诉它一切。想旁观可以开观战页，或者等旅程结束后看手帐。
2. **Python 接口**：`from woyou import new_trip, cmd`，在任何 agent 框架里循环调用。

旅程结束时（`end trip` 或睡满最后一晚）会自动在命令行打印一份「旅程结算」：
足迹、照片、故事、心愿、显影出的颜色，全离线生成、不落任何文件；想再看一遍，
随时 `uv run play.py report`。游记想导出成文件的话用 `uv run play.py export`
（日记的原始条目版）。旅行者还可以在游戏内输入 `share` 生成一份手帐风格的 HTML
分享页。
