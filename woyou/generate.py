# -*- coding: utf-8 -*-
"""卧游 · 城市内容包生成管线（DeepSeek）。

流程：normalize(城市名) → research(调研报告) → skeleton(城区+地点名录)
      → details(逐区细写) → npcs(人物与真实故事) → extras(吃食/事件/心愿/小知识)
      → moments(旅游搭子脚本) → sanitize + validate → 落盘缓存。

生成只发生在「第一次去某座城」；之后游玩全程读缓存，不再花 token。
所有 prompt 都强调真实性：真实地名、真实历史、真实物价量级；
结构问题（引用悬空、id 不合法等）由 sanitize 程序化兜底，保证可玩。
"""
import json
import re
import time

from . import content
from .llm import DeepSeek, parse_json_loose, LLMError
from .research import build_research
from .util import CONTENT_DIR, SLOTS, slugify, write_json

TIME_RULES = (
    "时刻制：一天分 10 刻，t=0..9。t0-1=清晨(约6-9点)，t2-3=上午(9-12)，"
    "t4-5=午后(12-16)，t6-7=黄昏(16-19)，t8-9=夜(19-23)。"
    "hours 字段用 [起,止] 两个整数表示开放刻区间（含两端），全天开放写 null。"
)

LOOK_RULES = (
    "look/photo/sounds 这类多变体文本对象的 key 约定：default 必有；"
    "可选 morning(清晨)/forenoon(上午)/afternoon(午后)/dusk(黄昏)/night(夜晚)/rain(雨天)。"
    "每段 60-130 字，第二人称现在时，写实、具体、克制，像好游记，不堆形容词。"
)

FACT_RULES = (
    "真实性铁律：只写真实存在的地点与真实文化。地名给出当地语言写法；"
    "历史与掌故必须属实或明确是流传的民间说法；价格用当地货币真实量级；"
    "禁止发明不存在的店名、寺名、传说。拿不准的细节宁可写得泛，不可编造。"
)


def _say(quiet, msg):
    if not quiet:
        print(msg, flush=True)


def _call_json(client, system, user, max_tokens=6000, temperature=0.75, tries=2):
    last = None
    for i in range(tries):
        try:
            return client.chat_json(system, user, max_tokens=max_tokens,
                                    temperature=temperature)
        except json.JSONDecodeError as e:
            last = e
            user = user + "\n\n（注意：上一次输出不是合法 JSON，请只输出一个 JSON 对象。）"
        except LLMError:
            raise
    raise LLMError(f"JSON 解析失败：{last}")


# ---------------------------------------------------------------- 各阶段

META_SYS = "你是旅行模拟游戏的世界设定师，输出严格的 JSON。" + FACT_RULES

META_PROMPT_TAIL = """
只输出一个 JSON 对象，字段如下：
{
  "city": "城市中文名", "city_en": "英文名", "country": "国家中文名",
  "currency": "当地货币中文名", "currency_symbol": "货币符号",
  "cny_rate": 1元人民币约合多少当地货币(数字),
  "hotel_rate": 经济型旅舍一晚价格(整数,当地货币),
  "transit_fare": 市内公共交通单程票价(整数),
  "train_cost_hint": 城际火车/巴士到邻近旅行城市的票价(整数),
  "flight_cost_hint": 飞往邻国主要城市的经济舱票价量级(整数),
  "postcard_price": 一张明信片加一枚邮票在当地的价格(整数,当地货币),
  "postcard_flavor": "30-60字、以「你」开头的完整一句（句号结尾）：在这座城买明信片的小场景——
                      在哪种店、挑的是什么图案的明信片、贴的是什么邮票。要具体到这座城的真实街道和本地特色。",
  "default_budget": 建议的5天旅行预算(整数,含住宿吃饭门票市内交通,宽裕适中),
  "default_days": 5,
  "intro": "180-260字的抵达叙事：第二人称，从落地/下车写到进城放下行李，要有这座城独有的气味与声音",
  "outro_shell": "30-50字的离城画面：列车启动/飞机起飞，城市在窗外退远。不提任何具体活动或地点",
  "outro_closing": "40-60字的城市性格收尾：这座城给人留下的底色印象。不提玩家做过的事",
  "city_brief": "100字左右的城市底色速写，供叙事引擎当背景知识",
  "weather": {"1": [["晴",4],["阴",3]], "2": [...], ..., "12": [...], "default": [["晴",1]]}
}
weather 是每月天气模式：月份为 key，值是 [天气名, 权重整数] 列表，天气名用中文
（晴/多云/阴/小雨/雷阵雨/雪…按当地气候写实），12 个月都要有，权重反映概率。
"""


def gen_meta(client, city_input):
    user = f"目标城市：{city_input}\n" + META_PROMPT_TAIL
    return _call_json(client, META_SYS, user, max_tokens=3000, temperature=0.6)


SKELETON_SYS = ("你是旅行模拟游戏的关卡设计师，基于调研报告规划一座城的游玩地图，"
                "输出严格的 JSON。" + FACT_RULES)

