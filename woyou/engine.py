# -*- coding: utf-8 -*-
"""卧游 · 游戏引擎。

定稿设计原则（对 AI 玩家即「游戏规则」，对代码即「结算次序」）：
- 全部文案在建城时写死进内容包，游玩零实时生成、零 API；
- 资源只有三样身体性的：钱、体力、时刻。没有心情条，没有过程分数——
  日记本身就是心境，回味值只在终局出现一次；
- 动词一小套，每个都有独立的感官寄存器，并且永远接得住；
- 世界对玩家有记忆：重访会被承认，本地人会认人，逛尽的地方给「熟地」的
  完成感而不是空状态；
- 每条命令返回：叙事段落 + 系统备注行 + 一行 STATE {...}；盲玩。
"""
import json
import re

from . import content, journal, score as scoring
from .state import (TripState, new_state, load_state, enter_city,
                    roll_weather)
from .util import (CONTENT_DIR, clamp, fmt_money, fuzzy_pick, read_json,
                   slot_of, stable_rng, SLOT_TURN)

# ---------------------------------------------------------------- 常量

VERB_ALIASES = {
    "help": ["help", "?", "帮助", "命令"],
    "status": ["status", "状态", "我"],
    "map": ["map", "地图"],
    "journal": ["journal", "日记", "游记"],
    "wishes": ["wishes", "心愿", "心愿单"],
    "wish": ["wish", "pick", "许愿", "挑心愿", "选心愿", "抄心愿"],
    "go": ["go", "goto", "move", "去", "前往", "走到"],
    "look": ["look", "看", "看看", "环顾", "观察"],
    "listen": ["listen", "听", "听听", "闭眼听"],
    "explore": ["explore", "逛", "逛逛", "探索", "走走"],
    "wander": ["wander", "漫步", "闲逛", "瞎逛", "晃悠"],
    "join": ["join", "入乡随俗", "照着做", "跟着做", "参与"],
    "talk": ["talk", "聊", "聊天", "搭话", "攀谈"],
    "eat": ["eat", "吃", "尝", "尝尝"],
    "photo": ["photo", "拍照", "拍", "摄影"],
    "buy": ["buy", "买", "购买"],
    "postcard": ["postcard", "明信片", "寄明信片"],
    "rest": ["rest", "休息", "歇", "歇脚", "坐坐"],
    "chat": ["chat", "闲聊", "搭子"],
    "sleep": ["sleep", "睡", "睡觉", "回旅舍", "回酒店"],
    "fly": ["fly", "train", "飞", "坐车", "换城", "启程"],
    "note": ["note", "memo", "记", "批注", "自语", "心声"],
    "end": ["end", "回程", "结束", "回家"],
    "export": ["export", "导出"],
    "share": ["share", "手帐", "分享"],
}
VERB_OF = {}
for verb, keys in VERB_ALIASES.items():
    for k in keys:
        VERB_OF[k] = verb

# 单字中文前缀命令：「去金阁寺」「吃汤豆腐」这种不带空格的写法
ZH_PREFIX = {"去": "go", "吃": "eat", "尝": "eat", "买": "buy", "听": "listen",
             "聊": "talk", "飞": "fly", "逛": "explore", "拍": "photo",
             "歇": "rest", "记": "note"}

SCENIC_TYPES = {"river", "park", "path", "viewpoint", "temple", "shrine"}
TIME_VERBS = {"go", "explore", "talk", "eat", "buy", "rest", "sleep", "fly",
              "join", "wander", "postcard"}

LOOK_AGAIN = [
    "光线和方才没什么不同，你又站了一会儿。",
    "你换了个角度看，景物还是那些景物，心境倒松了半分。",
    "没什么新的，可你并不急着走。",
]
LISTEN_AGAIN = [
    "还是那些声音，只是你听出了它们的次序。",
    "你又听了一会儿。声音没变，你的耳朵松下来了。",
]
# 重访的承认：同一地点、同一时辰、又一天——习惯正在形成
HABIT_LINES = [
    "你又来了。同一个时辰，同一个位置——这里开始认得你的影子了。",
    "不知不觉，这成了你的固定节目。熟了的地方，眼睛会自己安顿。",
    "你在老位置站定。旅行过半，有些地方已经不像景点，像约定。",
]
# 逛尽之地的完成感（若地点没写专属的 mastered 文本）
MASTERED_LINES = [
    "这里你已经走成了熟地——闭着眼能画出它的样子。从今往后，路过就是问候。",
    "没有新的角落了，但每个旧角落都认得你。把一个地方逛成熟地，也是旅行的一种完成。",
    "你信步走着，不再看路。对这里，你已经从客人变成了常来的人。",
]
NO_NPC = [
    "这会儿没遇上能搭话的人，只有些各自赶路的背影。",
    "你朝一位路人笑了笑，对方点头回礼，脚步没停。",
    "没人闲着。你听了一会儿别人的谈话声，像听一段不给字幕的电影。",
]
WANDER_GENERIC = [
    "你顺着一条没名字的小巷走了一段，两边的门牌越来越旧，走到头是一堵爬着藤的墙。原路折回时，觉得腿脚认识了这个街区。",
    "你跟着一只猫走了半条街，它在一扇木门前坐下，回头看你。你们在这里分了手。",
    "你什么也没找，也就什么都没错过。半个钟头，路过了四种炊烟的味道。",
]
JOIN_NONE = "你四下看了看，这会儿没什么可跟着本地人做的。看看就好，看也是做。"

STATE_PREFIX = "STATE "


# ---------------------------------------------------------------- 引擎