SKELETON_RULES = """
只输出一个 JSON 对象：
{
  "districts": [{"id":"ascii小写", "name":"片区中文名", "brief":"一句气质",
                 "intro":"60-100字，玩家第一次踏进这片区时的过场叙事"}],
  "start_loc": "玩家旅舍落脚点附近的一个 starter 地点 id",
  "locations": [{
     "id":"ascii小写下划线", "name":"中文名", "name_local":"当地语言写法",
     "aliases":["常用简称或别名, 2-4个"],
     "district":"所属片区id", "type":"类型",
     "starter": true或false, "fee": 门票整数(免费0),
     "hours": [起,止] 或 null,
     "brief": "不超过40字的一句话（地图上显示）",
     "reveal_via": 仅隐藏点需要: {"kind":"npc"或"explore", "at":"某个starter地点id"}
  }]
}
要求：
- 3-5 个片区；地点共 14-17 处，全部真实存在，类型尽量多样；
- type 只能取：temple/shrine/market/street/river/park/path/viewpoint/museum/shop/nightlife/cafe/landmark/square/mosque/church/canal/harbor/bath/palace/bridge/garden/ruins（按城市特色选最贴切的）
- 其中 11-13 处 starter:true（游客地图上有的），2-4 处 starter:false（深巷：
  本地人才知道的真实去处，游戏中需要被人指点或深度探索才会出现在地图上），
  隐藏点必须给 reveal_via，指定由哪个 starter 地点的人（npc）或深逛（explore）引出；
- """ + TIME_RULES


def gen_skeleton(client, meta, research):
    user = (f"城市：{meta['city']}（{meta['country']}）\n\n调研报告：\n{research}\n\n"
            + SKELETON_RULES)
    return _call_json(client, SKELETON_SYS, user, max_tokens=5000)


DETAIL_SYS = ("你是旅行文学作者兼游戏文案，为地点撰写可游玩的多层文本，"
              "输出严格的 JSON。" + FACT_RULES)

DETAIL_RULES = """
为上面列出的每个地点写细节。只输出一个 JSON 对象：
{"locations": [{
   "id": "对应的地点id",
   "look": {"default":"...", 以及至少两个时段变体, 有雨情味的地方加 "rain"},
   "sounds": {"default":"...", 另加 1-3 个变体（morning/dusk/night/rain 等）},
   "explore": [
      {"text":"第一层：走进去，看见什么"},
      {"text":"第二层：更细的纹理、人的痕迹"},
      {"text":"第三层(可选)：这个地方的底色，或一个容易被错过的细节",
       "gem":true 仅当这层是「本地人才懂的发现」, "title":"发现的名字(gem时)"}
   ],
   "photo": {"default":"按下快门时取景框里的画面，60-100字"},
   "join": 可选，见下（本片区约一半地点才有）,
   "revisit": 可选，见下（本片区至多 1 处）,
   "mastered": 可选，见下（本片区至多 1 处）,
   "rest_text": "仅咖啡店/园林/寺院等适合歇脚处：坐下歇息时的一段（可省略）",
   "shop": [仅市场/商店/老街，1-3件 {"id":"ascii","name":"","price":整数,
            "tags":["gift"或"food"或"craft"或"book"],"text":"不超50字"}]
}]}
要求：
- explore 每层 80-150 字，第二人称，层层深入，写真实细节（调研报告里提过的
  容易错过之处优先用上）；
- sounds：每个地点都必须有。60-120 字，**只写听觉**——声音的层次与远近、
  质地与节奏、间歇里的安静、忽然近了的一声。严禁任何视觉描写：不写颜色、
  光线、看见了什么、谁长什么样。玩家是闭着眼睛在听，要让人光凭耳朵就认得出
  这是哪儿（不同时段/雨天的变体，声音会换一套，不是同一段话改几个字）；
  若地点有休业时段（hours 之外的时辰），对应时段的变体要写「从门外听到」的
  声音——门里的安静、卷帘的动静、街面替它发出的声响；
- join（可选，本片区约一半地点写；只给市场／寺院／清真寺／教堂／河岸／老街／
  咖啡店／浴场这类「有本地人的做法可以照着模仿」的地方写）：
  {"text":"100-160字，第二人称：照着本地人的样子，把某件具体的小事做一次——
           排队的规矩、参拜的礼数、点单的暗语、跳石过河、洗手的次序、
           进门的一句招呼……动作要写清楚到读者能照做，允许笨拙，
           笨拙里要有乐趣；结尾常常是本地人给出的一个极小的认可",
   "journal":"意外"（多数配上）, "title":"日记标题（有 journal 时必给）"}；
- revisit（可选，本片区至多 1 处，全城合计 2-4 处；挑最有「老位置感」的地点：
  河岸／老咖啡店／小径／常经过的街角）：60-100 字，玩家第二次在同一时辰来到
  这里时读到的一段——这里认得他了：老位置空着、猫不再躲、老板点了下头。
  只呈现，不解释，不抒情；
- mastered（可选，本片区至多 1 处，全城合计 2-3 处；挑分量最重的大地点）：
  60-100 字，把这里逛尽之后的完成感——写「你已经熟到什么程度」的具体证据，
  从客人变成了熟人。绝不能写成「这里没有新内容了」；
- 若某地点被指定了「要在这里引出隐藏点 X」（见下方说明），在该地点的某一层
  explore 文字里自然带出，并在该层加 "reveal": {"loc": "X的id"}；
- """ + LOOK_RULES + "\n- " + TIME_RULES


def gen_details(client, meta, research, skeleton, district):
    locs = [l for l in skeleton["locations"] if l.get("district") == district["id"]]
    if not locs:
        return {"locations": []}
    roster = json.dumps(locs, ensure_ascii=False, indent=1)
    reveal_notes = []
    for l in skeleton["locations"]:
        rv = l.get("reveal_via") or {}
        if rv.get("kind") == "explore":
            at = rv.get("at")
            if any(x["id"] == at for x in locs):
                reveal_notes.append(f"- 在「{at}」的 explore 里引出隐藏点「{l['id']}」({l['name']})")
    notes = ("\n隐藏点引出安排：\n" + "\n".join(reveal_notes)) if reveal_notes else ""
    user = (f"城市：{meta['city']}。当前片区：{district['name']}。\n"
            f"本片区地点名录：\n{roster}\n{notes}\n\n"
            f"（调研报告节选，供核对事实）\n{research[:5000]}\n\n" + DETAIL_RULES)
    return _call_json(client, DETAIL_SYS, user, max_tokens=8000)


NPC_SYS = ("你是旅行模拟游戏的人物作者，为一座城写活生生的本地人，"
           "输出严格的 JSON。" + FACT_RULES)

NPC_RULES = """
只输出一个 JSON 对象：
{"npcs": [{
  "id":"ascii小写", "name":"称呼式名字（如：腌菜店的阿婆／河边的大学生乐手）",
  "loc":"所在地点id", "slots":[起,止] 出现时段或 null,
  "persona":"50字人设，给叙事引擎用",
  "meet":"80-130字初见场景：他/她正在做什么，怎么和旅行者搭上话",
  "recall":"40-80字：隔天再见时认出旅行者的那句话与那个动作。要让人一眼看出
            回头客和游客是两种待遇——多夹的那一片、替你留着的那本书、
            替你空着的老位子、少问的那一句。以他/她的台词开头最好",
  "topics":[{"t":"话题名","text":"60-120字他会聊的内容"}, 2-4个,
            可以在某个话题里加 "reveal":{"loc":"隐藏点id"} 表示他指点你去某处],
  "story":{"after_talks": 2或3, "title":"故事名",
           "text":"150-260字：一段真实的历史/民俗/行业掌故，讲成第一人称口述，
                   这是这个人物压箱底的东西，聊熟了才讲",
           "echo":"从 text 里逐字摘出的最有余味的一句（必须是 text 的原文片段，
                   一字不改、不加句末标点；旅行报告会把它当作这个人留下的回声）",
           "reveal": 可选 {"loc":"隐藏点id"}}
}]}
要求：
- 4-6 位，分布在不同地点（市场/老街/寺庙/咖啡店/河边优先），slots 要符合身份
  （夜市老板娘在夜里，早市摊主在清晨）；
- recall 每位都要写，且要和这个人的行当对得上（卖腌菜的用手，卖书的用书）；
- story 必须取材于调研报告「会遇到的人」与「掌故与传说」里的真实素材；
- story.echo 必须逐字出现在 story.text 里（会被程序校验，对不上就丢弃），
  挑那句余味最长的——不是概括，是原话；
- 下方若列出「由 npc 引出的隐藏点」，安排对应地点的人物在 topic 或 story 里 reveal 它。
"""


def gen_npcs(client, meta, research, skeleton):
    starters = [{"id": l["id"], "name": l["name"], "district": l["district"],
                 "type": l["type"]}
                for l in skeleton["locations"] if l.get("starter")]
    reveal_notes = []
    for l in skeleton["locations"]:
        rv = l.get("reveal_via") or {}
        if rv.get("kind") == "npc":
            reveal_notes.append(f"- 「{rv.get('at')}」的人物要引出隐藏点「{l['id']}」({l['name']})")
    notes = ("\n由 npc 引出的隐藏点：\n" + "\n".join(reveal_notes)) if reveal_notes else ""
    user = (f"城市：{meta['city']}。可安排人物的地点：\n"
            f"{json.dumps(starters, ensure_ascii=False)}\n{notes}\n\n"
            f"调研报告：\n{research}\n\n" + NPC_RULES)
    return _call_json(client, NPC_SYS, user, max_tokens=7000)


EXTRAS_SYS = ("你是旅行模拟游戏的系统内容作者，输出严格的 JSON。" + FACT_RULES)