class Trip:
    def __init__(self, state: TripState, pack: dict, autosave: bool = True):
        self.state = state
        self.pack = pack
        self.autosave = autosave
        self.companions = self._load_companions()

    # ---------- 构造 ----------
    @classmethod
    def new(cls, slug: str, days=None, budget=None, seed=None, month=None,
            mate: str = "") -> "Trip":
        pack = content.load_pack(slug)
        st = new_state(pack, days=days, budget=budget, seed=seed,
                       month=month, mate=mate)
        trip = cls(st, pack)
        st.save()
        return trip

    @classmethod
    def load(cls, trip_id: str) -> "Trip":
        st = load_state(trip_id)
        pack = content.load_pack(st.slug)
        return cls(st, pack)

    @staticmethod
    def _load_companions() -> dict:
        path = CONTENT_DIR / "companions.json"
        if path.exists():
            return {c["id"]: c for c in read_json(path).get("companions", [])}
        return {}

    @property
    def mate(self):
        return self.companions.get(self.state.mate)

    # ---------- 开场白 ----------
    def opening(self) -> str:
        st, meta = self.state, self.pack["meta"]
        self._begin()
        self.emit(meta["intro"])
        if self.mate:
            self.emit(self.mate["pick_line"])
        menu = self._wish_menu()
        lines = "\n".join(f"  {i + 1}. {w['text']}" for i, w in enumerate(menu))
        self.emit(f"手帐的第一页印着这座城的「心愿清单」——来过的旅人们常许的愿。"
                  f"想抄几条当这次旅行的小方向，就 wish <编号>（最多 {st.wish_cap} 条，"
                  f"随时可再添）；一条不抄、全凭脚步，也完全可以。\n{lines}")
        self.emit(f"今天天气{self._weather()}。行李放下了，门就在那儿。"
                  f"（输入 help 看你能做什么；look 四下看看是个好开头）")
        return self._flush("start")

    def _wish_menu(self) -> list:
        """当前城市还没被抄走的心愿选项（顺序稳定）。"""
        picked = {w["id"] for w in self.state.wishes}
        return [w for w in self.pack.get("wishes", [])
                if f"{self.state.slug}:{w['id']}" not in picked]

    # ---------- 主入口 ----------
    def cmd(self, raw: str) -> str:
        st = self.state
        raw = (raw or "").strip()
        self._begin()
        if not raw:
            self.emit("……什么也没说。输入 help 看看能做什么。")
            return self._flush(raw)

        verb, arg = self._parse(raw)
        if verb is None:
            self.emit(f"没明白「{raw}」。输入 help 看命令——每一个都接得住。")
            return self._flush(raw)

        if st.ended and verb not in {"status", "journal", "wishes", "map",
                                     "export", "help", "note", "share"}:
            self.emit("旅程已经结束了。可以 journal 翻翻日记、export 导出游记，"
                      "或者开一段新的旅程。")
            return self._flush(raw)

        # 夜深禁行（sleep 与只读命令除外）
        if (st.t >= 10 and verb in TIME_VERBS and verb != "sleep"):
            self.emit("夜太深了，眼皮在打架。今天到这儿吧——sleep 回旅舍。")
            return self._flush(raw)

        t_before = st.t
        handler = getattr(self, f"_cmd_{verb}")
        handler(arg)

        # 时段流转提示
        if not st.ended and st.t != t_before and st.t <= 9:
            s0, s1 = slot_of(t_before), slot_of(st.t)
            if s0 != s1 and s1 in SLOT_TURN:
                self.emit(SLOT_TURN[s1])
        if not st.ended and st.t >= 10 and verb != "sleep":
            self.emit("夜色深了，街上行人渐稀。该回旅舍了（sleep）。")

        # 事件与心愿
        if not st.ended:
            if st.t > t_before and verb != "sleep":
                self._maybe_event()
            self._check_wishes()
        return self._flush(raw)

    # ---------- 输出装配 ----------
    def _begin(self):
        self.out, self.notes, self.marks, self.records = [], [], [], []
        self._player_note = None
        self._mate_spoke = False

    def emit(self, text: str):
        if text:
            self.out.append(text.strip())

    def note(self, text: str):
        self.notes.append(text)

    def mark(self, m: str):
        self.marks.append(m)

    def record(self, **kw):
        self.records.append(kw)

    def _flush(self, raw_cmd: str) -> str:
        st = self.state
        parts = list(self.out)
        if self.notes:
            parts.append("\n".join(self.notes))
        parts.append(self._state_line())
        text = "\n\n".join(parts)
        entry = {"day": st.day, "t": min(st.t, 9), "cmd": raw_cmd, "out": text}
        if self._player_note:
            entry["note"] = self._player_note
        st.log.append(entry)
        st.log = st.log[-60:]
        if self.autosave:
            st.save()
        return text

    def _weather(self) -> str:
        return roll_weather(self.state, self.pack, self.state.day)

    def _state_line(self) -> str:
        st, meta = self.state, self.pack["meta"]
        loc = self.pack["_loc"].get(st.loc, {})
        box = st.box()
        vis = (box.get("visited") or {}).get(st.loc, {})
        layers = loc.get("explore", [])
        t_clamped = min(st.t, 9)
        d = {
            "day": st.day, "days": st.days_total, "t": st.t,
            "slot": slot_of(t_clamped), "city": meta["city"],
            "loc": loc.get("name", st.loc),
            "money": st.money, "cur": meta["currency_symbol"],
            "energy": st.energy,
            "weather": self._weather(),
            "journal": len(st.journal), "stories": len(st.stories_heard),
            "wishes": f"{sum(1 for w in st.wishes if w['done'])}/{len(st.wishes)}",
            "gems": st.gems,
            "mate": self.mate["name"] if self.mate else None,
            "new": self.marks, "end": st.ended,
            "locked": st.t >= 10,
            "here": {
                "type": loc.get("type", ""),
                "open": content.loc_open(loc, t_clamped),
                "has_food": bool(content.dishes_at(self.pack, st.loc)),
                "has_shop": bool(loc.get("shop")),
                "npc": len(content.npcs_at(self.pack, st.loc, t_clamped)),
                "explore_left": max(0, len(layers) - vis.get("explored", 0)),
            },
        }
        return STATE_PREFIX + json.dumps(d, ensure_ascii=False)

    # ---------- 解析 ----------
    def _parse(self, raw: str):
        parts = raw.split(None, 1)
        head = parts[0].casefold()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if head in VERB_OF:
            return VERB_OF[head], arg
        if raw[0] in ZH_PREFIX and len(raw) > 1:
            return ZH_PREFIX[raw[0]], raw[1:].strip()
        return None, raw

    # ---------- 资源结算 ----------
    def _spend_t(self, n: int):
        self.state.t += n

    def _spend_energy(self, n: int):
        self.state.energy = clamp(self.state.energy - n, 0, 100)

    def _gain_energy(self, n: int):
        self.state.energy = clamp(self.state.energy + n, 0, 100)

    def _pay(self, amount: int, why: str) -> bool:
        st, meta = self.state, self.pack["meta"]
        if amount <= 0:
            return True
        if st.money < amount:
            return False
        st.money -= amount
        rate = meta.get("cny_rate", 1) or 1
        st.spent += round(amount / rate)
        st.spent_local = getattr(st, "spent_local", 0) + amount
        self.note(f"💴 -{fmt_money(meta['currency_symbol'], amount)}（{why}）")
        return True

    def _journal(self, etype, title, text, loc_name=""):
        meta = self.pack["meta"]
        journal.add_entry(self.state, etype, title, text,
                          loc_name=loc_name, city=meta["city"])
        self.note(f"✎ 日记·{etype}「{title}」")

    # ---------- 只读命令 ----------
    def _cmd_help(self, arg):
        self.emit(
            "上路（会花时刻/体力/钱）——\n"
            "  go <地点>     去某处（跨区坐车要车费）    explore     深入逛逛，层层有发现\n"
            "  wander        不带目的地漫步，撞见小景    join        照着本地人的样子做一次\n"
            "  talk [某人]   和本地人搭话，聊出故事      eat [吃食]  吃点东西，恢复体力\n"
            "  buy [物件]    买点纪念                    postcard [一句话]  寄张明信片\n"
            "  rest          歇脚回体力                  sleep       回旅舍睡觉，进入第二天\n"
            "  fly <城市>    换一座城（花钱花时间）\n"
            "随时可做（免费）——\n"
            "  look          四下看看（时段天气不同景）  listen      闭上眼，听这个地方\n"
            "  photo [描述]  把眼前拍进日记              chat        和旅游搭子说说话\n"
            "  map / status / journal / wishes\n"
            "  wish <编号或关键词>  从心愿清单挑几条抄进手帐（可选，随时可添）\n"
            "  note <随想>   完全可选的自语，零消耗零影响，只在手帐边留痕\n"
            "  end trip      提前结束旅程回家\n"
            "中文也行：去金阁寺、吃汤豆腐、听（闭眼听一会儿）。\n"
            "每天 10 刻，用完必须 sleep。没有任务，没有必做之事——心愿单只是心愿。\n"
            "凭输出和 STATE 行做决定，怎么旅行，全看你。\n"
            "（大致体力：走路 6-15、逛/聊/跟/吃 4-12、漫步 5、歇脚回 +22。跨区更费腿。）\n"
            "（STATE 速查：t=当日第几刻(共10)、locked=该sleep了、"
            "here=当前地点可做的事、gems=深巷发现累计。）")

    def _cmd_status(self, arg):
        st, meta = self.state, self.pack["meta"]
        loc = self.pack["_loc"].get(st.loc, {})
        lines = [
            f"第 {st.day}/{st.days_total} 天 · {slot_of(st.t)} · {self._weather()}"
            f" · {meta['city']}（{meta['country']}）",
            f"身在：{loc.get('name', '?')}。钱包 {fmt_money(meta['currency_symbol'], st.money)}"
            f" · 体力 {st.energy}",
        ]
        if self.mate:
            lines.append(f"同行：{self.mate['name']}（{self.mate['tag']}）")
        else:
            lines.append("独行。")
        done = sum(1 for w in st.wishes if w["done"])
        lines.append(f"日记 {len(st.journal)} 条 · 故事 {len(st.stories_heard)} 个"
                     f" · 心愿 {done}/{len(st.wishes)} · 隐藏发现 {st.gems} 处")
        if st.energy < 25:
            lines.append("腿有点沉了——吃点东西或 rest 歇歇吧。")
        self.emit("\n".join(lines))

    def _cmd_map(self, arg):
        st, meta = self.state, self.pack["meta"]
        box = st.box()
        lines = [f"【{meta['city']}·手绘小地图】（跨区乘车 "
                 f"{fmt_money(meta['currency_symbol'], meta.get('transit_fare', 0))}/次）"]
        for dist in self.pack["districts"]:
            locs = [self.pack["_loc"][lid] for lid in box["known_locs"]
                    if self.pack["_loc"].get(lid, {}).get("district") == dist["id"]]
            if not locs:
                continue
            lines.append(f"— {dist['name']} —")
            for l in locs:
                here = "📍" if l["id"] == st.loc else "  "
                tags = [content.TYPE_LABEL.get(l["type"], l["type"])]
                vis = box["visited"].get(l["id"], {})
                if l.get("explore") and vis.get("explored", 0) >= len(l["explore"]):
                    tags.append("熟")
                if l.get("fee") and l["id"] not in box["fees_paid"]:
                    tags.append(f"门票{fmt_money(meta['currency_symbol'], l['fee'])}")
                if not content.loc_open(l, min(st.t, 9)):
                    tags.append(f"现在不开（{content.hours_text(l)}）")
                lines.append(f"{here} {l['name']}｜{'·'.join(tags)}｜{l['brief']}")
        names = st.route_names or st.route
        lines.append(f"（此行足迹：{'→'.join(names)}；fly <城市名> 可以去别的城）")
        self.emit("\n".join(lines))

    def _cmd_journal(self, arg):
        self.emit(journal.journal_brief(self.state))

    def _cmd_wishes(self, arg):
        st = self.state
        lines = []
        if st.wishes:
            lines.append(f"手帐里抄下的心愿（{len(st.wishes)}/{st.wish_cap}）——")
            for w in st.wishes:
                box = "✅" if w["done"] else "◻️"
                lines.append(f" {box} {w['text']}")
        else:
            lines.append(f"手帐的心愿页还空着（容量 {st.wish_cap} 条）。"
                         f"抄不抄都行，旅行不欠任何清单。")
        menu = self._wish_menu()
        if menu and len(st.wishes) < st.wish_cap and not st.ended:
            lines.append(f"这座城的心愿清单还剩这些可抄（wish <编号>）——")
            for i, w in enumerate(menu):
                lines.append(f"  {i + 1}. {w['text']}")
        self.emit("\n".join(lines))

    def _cmd_wish(self, arg):
        st = self.state
        if not arg:
            self._cmd_wishes("")
            return
        menu = self._wish_menu()
        if not menu:
            self.emit("这座城的心愿清单都抄完了。")
            return
        tokens = arg.replace("，", " ").replace(",", " ").split()
        chosen, misses = [], []
        for tok in tokens:
            w = None
            if tok.isdigit():
                idx = int(tok) - 1
                if 0 <= idx < len(menu):
                    w = menu[idx]
            else:
                wid = fuzzy_pick(tok, [(x["id"], [x["id"], x["text"]]) for x in menu])
                if wid:
                    w = next(x for x in menu if x["id"] == wid)
            if w is None:
                misses.append(tok)
            elif w not in chosen:
                chosen.append(w)
        added = []
        for w in chosen:
            if len(st.wishes) >= st.wish_cap:
                self.note(f"（手帐的心愿页写满了，{st.wish_cap} 条是上限）")
                break
            st.wishes.append({"id": f"{st.slug}:{w['id']}", "city": st.slug,
                              "wid": w["id"], "text": w["text"],
                              "done": False, "day": None})
            added.append(w)
            self.note(f"🎐 抄下心愿：「{w['text']}」")
        if added:
            self.emit("你把笔帽咬开，一条条抄进手帐。字落在纸上，"
                      "旅行就有了几个小小的朝向——路怎么走，还是你的事。")
            remaining = self._wish_menu()
            if remaining and len(st.wishes) < st.wish_cap:
                lines = [f"清单上还剩（wish <编号或关键词>）——"]
                for i, w in enumerate(remaining):
                    lines.append(f"  {i + 1}. {w['text']}")
                self.emit("\n".join(lines))
        if misses:
            self.emit(f"没对上号的：{'、'.join(misses)}（wishes 看编号或关键词）")
        if not added and not misses:
            self.emit("一条也没抄。也好，轻装上阵。")

    def _cmd_export(self, arg):
        path = journal.export_markdown(self.state, self.pack)
        self.emit(f"游记已写好：{path}")

    def _cmd_share(self, arg):
        st = self.state
        if not st.ended:
            self.emit("旅程还没结束呢。等旅行结束之后，再来做手帐吧。")
            return
        from . import share
        path = share.save_share_html(st, self.pack, ai_note=arg)
        self.emit(f"手帐已经做好了：{path}")

    def _cmd_note(self, arg):
        """旅人自语：纯可选的旁注。零耗时、零数值、不计分，只留痕。"""
        if not arg:
            self.emit("想留一句什么就写在后面（note <随想>）。写不写都行，"
                      "不影响任何数值，只会留在手帐边上和游记的旁注里。")
            return
        self._player_note = arg
        self.emit("✎ 你在手帐边上记了一笔。")

    # ---------- 移动 ----------
    def _cmd_go(self, arg):
        st, meta = self.state, self.pack["meta"]
        box = st.box()
        if not arg:
            self.emit("去哪儿？（map 看看已知的地方）")
            return
        lid = content.resolve_loc(self.pack, arg, box["known_locs"])
        if lid is None:
            all_ids = [l["id"] for l in self.pack["locations"]]
            hidden = content.resolve_loc(self.pack, arg, all_ids)
            if hidden:
                self.emit("你好像听人提过这个名字，但地图上还没有它的位置。"
                          "多逛逛、多跟本地人聊聊，兴许有人肯指路。")
            else:
                self.emit(f"打听了一圈，没人知道「{arg}」在哪。（map 看看已知的地方）")
            return
        if lid == st.loc:
            self.emit("你就站在这儿。look 四下看看吧。")
            return
        cur = self.pack["_loc"][st.loc]
        dst = self.pack["_loc"][lid]
        cross = cur["district"] != dst["district"]
        t_cost, e_cost = (2, 10) if cross else (1, 6)
        fare = meta.get("transit_fare", 0) if cross else 0
        walked = False
        if fare and st.money < fare:
            t_cost, e_cost, fare, walked = 3, 15, 0, True
        if st.energy < e_cost:
            self.emit("腿实在抬不动了。先 eat 吃点东西或 rest 歇一歇。")
            return
        if st.t + t_cost > 10:
            self.emit("今天太晚了，赶过去也是吃闭门羹。sleep 睡吧，明天赶早。")
            return
        if fare:
            self._pay(fare, "车费")
        self._spend_t(t_cost)
        self._spend_energy(e_cost)

        gate_note = ""
        fee = dst.get("fee", 0)
        if fee and lid not in box["fees_paid"]:
            if content.loc_open(dst, min(st.t, 9)):
                if not self._pay(fee, f"{dst['name']}门票"):
                    self.emit(f"到了{dst['name']}门口，门票要"
                              f"{fmt_money(meta['currency_symbol'], fee)}，兜里不够。"
                              f"你在门外站了站，转身走了。")
                    return
                box["fees_paid"].append(lid)
            else:
                gate_note = "（这会儿不开门，只能在外头看看。）"

        st.loc = lid
        move_word = "你慢慢走了过去" if walked else ("你坐车穿过半座城" if cross else "你溜达着过去")
        if walked:
            move_word += "（省下了车钱）"
        opened = content.loc_open(dst, min(st.t, 9))
        status = "" if opened else f"（现在不开放，开放时段：{content.hours_text(dst)}）"
        self.emit(f"{move_word}。\n📍 {dst['name']}｜{dst['brief']}{status}{gate_note}")

        dist = self.pack["_dist"][dst["district"]]
        seen_key = f"seen_dist:{st.slug}:{dist['id']}"
        if not st.flags.get(seen_key):
            st.flags[seen_key] = True
            if dist.get("intro"):
                self.emit(dist["intro"])
        self.record(kind="visit", loc=lid)

    # ---------- 观察 ----------
    CLOSED_LOOK = {
        "market": "卷帘门大半拉着，摊台空空地立在暗里。市场歇着的时候，这条巷子把嗓音也收走了。",
        "temple": "山门阖着，只有檐角的铃偶尔响一声。墙里的安静漫出来，比开门时更像一座寺。",
        "shrine": "社门未开，参道空无一人。石灯笼立在原地，替神明守着门。",
        "nightlife": "巷子还没醒。灯笼没点，木门紧闭，白天的先客只有几只斑鸠。要看它的真面目，得等天黑。",
        "cafe": "门上挂着「準備中」的小牌。玻璃里望进去，椅子都四脚朝天地睡在桌上。",
        "shop": "还没到开门的时辰。你贴着橱窗望了望里面，灯没开，货架在暗处影影绰绰。",
    }

    def _cmd_look(self, arg):
        st = self.state
        box = st.box()
        loc = self.pack["_loc"][st.loc]
        slot, weather = slot_of(st.t), self._weather()
        if not content.loc_open(loc, min(st.t, 9)):
            text = self.CLOSED_LOOK.get(loc["type"],
                    "这会儿不是它的时辰，你只能在外头看看轮廓。")
            self.emit(f"{loc['name']}还没开（{content.hours_text(loc)}）。{text}")
            self.record(kind="look", loc=st.loc, slot=slot, weather=weather)
            return
        vis = box["visited"].setdefault(st.loc, {"looked": [], "explored": 0, "photos": []})
        key = f"{slot}|{weather}"
        text = content.pick_text(loc.get("look", {}), slot, weather)
        if key in vis["looked"]:
            rng = stable_rng(st.seed, "lookagain", st.day, st.t, len(st.log))
            self.emit(text)
            self.emit(rng.choice(LOOK_AGAIN))
        else:
            vis["looked"].append(key)
            self.emit(text)
            self._ambient_trivia(loc)
        self._habit_note(loc, vis, slot)
        if not self._mate_moment():
            rainy = any(x in weather for x in ("雨", "雪"))
            self._mate_ambient(rainy and "rain" or None, loc["type"],
                               {"黄昏": "dusk", "夜晚": "night",
                                "清晨": "morning"}.get(slot))
        self.record(kind="look", loc=st.loc, slot=slot, weather=weather)

    def _habit_note(self, loc, vis, slot):
        """世界记忆①：同一地点、同一时辰、跨越不同的日子——习惯被承认。"""
        st = self.state
        days = vis.setdefault("slot_days", {}).setdefault(slot, [])
        if st.day not in days:
            days.append(st.day)
        if len(days) < 2 or st.flags.get(f"habit:{st.slug}:{st.loc}:{st.day}"):
            return
        st.flags[f"habit:{st.slug}:{st.loc}:{st.day}"] = True
        custom = loc.get("revisit")
        if custom and len(days) == 2:
            self.emit(custom)
        else:
            rng = stable_rng(st.seed, "habit", st.loc, len(days))
            self.emit(rng.choice(HABIT_LINES))

    def _cmd_listen(self, arg):
        """闭上眼听这个地方。免费，像 look 一样是纯感受。"""
        st = self.state
        box = st.box()
        loc = self.pack["_loc"][st.loc]
        slot, weather = slot_of(st.t), self._weather()
        sounds = loc.get("sounds")
        if not sounds:
            self.emit("你闭上眼听了一会儿。风声、人声、远处说不清的市声——"
                      "这个地方的声音没什么特别，特别的是你停下来听了。")
            self.record(kind="listen", loc=st.loc, slot=slot, weather=weather)
            return
        vis = box["visited"].setdefault(st.loc, {"looked": [], "explored": 0, "photos": []})
        heard = vis.setdefault("heard", [])
        key = f"{slot}|{weather}"
        text = content.pick_text(sounds, slot, weather)
        self.emit("你闭上眼。\n" + text)
        if key in heard:
            rng = stable_rng(st.seed, "listenagain", st.day, st.t)
            self.emit(rng.choice(LISTEN_AGAIN))
        else:
            heard.append(key)
        self._mate_ambient("listen", loc["type"])
        self.record(kind="listen", loc=st.loc, slot=slot, weather=weather)

    def _cmd_explore(self, arg):
        st = self.state
        box = st.box()
        loc = self.pack["_loc"][st.loc]
        if not content.loc_open(loc, min(st.t, 9)):
            self.emit(f"{loc['name']}这会儿不开（{content.hours_text(loc)}）。"
                      f"改天赶在开放时来吧。")
            return
        vis = box["visited"].setdefault(st.loc, {"looked": [], "explored": 0, "photos": []})
        layers = loc.get("explore", [])
        level = vis["explored"]
        deeper = level < len(layers)
        t_cost, e_cost = (2, 12) if deeper else (1, 6)
        if st.energy < e_cost:
            self.emit("有点走不动了。先吃点东西或 rest 歇歇，再来慢慢逛。")
            return
        if st.t + t_cost > 10:
            self.emit("天太晚了，逛也逛不出什么了。sleep 吧。")
            return
        self._spend_t(t_cost)
        self._spend_energy(e_cost)
        if not deeper:
            rng = stable_rng(st.seed, "mastered", st.day, st.t)
            self.emit(rng.choice(MASTERED_LINES[1:]))
            self.record(kind="explore", loc=st.loc, level=level)
            return

        layer = layers[level]
        vis["explored"] = level + 1
        self.emit(layer["text"])
        rv = layer.get("reveal") or {}
        if rv.get("loc"):
            self._reveal_loc(rv["loc"])
        if layer.get("gem") or rv.get("gem"):
            st.gems += 1
            title = layer.get("title", f"{loc['name']}的角落")
            self._journal("意外", title, layer["text"], loc["name"])
            self.mark("gem")
        if vis["explored"] >= len(layers):
            st.flags[f"mastered:{st.slug}:{st.loc}"] = True
            mastered_text = loc.get("mastered") or MASTERED_LINES[0]
            self.emit(mastered_text)
            self._journal("纪念", f"{loc['name']}被你逛成了熟地",
                          mastered_text, loc["name"])
        self.record(kind="explore", loc=st.loc, level=level + 1)

    def _reveal_loc(self, lid: str):
        box = self.state.box()
        if lid in box["known_locs"]:
            return
        box["known_locs"].append(lid)
        name = self.pack["_loc"][lid]["name"]
        self.note(f"◉ 新地点标进了地图：「{name}」")
        self.mark(f"loc:{lid}")

    def _cmd_wander(self, arg):
        """不带目的地漫步，撞见这座城写好的小景。"""
        st = self.state
        box = st.box()
        loc = self.pack["_loc"][st.loc]
        if st.t + 1 > 10:
            self.emit("夜太深，巷子都睡了。明天再漫步吧。")
            return
        if st.energy < 5:
            self.emit("腿已经在抗议了。先歇歇或吃点东西。")
            return
        self._spend_t(1)
        self._spend_energy(5)
        slot, weather = slot_of(st.t), self._weather()
        pool = []
        for i, w in enumerate(self.pack.get("wander", [])):
            if w.get("district") and w["district"] != loc["district"]:
                continue
            if w.get("slot") and w["slot"] != slot:
                continue
            if w.get("weather") and w["weather"] not in weather:
                continue
            pool.append((i, w))
        seen = box.setdefault("wander_seen", [])
        fresh = [(i, w) for i, w in pool if i not in seen]
        rng = stable_rng(st.seed, "wander", st.day, st.t, len(seen))
        if fresh:
            i, w = rng.choice(fresh)
            seen.append(i)
            self.emit(w["text"])
        elif pool:
            _, w = rng.choice(pool)
            self.emit(w["text"])
        else:
            self.emit(rng.choice(WANDER_GENERIC))
        self._mate_ambient("wander", loc["type"])
        self.record(kind="wander", loc=st.loc)

    def _cmd_join(self, arg):
        """入乡随俗：照着本地人的样子做一次这里的小事。"""
        st = self.state
        box = st.box()
        loc = self.pack["_loc"][st.loc]
        act = loc.get("join")
        if not act:
            self.emit(JOIN_NONE)
            return
        if not content.loc_open(loc, min(st.t, 9)):
            self.emit(f"{loc['name']}这会儿不开（{content.hours_text(loc)}），"
                      f"想跟着本地人做点什么，得等他们在的时候。")
            return
        if st.t + 1 > 10:
            self.emit("太晚了，本地人都归家了。明天再学。")
            return
        joined = box.setdefault("joined", [])
        self._spend_t(1)
        self._spend_energy(4)
        if st.loc in joined:
            self.emit("你又照着做了一遍，这回熟练多了——熟练到少了点第一次的笨拙的乐趣。")
        else:
            joined.append(st.loc)
            self.emit(act["text"])
            if act.get("journal"):
                title = act.get("title", f"在{loc['name']}入乡随俗")
                self._journal(act.get("journal", "意外"), title, act["text"], loc["name"])
        self.record(kind="join", loc=st.loc, loc_type=loc["type"])

    # ---------- 人 ----------
    def _cmd_talk(self, arg):
        st = self.state
        box = st.box()
        npcs = content.npcs_at(self.pack, st.loc, min(st.t, 9))
        if not npcs:
            rng = stable_rng(st.seed, "nonpc", st.day, st.t)
            self.emit(rng.choice(NO_NPC))
            return
        npc = None
        if arg:
            nid = fuzzy_pick(arg, [(n["id"], [n["id"], n["name"]]) for n in npcs])
            npc = self.pack["_npc"].get(nid) if nid else None
            if npc is None:
                self.emit(f"这附近没见到「{arg}」。眼下能搭上话的："
                          + "、".join(n["name"] for n in npcs))
                return
        else:
            fresh = [n for n in npcs if n["id"] not in box["met"]]
            npc = fresh[0] if fresh else npcs[0]

        if st.t + 1 > 10:
            self.emit("太晚了，人家也要归家了。")
            return
        self._spend_t(1)
        self._spend_energy(4)
        nid = npc["id"]
        count = box["talked"].get(nid, 0) + 1
        box["talked"][nid] = count

        if nid not in box["met"]:
            box["met"].append(nid)
            box.setdefault("talked_day", {})[nid] = st.day   # 初见也算「见过面」
            self.emit(npc["meet"])
            self._journal("人物", npc["name"], npc["meet"], self.pack["_loc"][st.loc]["name"])
        else:
            self._npc_recall(npc, box)
            topics = npc.get("topics", [])
            if topics:
                tp = topics[(count - 2) % len(topics)]
                self.emit(tp["text"])
                rv = tp.get("reveal") or {}
                if rv.get("loc"):
                    self._reveal_loc(rv["loc"])
            else:
                self.emit(f"{npc['name']}和你有一搭没一搭地聊着。")

        # 故事解锁：聊到火候，压箱底的东西才拿出来
        story = npc.get("story")
        if story and story["title"] not in st.stories_heard:
            need = int(story.get("after_talks", 2))
            if count == need - 1:
                hint = npc.get("story_hint",
                               f"{npc['name']}顿了顿，像是想起什么又咽了回去。")
                self.emit(hint)
            if count >= need:
                self.emit(story["text"])
                st.stories_heard.append(story["title"])
                self._journal("故事", story["title"], story["text"],
                              self.pack["_loc"][st.loc]["name"])
                self.mark(f"story:{story['title']}")
                rv = story.get("reveal") or {}
                if rv.get("loc"):
                    self._reveal_loc(rv["loc"])
                self.record(kind="story")
        self.record(kind="npc", id=nid)

    def _npc_recall(self, npc, box):
        """世界记忆②：本地人认人。隔天再见会打招呼，还记得你买过什么。"""
        st = self.state
        nid = npc["id"]
        last_day = box.setdefault("talked_day", {}).get(nid)
        box["talked_day"][nid] = st.day
        if last_day is None or last_day >= st.day:
            return
        if st.flags.get(f"recall:{st.slug}:{nid}"):
            # 已经认过一次人，之后隔天见面只给短问候
            self.emit(f"{npc['name']}朝你点点头，像对一个街坊。")
            return
        st.flags[f"recall:{st.slug}:{nid}"] = True
        greet = npc.get("recall") or (
            f"「又是你。」{npc['name']}认出了你，神色松了半分——"
            f"回头客和游客，在这条街上是两种待遇。")
        memory_line = ""
        for b in st.bought:
            if b.get("loc") == st.loc:
                memory_line = f"「上回带走的{b['name']}，还合心意吧？」"
                break
        if not memory_line:
            for did in st.dishes_tried:
                dish = self.pack["_dish"].get(did)
                if dish and st.loc in dish.get("locs", []):
                    memory_line = f"「上回那份{dish['name']}，吃得惯吧？」"
                    break
        self.emit((greet + memory_line).strip())

    # ---------- 吃买歇 ----------
    def _cmd_eat(self, arg):
        st, meta = self.state, self.pack["meta"]
        loc = self.pack["_loc"][st.loc]
        if not content.loc_open(loc, min(st.t, 9)):
            self.emit(f"这会儿{loc['name']}的店家都歇了（{content.hours_text(loc)}）。"
                      f"想吃东西，得赶在开门的时辰。")
            return
        dishes = content.dishes_at(self.pack, st.loc)
        if not dishes:
            self.emit("这附近没什么正经吃食。市场、老街一带总归有的。")
            return
        if not arg:
            lines = ["能吃到的——"]
            for d in dishes:
                lines.append(f"  {d['name']}｜{fmt_money(meta['currency_symbol'], d['price'])}")
            lines.append("（eat <名字> 来一份）")
            self.emit("\n".join(lines))
            return
        did = fuzzy_pick(arg, [(d["id"], [d["id"], d["name"]]) for d in dishes])
        if did is None:
            self.emit(f"这儿没有「{arg}」。eat 看看有什么。")
            return
        dish = self.pack["_dish"][did]
        if st.t + 1 > 10:
            self.emit("店家都在收摊了。回旅舍吧。")
            return
        if not self._pay(dish["price"], dish["name"]):
            self.emit(f"摸了摸钱包，{dish['name']}要"
                      f"{fmt_money(meta['currency_symbol'], dish['price'])}，这顿吃不起。")
            return
        self._spend_t(1)
        self._gain_energy(int(dish.get("energy", 20)))
        self.emit(dish["text"])
        self._mate_ambient("eat")
        if did not in st.dishes_tried:
            st.dishes_tried.append(did)
            self._journal("风味", dish["name"], dish["text"],
                          self.pack["_loc"][st.loc]["name"])
        self.record(kind="eat", dish=did, tags=dish.get("tags", []))

    def _cmd_buy(self, arg):
        st, meta = self.state, self.pack["meta"]
        loc = self.pack["_loc"][st.loc]
        goods = loc.get("shop", [])
        if not goods:
            self.emit("这儿没什么可买的。市场和老街上总有些让人手痒的小东西。")
            return
        if not content.loc_open(loc, min(st.t, 9)):
            self.emit(f"店家都打烊了（{content.hours_text(loc)}）。橱窗里的东西"
                      f"明天还在，钱包今晚是安全的。")
            return
        if not arg:
            lines = ["货架上——"]
            for g in goods:
                lines.append(f"  {g['name']}｜{fmt_money(meta['currency_symbol'], g['price'])}｜{g.get('text','')}")
            lines.append("（buy <名字> 买下）")
            self.emit("\n".join(lines))
            return
        gid = fuzzy_pick(arg, [(g["id"], [g["id"], g["name"]]) for g in goods])
        if gid is None:
            self.emit(f"没找到「{arg}」。buy 看看货架。")
            return
        item = next(g for g in goods if g["id"] == gid)
        if any(b["id"] == gid for b in st.bought):
            self.emit(f"{item['name']}你已经买过一份了。")
            return
        if st.t + 1 > 10:
            self.emit("店家在拉卷帘门了。明天再来吧。")
            return
        if not self._pay(item["price"], item["name"]):
            self.emit(f"钱不凑手，{item['name']}只好先放下。")
            return
        self._spend_t(1)
        self.emit(f"你把{item['name']}收进包里。{item.get('text', '')}")
        st.bought.append({"id": gid, "name": item["name"], "tags": item.get("tags", []),
                          "city": meta["city"], "loc": st.loc})
        self._journal("纪念", item["name"], item.get("text", item["name"]), loc["name"])
        self.record(kind="buy", tags=item.get("tags", []), item=gid)

    def _cmd_postcard(self, arg):
        """寄一张明信片。想附一句话就写在后面，不写也行。"""
        st, meta = self.state, self.pack["meta"]
        price = int(meta.get("postcard_price", 30))
        if st.t + 1 > 10:
            self.emit("邮筒不跑，但今天你该歇了。")
            return
        if not self._pay(price, "明信片和邮票"):
            self.emit("摸了摸钱包，连明信片都要斟酌——那就明天再寄。")
            return
        self._spend_t(1)
        raw_flavor = meta.get("postcard_flavor",
                              "你在街角的小店挑了一张明信片，贴上邮票。")
        if isinstance(raw_flavor, list):
            rng = stable_rng(st.seed, "postcard", st.day, st.t)
            flavor = rng.choice(raw_flavor)
        else:
            flavor = raw_flavor
        if arg:
            body = f"背面你只写了一句：「{arg}」"
        else:
            body = "背面想了半天，最后只画了个小小的太阳。"
        self.emit(f"{flavor}{body}投进邮筒的那一下，旅行忽然有了收信人。")
        self._journal("纪念", f"寄自{meta['city']}的明信片",
                      f"{flavor}{body}", self.pack["_loc"][st.loc]["name"])
        self.record(kind="postcard")

    def _cmd_rest(self, arg):
        st = self.state
        loc = self.pack["_loc"][st.loc]
        if st.t + 1 > 10:
            self.emit("这个点就别歇了，直接 sleep 吧。")
            return
        self._spend_t(1)
        self._gain_energy(22)
        text = loc.get("rest_text")
        if not text:
            generic = {
                "cafe": "你找了个靠窗的位子坐下，让一杯热饮的时间慢慢过去。",
                "park": "你在树荫下的长椅上坐了坐，风把别处的声音送过来又带走。",
                "temple": "你在廊下坐了一会儿。这种地方，坐着本身就是内容。",
            }
            text = generic.get(loc["type"], "你找了个能坐的地方歇脚，看人来人往。")
        self.emit(text)
        self.record(kind="rest", loc=st.loc, loc_type=loc["type"])

    # ---------- 搭子 ----------
    def _cmd_chat(self, arg):
        st = self.state
        if not self.mate:
            self.emit("这一程你是独行。安静也有安静的好。")
            return
        day_key = f"chat:{st.day}"
        n = st.flags.get(day_key, 0)
        if n >= 3:
            self.emit(f"{self.mate['name']}摆摆手：「今天话说够啦，看景看景。」")
            return
        st.flags[day_key] = n + 1
        lines = self.mate.get("lines", {})
        slot, weather = slot_of(st.t), self._weather()
        rainy = any(x in weather for x in ("雨", "雪"))
        if rainy and lines.get("rain"):
            bucket = lines["rain"]
        else:
            key = {"清晨": "morning", "黄昏": "dusk", "夜晚": "night"}.get(slot)
            bucket = lines.get(key) or lines.get("default") or ["……"]
        rng = stable_rng(st.seed, "chat", st.day, st.t, n)
        self.emit(rng.choice(bucket))

    def _mate_moment(self) -> bool:
        """搭子时刻：首次在特定地点驻足观看时触发（内容包脚本）。"""
        st = self.state
        if not self.mate:
            return False
        box = st.box()
        moments = (self.pack.get("companion_moments", {}) or {}).get(st.mate, [])
        for m in moments:
            if m["loc"] != st.loc or m["loc"] in box["moments_fired"]:
                continue
            box["moments_fired"].append(m["loc"])
            self.emit(m["text"])
            if m.get("journal"):
                title = m.get("title", f"{self.mate['name']}在这里")
                self._journal(m.get("journal", "人物"), title, m["text"],
                              self.pack["_loc"][st.loc]["name"])
            self._mate_spoke = True
            return True
        return False

    def _mate_ambient(self, *keys):
        """搭子的「同行状态」：他自己在看、在吃、在忍不住说——不是功能，是个人。

        写死在 companions.json 的 reactions 池里，按情境键取；
        出现有节制：同一(地点,情境)只说一次，且每回合至多一句。
        """
        st = self.state
        if not self.mate or self._mate_spoke:
            return
        reactions = self.mate.get("reactions", {})
        if not reactions:
            return
        for key in keys:
            if not key or key not in reactions:
                continue
            flag = f"amb:{st.slug}:{st.loc}:{key}"
            if st.flags.get(flag):
                continue
            rng = stable_rng(st.seed, "ambient", st.slug, st.loc, key)
            st.flags[flag] = True
            if rng.random() > 0.65:     # 三成多的时候，他只是安静地在
                return
            self.emit(rng.choice(reactions[key]))
            self._mate_spoke = True
            return

    # ---------- 拍照 ----------
    def _cmd_photo(self, arg):
        st = self.state
        box = st.box()
        loc = self.pack["_loc"][st.loc]
        slot, weather = slot_of(st.t), self._weather()
        vis = box["visited"].setdefault(st.loc, {"looked": [], "explored": 0, "photos": []})
        key = f"{slot}|{weather}"
        if key in vis["photos"]:
            self.emit("取景框里和刚才那张几乎一样。你看了看，把它删了。")
            return
        vis["photos"].append(key)
        scene = content.pick_text(loc.get("photo") or loc.get("look", {}), slot, weather)
        self.emit(f"你举起相机。\n{scene}")
        caption = f"\n你在底下写：「{arg}」" if arg else ""
        self._journal("风景", f"{loc['name']}·{slot}", scene + caption, loc["name"])
        self._mate_ambient("photo")
        # 带相机的旅伴（小柒），总有一张会落在你身上
        if (self.mate and self.mate.get("camera")
                and loc["type"] in SCENIC_TYPES
                and not st.flags.get(f"portrait:{st.slug}")):
            st.flags[f"portrait:{st.slug}"] = True
            text = (f"{self.mate['name']}忽然叫你的名字，你一回头，快门响了。"
                    f"「这张不删，」她看着屏幕说，「你在{loc['name']}，光很好。」")
            self.emit(text)
            self._journal("纪念", f"有你的照片·{loc['name']}", text, loc["name"])
        self.record(kind="photo", loc=st.loc, slot=slot, weather=weather)

    # ---------- 睡觉与跨城 ----------
    def _cmd_sleep(self, arg):
        st, meta = self.state, self.pack["meta"]
        st.loc = meta["start_loc"]   # 夜里回到旅舍所在的落脚点
        rate = int(meta.get("hotel_rate", 0))
        if st.money >= rate:
            self._pay(rate, "住宿一晚")
            broke = False
        else:
            st.money = 0
            broke = True
        self.emit("你回到旅舍，洗去一身走出来的疲惫。窗外的城市还醒着，你先睡了。")
        if broke:
            self.emit("（住宿钱不太凑手，老板娘摆摆手让你先记着。明天得省着点了。）")

        st.day += 1
        st.t = 0
        st.energy = 100
        for k in list(st.flags):
            if k.startswith("chat:"):
                del st.flags[k]
        if st.day > st.days_total:
            self._finale(early=False)
            return
        weather = self._weather()
        rng = stable_rng(st.seed, "morning", st.day)
        breakfast = rng.choice([
            "楼下飘来早饭的香气。", "旅舍老板娘和你道了早安。",
            "你在门口站了站，深吸一口这座城醒来的味道。", "街上的第一班车正驶过。",
        ])
        self.emit(f"— 第 {st.day} 天 · {weather} —\n新的一天。{breakfast}"
                  f"（剩 {st.days_total - st.day + 1} 天，钱包 "
                  f"{fmt_money(meta['currency_symbol'], st.money)}）")

    def _cmd_fly(self, arg):
        st, meta = self.state, self.pack["meta"]
        if not arg:
            packs = [p for p in content.list_packs() if p["slug"] != st.slug]
            hint = "、".join(f"{p['city']}" for p in packs) if packs else "（还没有别的城市内容包）"
            self.emit(f"去哪座城？fly <城市名>。已经备好的：{hint}\n"
                      f"没备好的城市也可以直接说，我会现场做调研（需要 DeepSeek API，约一两分钟）。")
            return
        dest_pack = self._resolve_city_pack(arg)
        if dest_pack is None:
            return
        dmeta = dest_pack["meta"]
        if dmeta["slug"] == st.slug:
            self.emit("你就在这座城里。")
            return
        same_country = dmeta.get("country") == meta.get("country")
        t_cost = 3 if same_country else 5
        if st.t + t_cost > 9:
            self.emit("今天出发太晚了，到了也是深夜。明天一早再启程吧。")
            return
        src_rate = meta.get("cny_rate", 1) or 1
        dst_rate = dmeta.get("cny_rate", 1) or 1
        if same_country:
            fare_dst = int(dmeta.get("train_cost_hint",
                                     dmeta.get("transit_fare", 100) * 20))
            fare_word = "车票"
        else:
            fare_dst = int(dmeta.get("flight_cost_hint",
                                     dmeta.get("hotel_rate", 500) * 4))
            fare_word = "机票"
        fare_src = max(1, round(fare_dst / dst_rate * src_rate))
        if st.money < fare_src:
            self.emit(f"去{dmeta['city']}的{fare_word}要"
                      f"{fmt_money(meta['currency_symbol'], fare_src)}，钱不够。"
                      f"要么省几天，要么就在这座城把日子过完。")
            return
        self._pay(fare_src, f"去{dmeta['city']}的{fare_word}")
        self._spend_t(t_cost)
        self._spend_energy(10 if same_country else 18)

        if dmeta.get("currency") != meta.get("currency"):
            new_money = int(st.money / src_rate * dst_rate * 0.98)
            self.emit(f"到站后你在兑换窗口把剩下的钱换成了{dmeta.get('currency','当地货币')}"
                      f"——{fmt_money(meta['currency_symbol'], st.money)} 换成 "
                      f"{fmt_money(dmeta['currency_symbol'], new_money)}（手续费吃掉一点）。")
            st.money = new_money

        vehicle = "列车" if same_country else "飞机"
        self.emit(f"{vehicle}载着你离开{meta['city']}。窗外的景色慢慢换了口音。")
        self.pack = dest_pack
        first_time = dmeta["slug"] not in st.cities
        enter_city(st, dest_pack)
        self.mark(f"city:{dmeta['slug']}")
        self.emit(dmeta["intro"] if first_time else
                  f"又回到了{dmeta['city']}。熟门熟路，像回到一个旧朋友家。")
        if first_time:
            menu = self._wish_menu()
            if menu:
                self.emit(f"手帐翻开新的一页，印着{dmeta['city']}的心愿清单"
                          f"（wishes 查看、wish <编号> 抄下；心愿页扩到 "
                          f"{st.wish_cap} 条）。")
        self.record(kind="visit", loc=st.loc)

    def _resolve_city_pack(self, query: str):
        """按名字找已生成的内容包；没有则尝试现场生成。"""
        packs = content.list_packs()
        cands = []
        for p in packs:
            names = [p["slug"], p.get("city") or ""]
            cands.append((p["slug"], names))
        slug = fuzzy_pick(query, cands)
        if slug:
            return content.load_pack(slug)
        from . import llm
        if not llm.has_key():
            known = "、".join(p["city"] for p in packs) or "（无）"
            self.emit(f"「{query}」的内容包还没做，而且没配 DEEPSEEK_API_KEY，"
                      f"没法现场调研。已备好的城市：{known}")
            return None
        self.emit(f"你翻开手机开始查去{query}的路线……（首次到访，正在为它做调研与"
                  f"内容生成，通常一两分钟，请稍等）")
        try:
            from .generate import build_city
            new_slug = build_city(query, quiet=False)
            return content.load_pack(new_slug)
        except Exception as e:
            self.emit(f"调研没能完成：{e}\n可以稍后用 `python play.py build --city "
                      f"{query}` 重试，或先去已备好的城市。")
            return None

    # ---------- 提前回程 / 终局 ----------
    def _cmd_end(self, arg):
        if arg.casefold() != "trip":
            self.emit("确定要现在结束旅程回家吗？再输入 end trip 确认。")
            return
        self._finale(early=True)

    def _finale(self, early: bool):
        st, meta = self.state, self.pack["meta"]
        st.ended = True
        if early:
            self.emit("你决定就到这里。有些旅行不必走满全程，想回家的那一刻，"
                      "就是终点到了。")
        self.emit(self._build_outro())
        sc = self._score()
        st.score = sc
        names = st.route_names or st.route
        days_lived = min(st.day, st.days_total)
        lines = [f"—— 旅程结算 ——", f"足迹：{ ' → '.join(names) }，共 {days_lived} 天"]
        lines.append(f"回味值 {sc['total']} —— {sc['grade']}")
        if sc["labels"]:
            lines.append("它由这些成色组成——" + "、".join(sc["labels"]))
        else:
            lines.append("这趟走得很轻，颜色还留在下一次")
        lines.append("（export 可导出完整游记）")
        lines.append("（share 可做一份手帐分享页）")
        lines.append("（report 可洗出这趟旅行的报告与颜色）")
        self.emit("\n".join(lines))

    def _build_outro(self) -> str:
        st, meta = self.state, self.pack["meta"]
        city = meta["city"]
        parts = []
        shell = meta.get("outro_shell") or (
            f"回程的路上，{city}在身后一点点变小。")
        parts.append(shell)
        memories = []
        if st.stories_heard:
            title = st.stories_heard[-1]
            memories.append(f"一个听来的故事")
        has_gifts = bool(st.bought)
        if has_gifts:
            memories.append("背包里多出来的东西")
        box = st.box()
        mastered = [k for k in st.flags if k.startswith(f"mastered:{st.slug}:")]
        if mastered:
            lid = mastered[0].split(":")[2]
            loc = self.pack["_loc"].get(lid, {})
            memories.append(f"逛到熟透的{loc.get('name', '那条街')}")
        if st.journal:
            photos = [e for e in st.journal if e.get("type") == "风景"]
            if photos:
                memories.append("几张拍下的画面")
        done_wishes = [w for w in st.wishes if w.get("done")]
        if done_wishes:
            memories.append("手帐上画了钩的心愿")
        if memories:
            parts.append("你想起" + "、".join(memories) + "。")
        closing = meta.get("outro_closing") or (
            f"你没有回头看太久——该带走的，都已经在日记里了。")
        parts.append(closing)
        return "".join(parts)

    def _score(self) -> dict:
        """回味值：只在终局出现一次，由这趟旅行的「成色」得出。

        计分哲学——布尔成色反刷量：第五份甜点不加分；坐两次黄昏的河岸与
        买五件纪念品等值。每种成色只问有没有，不问几次（各 12 分），量的
        那一面只留「日记涓滴」一小截（至多 15 分）。所以分数是这趟旅行的
        宽度，不是它的次数。
        """
        s = scoring.compute(self.state, self.pack)
        breakdown = {label: scoring.POINT_PER_DIM for label in s["labels"]}
        breakdown["日记涓滴"] = s["trickle"]
        return {"total": s["score"], "score": s["score"], "grade": s["grade"],
                "breakdown": breakdown, "dims": s["dims"], "labels": s["labels"]}

    # ---------- 事件 ----------
    def _maybe_event(self):
        st = self.state
        box = st.box()
        loc = self.pack["_loc"].get(st.loc, {})
        weather = self._weather()
        rng = stable_rng(st.seed, "event", st.slug, st.day, st.t, len(st.log))
        events = list(self.pack.get("events", []))
        rng.shuffle(events)
        for ev in events:
            if ev.get("once") and ev["id"] in box["events_fired"]:
                continue
            if ev.get("weather") and ev["weather"] not in weather:
                continue
            if ev.get("slots") and not (ev["slots"][0] <= min(st.t, 9) <= ev["slots"][1]):
                continue
            if ev.get("district") and loc.get("district") != ev["district"]:
                continue
            if ev.get("loc") and st.loc != ev["loc"]:
                continue
            if rng.random() >= float(ev.get("chance", 0.15)):
                continue
            box["events_fired"].append(ev["id"])
            self.emit(ev["text"])
            if ev.get("journal"):
                title = ev.get("title", "路上的一幕")
                self._journal(ev["journal"], title, ev["text"],
                              loc.get("name", ""))
            rv = ev.get("reveal") or {}
            if rv.get("loc"):
                self._reveal_loc(rv["loc"])
            return

    # ---------- 心愿 ----------
    def _check_wishes(self):
        st = self.state
        for w in st.wishes:
            if w["done"]:
                continue
            spec = self.pack["_wish"].get(w["wid"]) if w["city"] == st.slug else None
            if spec is None:
                continue
            if self._wish_hit(spec["check"]):
                w["done"] = True
                w["day"] = st.day
                self.note(f"✦ 心愿达成：「{w['text']}」")
                self.mark(f"wish:{w['wid']}")
                self._journal("心愿", w["text"], f"手帐上这一行，今天可以画钩了：{w['text']}")

    def _wish_hit(self, check: dict) -> bool:
        st = self.state
        ctype = check.get("type")
        if ctype == "story":
            return len(st.stories_heard) >= int(check.get("count", 1))
        if ctype == "gem":
            return st.gems >= int(check.get("count", 1))
        if ctype == "npc":
            return check.get("id") in st.box()["met"]
        for r in self.records:
            if ctype == "look" and r["kind"] == "look":
                if check.get("loc") and r["loc"] != check["loc"]:
                    continue
                if check.get("slot") and r["slot"] != check["slot"]:
                    continue
                if check.get("weather") and check["weather"] not in r["weather"]:
                    continue
                return True
            if ctype == "visit" and r["kind"] == "visit" and r["loc"] == check.get("loc"):
                return True
            if ctype == "eat" and r["kind"] == "eat":
                if check.get("dish") and r["dish"] != check["dish"]:
                    continue
                if check.get("tag") and check["tag"] not in r.get("tags", []):
                    continue
                return True
            if ctype == "photo" and r["kind"] == "photo":
                if check.get("loc") and r["loc"] != check["loc"]:
                    continue
                if check.get("slot") and r["slot"] != check["slot"]:
                    continue
                if check.get("weather") and check["weather"] not in r["weather"]:
                    continue
                return True
            if ctype == "buy" and r["kind"] == "buy":
                if check.get("tag") and check["tag"] not in r.get("tags", []):
                    continue
                return True
            if ctype == "explore" and r["kind"] == "explore":
                if check.get("loc") and r["loc"] != check["loc"]:
                    continue
                if r["level"] < int(check.get("level", 1)):
                    continue
                return True
            if ctype == "rest" and r["kind"] == "rest":
                if check.get("loc_type") and r["loc_type"] != check["loc_type"]:
                    continue
                return True
            if ctype == "listen" and r["kind"] == "listen":
                if check.get("loc") and r["loc"] != check["loc"]:
                    continue
                if check.get("slot") and r["slot"] != check["slot"]:
                    continue
                if check.get("weather") and check["weather"] not in r["weather"]:
                    continue
                return True
            if ctype == "join" and r["kind"] == "join":
                if check.get("loc") and r["loc"] != check["loc"]:
                    continue
                if check.get("loc_type") and r["loc_type"] != check["loc_type"]:
                    continue
                return True
            if ctype == "wander" and r["kind"] == "wander":
                return True
            if ctype == "postcard" and r["kind"] == "postcard":
                return True
        return False

    # ---------- 行前功课的回忆（不是问答） ----------
    @staticmethod
    def _bigrams(s: str) -> set:
        s = re.sub(r"[\s，。！？、·；：（）「」『』…—\-\?\!,\.]+", "", s)
        return {s[i:i + 2] for i in range(len(s) - 1)}

    def _ambient_trivia(self, loc: dict):
        """初到一地，行前功课里相关的那句会自己浮上来——是回忆涌现，不是查询。"""
        st = self.state
        flag = f"trivia:{st.slug}:{loc['id']}"
        if st.flags.get(flag):
            return
        trivia = self.pack.get("trivia", [])
        if not trivia:
            return
        seen = st.flags.get("_seen_trivia") or []
        key = self._bigrams(f"{loc['name']}{loc.get('name_local', '')}{loc['brief']}")
        best, best_score = None, 0
        for tv in trivia:
            if tv in seen:
                continue
            score = len(key & self._bigrams(tv))
            if score > best_score:
                best, best_score = tv, score
        if best is not None and best_score >= 2:
            st.flags[flag] = True
            seen.append(best)
            st.flags["_seen_trivia"] = seen
            self.emit(f"脑子里翻出一句来——{best}")