EXTRAS_RULES = """
只输出一个 JSON 对象，含五个数组：
{"dishes": [{"id":"ascii","name":"吃食名","price":整数,
             "locs":["能吃到它的1-3个地点id"],"tags":["local"等],
             "text":"60-100字：端上来的样子、吃法、来历","energy":18-30}],
 "events": [{"id":"ascii","chance":0.1到0.25,"once":true或false,
             "weather":"雨" 可选(天气名包含此字才触发),
             "slots":[起,止] 可选,"district":"片区id" 可选,"loc":"地点id" 可选,
             "text":"60-130字：路上撞见的一幕",
             "journal":"意外" 可选(值得记进日记的才加),"title":"日记标题(有journal时)"}],
 "wander": [{"text":"60-120字：不带目的地闲逛时撞见的小景",
             "district":"片区id" 可选,"slot":"清晨/上午/午后/黄昏/夜晚 之一" 可选,
             "weather":"雨" 可选}],
 "wishes": [{"id":"ascii","text":"心愿的说法（像旅人手帐里写的）","check":{...}}],
 "trivia": ["6-10条这座城可靠的小知识，每条不超60字"]}

wishes 的 check 判定 DSL（type 必选其一，引用必须用真实存在的 id）：
 {"type":"look","loc":可选,"slot":"清晨/上午/午后/黄昏/夜晚"可选,"weather":"雨"可选}
 {"type":"listen","loc":可选,"slot":可选,"weather":可选}   闭上眼睛听一个地方
 {"type":"visit","loc":"地点id"}     {"type":"eat","dish":"吃食id"} 或 {"type":"eat","tag":"local"}
 {"type":"photo","loc":可选,"slot":可选,"weather":可选}   {"type":"buy","tag":"gift"}
 {"type":"join","loc":可选}          照着本地人的样子做一次那里的规矩
 {"type":"postcard"}                 寄出一张明信片
 {"type":"story","count":1}          {"type":"gem","count":1}
 {"type":"discovery","count":1}      找到隐藏地点或 gem（比 gem 宽松）
 {"type":"explore","loc":"地点id","level":2}              {"type":"rest","loc_type":"cafe"}
要求：
- dishes 8-10 样，全部真实；events 6-9 个，其中 1-2 个雨天限定、1-2 个夜晚限定；
- wander 10-14 条：玩家漫无目的地走时撞见的东西，写「无名的、非景点的、日常的」——
  小巷尽头的壁龛、窗台上晒的腌菜坛子、二楼晾出来的被子、门口给猫留的水碗、
  老人坐在台阶上扇扇子、墙角贴的手写告示、深夜还亮着灯的裁缝铺……
  要有生活的证据感：看得出有人在照料它、有人天天从它旁边过；
  意象要贴合这座城的文化和气候，不要套用其他城市的典型物件；
  不写已经在地点列表里的地方，不写成景点介绍，不给它名字也没关系；
  半数挂 district（写出那个片区的性格），少数挂 slot 或 weather:"雨"；
- wishes 12-16 条：这是给玩家自己挑选的「心愿清单」菜单，玩家只会抄走其中几条，
  所以选项要足、质感要厚——每条都像旅人手帐里会写的话，带这座城独有的具体意象
  （「在雨里逛一次老街」而不是「完成look动作」；点出真实的河、真实的坂道、真实的味道）；
  难度错落：有的头两天就能撞上，有的要走到深处、聊到熟了才行，还有一两条
  要靠隐藏地点才能达成（这会引着人往深处走）；
  其中必须有 1-2 条 listen 型（写成「听」的说法：晚钟、市声、雨打棚顶），
  1 条 join 型（loc 只能从下面「写了入乡随俗的地点」里挑；那份名单为空就不给 loc），
  postcard 型可选一条；
- 心愿判定要和它的说法一致。
"""


def gen_extras(client, meta, research, skeleton, join_locs=None):
    locs = [{"id": l["id"], "name": l["name"], "type": l["type"],
             "district": l["district"]} for l in skeleton["locations"]]
    dists = [{"id": d["id"], "name": d["name"]} for d in skeleton["districts"]]
    joinable = json.dumps(list(join_locs or []), ensure_ascii=False)
    user = (f"城市：{meta['city']}。\n地点列表：{json.dumps(locs, ensure_ascii=False)}\n"
            f"片区：{json.dumps(dists, ensure_ascii=False)}\n"
            f"写了入乡随俗（join）的地点：{joinable}\n\n"
            f"调研报告：\n{research}\n\n" + EXTRAS_RULES)
    return _call_json(client, EXTRAS_SYS, user, max_tokens=8000)


MOMENTS_SYS = ("你是旅行模拟游戏的剧情作者，为「旅游搭子」写地点触发的小剧场，"
               "输出严格的 JSON。写人要有生活质感，不煽情。")

MOMENTS_RULES = """
三位搭子的人设：
- aman（阿满）：吃货发小，见到市场和小吃摊走不动路，嘴上没正形但把最后一口留给你。
- yanqiu（砚秋）：历史控学长，在古迹前会忽然安静，然后讲出一段来历，口头禅「你看，这里有意思」。
- xiaoqi（小柒）：摄影系妹妹，背旧相机，永远在等光，会偷拍你，坚持「照片里得有人气」。

只输出一个 JSON 对象：
{"moments": {
  "aman":  [{"loc":"地点id","text":"90-160字：在这个地点，他会拉着你做什么、说什么（带对话）",
             "journal":"人物" 可选,"title":"日记标题(有journal时)"}],
  "yanqiu": [...], "xiaoqi": [...]
}}
要求：
- 每位搭子 3-4 个时刻，选和人设最来电的地点（阿满配市场夜市、砚秋配寺庙古迹博物馆、
  小柒配河岸眺望点老街）；三人别全挤在同一批地点；
- 内容要用到该地点真实的细节（调研报告里有）；至少一半配 journal:"人物"。
"""


def gen_moments(client, meta, research, skeleton):
    locs = [{"id": l["id"], "name": l["name"], "type": l["type"]}
            for l in skeleton["locations"] if l.get("starter")]
    user = (f"城市：{meta['city']}。可用地点：{json.dumps(locs, ensure_ascii=False)}\n\n"
            f"调研报告节选：\n{research[:4000]}\n\n" + MOMENTS_RULES)
    return _call_json(client, MOMENTS_SYS, user, max_tokens=5000)


# ---------------------------------------------------------------- 清洗与兜底

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _fix_id(raw, fallback):
    s = str(raw or "").strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    if not s or not _ID_RE.match(s):
        return fallback
    return s


def _int(v, default=0):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def sanitize_pack(pack: dict) -> list:
    """程序化修补常见生成问题；返回修补日志。"""
    log = []
    meta = pack.setdefault("meta", {})

    # --- id 规整（地点/片区），并建立重写映射 ---
    dmap, lmap = {}, {}
    for i, d in enumerate(pack.get("districts", [])):
        new = _fix_id(d.get("id"), f"d{i}")
        if new != d.get("id"):
            log.append(f"district id 规整 {d.get('id')} -> {new}")
        dmap[d.get("id")] = new
        d["id"] = new
    seen = set()
    for i, l in enumerate(pack.get("locations", [])):
        new = _fix_id(l.get("id"), f"loc{i}")
        if new in seen:
            new = f"{new}_{i}"
        seen.add(new)
        if new != l.get("id"):
            log.append(f"location id 规整 {l.get('id')} -> {new}")
        lmap[l.get("id")] = new
        l["id"] = new
        l["district"] = dmap.get(l.get("district"), l.get("district"))

    def remap_loc(x):
        return lmap.get(x, x)

    dist_ids = {d["id"] for d in pack.get("districts", [])}
    loc_ids = {l["id"] for l in pack.get("locations", [])}

    # --- 地点字段兜底 ---
    starters = 0
    for l in pack.get("locations", []):
        l.setdefault("name", l["id"])
        l.setdefault("brief", l["name"])
        if l.get("district") not in dist_ids:
            l["district"] = next(iter(dist_ids))
            log.append(f"{l['id']} district 悬空，改挂 {l['district']}")
        if l.get("type") not in content.LOC_TYPES:
            log.append(f"{l['id']} type={l.get('type')} 非法，改 street")
            l["type"] = "street"
        look = l.get("look")
        if not isinstance(look, dict):
            look = {"default": str(look) if look else l["brief"]}
        look.setdefault("default", l["brief"])
        l["look"] = look
        # 听觉层：缺了不补（引擎有通用兜底），但结构要干净
        sounds = l.get("sounds")
        if isinstance(sounds, str) and sounds.strip():
            l["sounds"] = {"default": sounds.strip()}
        elif isinstance(sounds, dict):
            sd = {k: v for k, v in sounds.items() if isinstance(v, str) and v.strip()}
            if sd:
                sd.setdefault("default", next(iter(sd.values())))
                l["sounds"] = sd
            else:
                l.pop("sounds", None)
        elif "sounds" in l:
            l.pop("sounds", None)
        # 入乡随俗：要么是完整的一段，要么干脆没有
        join = l.get("join")
        if isinstance(join, dict) and join.get("text"):
            join.pop("mood", None)
            l["join"] = join
        elif "join" in l:
            log.append(f"{l['id']} join 结构不完整，移除")
            l.pop("join", None)
        # 重访的承认 / 熟地的完成感
        for key in ("revisit", "mastered"):
            v = l.get(key)
            if isinstance(v, str) and v.strip():
                l[key] = v.strip()
            elif key in l:
                l.pop(key, None)
        l["fee"] = max(0, _int(l.get("fee"), 0))
        hours = l.get("hours")
        if isinstance(hours, list) and len(hours) == 2:
            l["hours"] = [max(0, _int(hours[0])), min(9, _int(hours[1], 9))]
        else:
            l["hours"] = None
        exs = []
        for ex in l.get("explore", []) or []:
            if isinstance(ex, dict) and ex.get("text"):
                rv = ex.get("reveal")
                if isinstance(rv, dict) and rv.get("loc"):
                    rv["loc"] = remap_loc(rv["loc"])
                    if rv["loc"] not in loc_ids:
                        log.append(f"{l['id']} explore reveal 悬空，移除")
                        ex.pop("reveal", None)
                exs.append(ex)
        l["explore"] = exs
        goods = []
        for j, g in enumerate(l.get("shop", []) or []):
            if isinstance(g, dict) and g.get("name"):
                g["id"] = _fix_id(g.get("id"), f"{l['id']}_g{j}")
                g["price"] = max(1, _int(g.get("price"), 1))
                g.setdefault("tags", [])
                goods.append(g)
        l["shop"] = goods
        if l.get("starter"):
            starters += 1
        rv = l.get("reveal_via")
        if isinstance(rv, dict):
            rv["at"] = remap_loc(rv.get("at"))

    if starters < 3:
        for l in pack.get("locations", [])[:4]:
            l["starter"] = True
        log.append("starter 不足 3，强制前几个地点为 starter")

    start = lmap.get(meta.get("start_loc"), meta.get("start_loc"))
    starter_ids = [l["id"] for l in pack.get("locations", []) if l.get("starter")]
    if start not in starter_ids:
        start = starter_ids[0]
        log.append(f"start_loc 无效，改为 {start}")
    meta["start_loc"] = start

    # --- meta 数值 ---
    for k, dv in (("hotel_rate", 100), ("transit_fare", 5), ("default_budget", 3000),
                  ("default_days", 5), ("train_cost_hint", 200),
                  ("flight_cost_hint", 2000), ("postcard_price", 30)):
        meta[k] = _int(meta.get(k), dv)
    meta["default_days"] = min(14, max(3, meta["default_days"]))
    meta["postcard_price"] = max(1, meta["postcard_price"])
    flavor = meta.get("postcard_flavor")
    if isinstance(flavor, list):
        flavor = [s.strip() for s in flavor if isinstance(s, str) and s.strip()]
        if flavor:
            meta["postcard_flavor"] = flavor
        else:
            meta.pop("postcard_flavor", None)
    elif isinstance(flavor, str) and flavor.strip():
        meta["postcard_flavor"] = flavor.strip()
    else:
        meta.pop("postcard_flavor", None)
    try:
        meta["cny_rate"] = float(meta.get("cny_rate", 1)) or 1
    except (TypeError, ValueError):
        meta["cny_rate"] = 1
    weather = meta.get("weather")
    if not isinstance(weather, dict) or not weather:
        weather = {"default": [["晴", 3], ["多云", 2], ["小雨", 1]]}
        log.append("weather 缺失，使用通用模式")
    if "default" not in weather:
        weather["default"] = next(iter(weather.values()))
    meta["weather"] = weather
    meta.setdefault("currency_symbol", "$")
    meta.setdefault("currency", "当地货币")

    # --- npc ---
    npcs = []
    for i, n in enumerate(pack.get("npcs", []) or []):
        if not isinstance(n, dict) or not n.get("name"):
            continue
        n["id"] = _fix_id(n.get("id"), f"npc{i}")
        n["loc"] = remap_loc(n.get("loc"))
        if n["loc"] not in loc_ids:
            log.append(f"npc {n['id']} loc 悬空，弃用")
            continue
        n.setdefault("persona", n["name"])
        n.setdefault("meet", f"你在这里遇到了{n['name']}。")
        recall = n.get("recall")
        if isinstance(recall, str) and recall.strip():
            n["recall"] = recall.strip()
        elif "recall" in n:
            n.pop("recall", None)        # 引擎有通用的「认人」兜底
        slots = n.get("slots")
        if isinstance(slots, list) and len(slots) == 2:
            n["slots"] = [max(0, _int(slots[0])), min(9, _int(slots[1], 9))]
        else:
            n["slots"] = None
        topics = []
        for tp in n.get("topics", []) or []:
            if isinstance(tp, dict) and tp.get("text"):
                rv = tp.get("reveal")
                if isinstance(rv, dict) and rv.get("loc"):
                    rv["loc"] = remap_loc(rv["loc"])
                    if rv["loc"] not in loc_ids:
                        tp.pop("reveal", None)
                topics.append(tp)
        n["topics"] = topics
        st = n.get("story")
        if isinstance(st, dict) and st.get("text"):
            st.setdefault("title", f"{n['name']}的故事")
            st["after_talks"] = min(4, max(2, _int(st.get("after_talks"), 2)))
            # 回声：必须是故事原文里的一句，对不上就当没写（旅行报告会引用它）
            echo = st.get("echo")
            if isinstance(echo, str) and echo.strip() and echo.strip() in st["text"]:
                st["echo"] = echo.strip()
            elif "echo" in st:
                log.append(f"npc {n['id']} 的 story.echo 不是原文，移除")
                st.pop("echo", None)
            rv = st.get("reveal")
            if isinstance(rv, dict) and rv.get("loc"):
                rv["loc"] = remap_loc(rv["loc"])
                if rv["loc"] not in loc_ids:
                    st.pop("reveal", None)
        else:
            n.pop("story", None)
        npcs.append(n)
    pack["npcs"] = npcs

    # --- dishes ---
    dishes = []
    for i, d in enumerate(pack.get("dishes", []) or []):
        if not isinstance(d, dict) or not d.get("name"):
            continue
        d["id"] = _fix_id(d.get("id"), f"dish{i}")
        d["price"] = max(1, _int(d.get("price"), 1))
        d["locs"] = [remap_loc(x) for x in (d.get("locs") or []) if remap_loc(x) in loc_ids]
        if not d["locs"]:
            markets = [l["id"] for l in pack["locations"]
                       if l["type"] in ("market", "street", "nightlife")]
            if markets:
                d["locs"] = [markets[0]]
                log.append(f"dish {d['id']} locs 悬空，挂到 {markets[0]}")
            else:
                continue
        d.setdefault("text", d["name"])
        d["energy"] = _int(d.get("energy"), 20)
        d.setdefault("tags", [])
        dishes.append(d)
    pack["dishes"] = dishes
    dish_ids = {d["id"] for d in dishes}

    # --- events ---
    events = []
    for i, e in enumerate(pack.get("events", []) or []):
        if not isinstance(e, dict) or not e.get("text"):
            continue
        e["id"] = _fix_id(e.get("id"), f"ev{i}")
        try:
            e["chance"] = min(0.35, max(0.03, float(e.get("chance", 0.15))))
        except (TypeError, ValueError):
            e["chance"] = 0.15
        if e.get("loc"):
            e["loc"] = remap_loc(e["loc"])
            if e["loc"] not in loc_ids:
                e.pop("loc", None)
        if e.get("district") and e["district"] not in dist_ids:
            e.pop("district", None)
        slots = e.get("slots")
        if isinstance(slots, list) and len(slots) == 2:
            e["slots"] = [max(0, _int(slots[0])), min(9, _int(slots[1], 9))]
        elif slots is not None:
            e.pop("slots", None)
        rv = e.get("reveal")
        if isinstance(rv, dict) and rv.get("loc"):
            rv["loc"] = remap_loc(rv["loc"])
            if rv["loc"] not in loc_ids:
                e.pop("reveal", None)
        events.append(e)
    pack["events"] = events

    # --- wander（漫步小景：无名的日常偶遇） ---
    wander = []
    for w in pack.get("wander", []) or []:
        if not isinstance(w, dict) or not w.get("text"):
            continue
        if w.get("district"):
            w["district"] = dmap.get(w["district"], w["district"])
            if w["district"] not in dist_ids:
                log.append("wander 片区悬空，改为不限片区")
                w.pop("district", None)
        if "slot" in w and w.get("slot") not in SLOTS:
            w.pop("slot", None)
        if "weather" in w and not isinstance(w.get("weather"), str):
            w.pop("weather", None)
        wander.append(w)
    pack["wander"] = wander

    # --- wishes（丢掉引用悬空的，不足则用通用心愿补齐） ---
    join_locs = {l["id"] for l in pack.get("locations", []) if l.get("join")}
    wishes = []
    for i, w in enumerate(pack.get("wishes", []) or []):
        if not isinstance(w, dict) or not w.get("text"):
            continue
        w["id"] = _fix_id(w.get("id"), f"w{i}")
        chk = w.get("check")
        if not isinstance(chk, dict):
            continue
        if chk.get("type") not in {"look", "visit", "eat", "story", "photo",
                                   "buy", "gem", "discovery", "explore",
                                   "npc", "rest", "listen", "join",
                                   "wander", "postcard"}:
            log.append(f"wish {w['id']} 判定类型非法，弃用")
            continue
        if chk.get("loc"):
            chk["loc"] = remap_loc(chk["loc"])
            if chk["loc"] not in loc_ids:
                log.append(f"wish {w['id']} loc 悬空，弃用")
                continue
            # join 型指向没写 join 的地点就永远做不成，放宽成「随便哪儿」
            if chk["type"] == "join" and chk["loc"] not in join_locs:
                log.append(f"wish {w['id']} join 指向没有入乡随俗的地点，改为不限地点")
                chk.pop("loc", None)
        if chk["type"] == "join" and not join_locs:
            log.append(f"wish {w['id']} 无处可入乡随俗，弃用")
            continue
        if chk.get("dish") and chk["dish"] not in dish_ids:
            log.append(f"wish {w['id']} dish 悬空，弃用")
            continue
        wishes.append(w)
    GENERIC_WISHES = [
        {"id": "any_story", "text": "听到一个只有本地人才讲得出的故事",
         "check": {"type": "story", "count": 1}},
        {"id": "any_gem", "text": "找到一处地图上没有的去处",
         "check": {"type": "gem", "count": 1}},
        {"id": "any_gift", "text": "挑一份想送出去的礼物",
         "check": {"type": "buy", "tag": "gift"}},
        {"id": "rain_walk", "text": "在雨里走一段路，不打伞也行",
         "check": {"type": "look", "weather": "雨"}},
        {"id": "dusk_photo", "text": "在黄昏拍下一张舍不得删的照片",
         "check": {"type": "photo", "slot": "黄昏"}},
        {"id": "night_out", "text": "夜里不早回，看看这座城醒着的另一面",
         "check": {"type": "look", "slot": "夜晚"}},
        {"id": "close_eyes", "text": "找个地方闭上眼睛，只用耳朵待一会儿",
         "check": {"type": "listen"}},
        {"id": "send_card", "text": "给一个想念的人寄张明信片",
         "check": {"type": "postcard"}},
    ]
    have = {w["id"] for w in wishes}
    for g in GENERIC_WISHES:
        if len(wishes) >= 7:
            break
        if g["id"] not in have:
            wishes.append(g)
            log.append(f"补通用心愿 {g['id']}")
    pack["wishes"] = wishes

    # --- 搭子时刻 ---
    moments = pack.get("companion_moments") or {}
    clean = {}
    for cid, arr in moments.items() if isinstance(moments, dict) else []:
        keep = []
        for m in arr or []:
            if isinstance(m, dict) and m.get("text"):
                m["loc"] = remap_loc(m.get("loc"))
                if m["loc"] in loc_ids:
                    keep.append(m)
        if keep:
            clean[cid] = keep
    pack["companion_moments"] = clean

    # --- trivia ---
    pack["trivia"] = [str(t) for t in (pack.get("trivia") or []) if t][:12]

    ensure_reveals(pack, log)
    for l in pack.get("locations", []):
        l.pop("reveal_via", None)
    return log


def ensure_reveals(pack: dict, log: list) -> None:
    """保证每个隐藏地点都有至少一条通路（explore/npc/事件里的 reveal）。"""
    hidden = [l for l in pack["locations"] if not l.get("starter")]
    if not hidden:
        return
    revealed = set()
    for l in pack["locations"]:
        for ex in l.get("explore", []):
            rv = ex.get("reveal") or {}
            if rv.get("loc"):
                revealed.add(rv["loc"])
    for n in pack.get("npcs", []):
        for tp in n.get("topics", []):
            rv = tp.get("reveal") or {}
            if rv.get("loc"):
                revealed.add(rv["loc"])
        st = n.get("story") or {}
        rv = st.get("reveal") or {}
        if rv.get("loc"):
            revealed.add(rv["loc"])
    for e in pack.get("events", []):
        rv = e.get("reveal") or {}
        if rv.get("loc"):
            revealed.add(rv["loc"])

    for h in hidden:
        if h["id"] in revealed:
            continue
        donor = None
        rv_via = h.get("reveal_via") or {}
        cand = [l for l in pack["locations"]
                if l.get("starter") and l["id"] == rv_via.get("at")]
        if not cand:
            cand = [l for l in pack["locations"]
                    if l.get("starter") and l["district"] == h["district"]
                    and l.get("explore")]
        if not cand:
            cand = [l for l in pack["locations"] if l.get("starter")]
        donor = cand[0]
        layers = donor.setdefault("explore", [])
        tail = (f"临走前和店家闲聊了两句，对方压低声音向你提起一个去处"
                f"——「{h['name']}」。你把它记在了地图边上。")
        if layers:
            layers[-1]["text"] = layers[-1]["text"].rstrip() + tail
            layers[-1].setdefault("reveal", {})["loc"] = h["id"]
        else:
            layers.append({"text": tail, "reveal": {"loc": h["id"]}})
        log.append(f"隐藏点 {h['id']} 无通路，补挂在 {donor['id']} 的探索里")
        revealed.add(h["id"])


# ---------------------------------------------------------------- 总装

def build_city(city_input: str, quiet: bool = False, force: bool = False) -> str:
    """生成一座城市的内容包，返回 slug。已存在且未 force 时直接复用。"""
    client = DeepSeek()
    t0 = time.time()

    _say(quiet, f"◐ 定位城市「{city_input}」…")
    meta = gen_meta(client, city_input)
    slug = slugify(meta.get("city_en") or city_input)
    meta["slug"] = slug
    meta["lang"] = "zh"
    out_dir = CONTENT_DIR / slug
    if (out_dir / "pack.json").exists() and not force:
        _say(quiet, f"✓ 「{meta['city']}」的内容包已存在（{slug}），直接使用。")
        return slug

    _say(quiet, f"◐ 调研中：{meta['city']}（{meta['country']}）——历史、街巷、吃食、掌故…")
    research = build_research(client, meta["city"], meta.get("city_en", ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "research.md").write_text(
        f"# {meta['city']} · 调研报告\n\n{research}", encoding="utf-8")

    _say(quiet, "◐ 规划城区与地点名录…")
    skeleton = gen_skeleton(client, meta, research)
    districts = skeleton.get("districts", [])
    meta["start_loc"] = skeleton.get("start_loc", "")

    detailed = {}
    for d in districts:
        _say(quiet, f"◐ 细写片区「{d.get('name', d.get('id'))}」…")
        det = gen_details(client, meta, research, skeleton, d)
        for l in det.get("locations", []):
            if isinstance(l, dict) and l.get("id"):
                detailed[l["id"]] = l

    locations = []
    for l in skeleton.get("locations", []):
        full = dict(l)
        extra = detailed.get(l.get("id"), {})
        for k, v in extra.items():
            if k != "id":
                full[k] = v
        locations.append(full)

    _say(quiet, "◐ 写这座城会遇到的人（与他们压箱底的故事）…")
    npcs = gen_npcs(client, meta, research, skeleton).get("npcs", [])

    _say(quiet, "◐ 安排吃食、街头事件、漫步小景、心愿池与小知识…")
    join_locs = [l["id"] for l in locations if isinstance(l.get("join"), dict)]
    extras = gen_extras(client, meta, research, skeleton, join_locs=join_locs)

    _say(quiet, "◐ 给三位旅游搭子写小剧场…")
    try:
        moments = gen_moments(client, meta, research, skeleton).get("moments", {})
    except Exception:
        moments = {}
        _say(quiet, "  （搭子小剧场生成失败，跳过——不影响游玩）")

    pack = {
        "schema": 1,
        "meta": meta,
        "districts": districts,
        "locations": locations,
        "npcs": npcs,
        "dishes": extras.get("dishes", []),
        "events": extras.get("events", []),
        "wander": extras.get("wander", []),
        "wishes": extras.get("wishes", []),
        "trivia": extras.get("trivia", []),
        "companion_moments": moments,
    }

    fix_log = sanitize_pack(pack)
    errs = content.validate_pack(pack)
    if errs:
        write_json(out_dir / "pack.debug.json", pack)
        raise LLMError("生成的内容包未通过校验：\n- " + "\n- ".join(errs[:10])
                       + f"\n（半成品已存到 {out_dir / 'pack.debug.json'}）")

    write_json(out_dir / "pack.json", pack)
    info = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": round(time.time() - t0),
        "model": client.model,
        "tokens": dict(client.usage),
        "fixes": fix_log,
    }
    write_json(out_dir / "build_info.json", info)
    _say(quiet, f"✓ 「{meta['city']}」建好了：{len(locations)} 处地点、{len(npcs)} 位人物、"
                f"{len(pack['dishes'])} 样吃食、{len(pack['wander'])} 处漫步小景。"
                f"耗时 {info['seconds']}s，"
                f"tokens {client.usage['prompt_tokens']}+{client.usage['completion_tokens']}。")
    return slug
