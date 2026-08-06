# -*- coding: utf-8 -*-
"""卧游 · 旅行报告。

把一段存档洗成一份「年度报告体」的纯文字报告，给 AI 玩家自己读：
封面 / 开头段 / 观察 / 时间线 / 拍立得 / 心愿 / 显影 / 尾注 / 附录。

定稿原则：
- 除「观察」一段外，全文由存档数据确定性拼装——不联网、不猜测、不润色；
- 正文里不出现任何分数与成色清单：数字只留在文件末尾的附录；
- 「观察」是唯一的 LLM 环节（DeepSeek，可选）。未开启或生成失败就整段省略，
  玩家的自语回落到时间线——每句自语在整份报告里只出现一次；
- 颜色（显影段）来自 woyou.score：这趟旅行达成了哪几味，就染成什么颜色；
- 真实照片要过「时辰档案」的门（photos/manifest.json 里的时段／天气／季节）：
  一张照片只在它能诚实代表的条件下出场，对不上就静默回落文字拍立得。
  用了谁的照片，附录的「图片来源」就得逐张署名——CC 的义务不打折。

公开 API：
    make_report(state, pack, observe=False, photos="text") -> dict
    save_report(state, pack, observe=False, photos="text") -> Path
"""
import json
import unicodedata
from pathlib import Path

from . import llm
from .util import CONTENT_DIR, ROOT, SAVE_DIR, fmt_money, read_json, slot_of

# ---------------------------------------------------------------- 版式常量

PAGE_W = 46           # 全篇的版心宽度（显示列，CJK 记 2）
COVER_RULE = "═" * PAGE_W
BODY_COLS = PAGE_W - 2   # 拍立得框内文字的折行宽度
PHOTO_LIMIT = 6


def _rule(title: str) -> str:
    return f"──── {title} ────"


RULE_OBSERVE = _rule("观察")
RULE_TIMELINE = _rule("时间线")
RULE_PHOTOS = _rule("拍立得")
RULE_WISHES = _rule("心愿")
RULE_DEVELOP = _rule("显影")
RULE_APPENDIX = _rule("附录")

MARKS = {"风景": "📷", "故事": "📜", "心愿": "✦"}
FORM_LABEL = {"text": "文字", "real": "照片", "both": "都要"}

STATE_PREFIX = "STATE "
SLEEP_WORDS = {"sleep", "睡", "睡觉", "回旅舍", "回酒店"}

# 从 log 的输出里认出「被拦下的那些时候」。
# 只认引擎写死的整句片段——不认单个词，免得把风景文字里的「收摊时分」当成扑空。
BLOCKED_HINTS = [
    ("还没开（", "扑空：没到开门的时辰"),
    ("这会儿不开", "扑空：没到开门的时辰"),
    ("店家都歇了（", "扑空：店家歇着"),
    ("店家都打烊了", "扑空：店家打烊了"),
    ("店家都在收摊", "扑空：店家在收摊"),
    ("店家在拉卷帘门", "扑空：店家在拉卷帘门"),
    ("吃闭门羹", "太晚了，赶过去也是吃闭门羹"),
    ("兜里不够", "门票钱不够，在门外站了站就走了"),
    ("钱不凑手", "钱不凑手，东西先放下了"),
    ("这顿吃不起", "钱不够，这顿没吃成"),
    ("钱不够", "钱不够"),
    ("腿实在抬不动", "腿走不动了"),
    ("有点走不动了", "腿走不动了"),
    ("腿已经在抗议", "腿走不动了"),
    ("眼皮在打架", "夜太深，被劝回旅舍"),
    ("夜太深，巷子都睡了", "夜太深，巷子都睡了"),
    ("今天出发太晚了", "太晚了，没能启程"),
    ("今天太晚了", "太晚了，没能成行"),
    ("天太晚了", "太晚了，没能成行"),
    ("太晚了，人家也要归家", "太晚了，人家要归家了"),
    ("太晚了，本地人都归家", "太晚了，本地人都归家了"),
    ("邮筒不跑", "今天该歇了，明信片明天再寄"),
]

OBSERVE_SYSTEM = """你在为一份旅行报告写「观察」一段。材料是一份旅行存档的事实清单。

硬规则（逐条遵守）：
1) 分行诗体：一行一个念头，行尾不加句号，节与节之间空一行，总共 4-7 节、不超过 28 行。
2) 三禁：
   禁心理断言——不写「懂了」「爱上了」「学会了」「你发现」「你感到」「你明白」
   「你意识到」这类替人认定的内心变化，只写眼耳鼻舌身能接收到的；
   禁替玩家下判断——不写「排对了」「值得了」「不虚此行」这类结论；
   禁编造——事实清单里没有的地点、人、话、事，一个字也不许添。
3) 玩家自语：清单里每一条自语都必须以「你说，」引出并逐字引用——不改字、不润色、
   不省略任何一条，每条恰好出现一次。
4) 没有自语就一个字也不提自语。
5) 视角：你是一架不带感情的摄影机。只拍到的才写，拍不到的（内心活动、
   价值判断、因果归纳）一律不写。「你走过」「你停下」可以，
   「你领悟」「你被打动」不行。

只输出这一段本身，不要标题，不要解释，不要 Markdown 标记。"""


# ---------------------------------------------------------------- 小工具

def _cw(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _width(s: str) -> int:
    return sum(_cw(c) for c in s)


def _center(s: str, cols: int = PAGE_W) -> str:
    pad = max(0, (cols - _width(s)) // 2)
    return " " * pad + s


def _wrap(text: str, cols: int = BODY_COLS) -> list:
    out, cur, w = [], "", 0
    for ch in (text or "").replace("\r", ""):
        if ch == "\n":
            out.append(cur)
            cur, w = "", 0
            continue
        cw = _cw(ch)
        if w + cw > cols:
            out.append(cur)
            cur, w = "", 0
        cur += ch
        w += cw
    if cur:
        out.append(cur)
    return out or [""]


def _log_state(entry: dict):
    for ln in (entry.get("out") or "").split("\n"):
        if ln.startswith(STATE_PREFIX):
            try:
                return json.loads(ln[len(STATE_PREFIX):])
            except (ValueError, TypeError):
                return None
    return None


def _companions() -> dict:
    path = CONTENT_DIR / "companions.json"
    if path.exists():
        try:
            return {c["id"]: c for c in read_json(path).get("companions", [])}
        except (OSError, ValueError, KeyError):
            return {}
    return {}


def _city_names(state, pack) -> list:
    names = list(state.route_names or [])
    if len(names) < len(state.route):
        names = []
        for s in state.route:
            names.append(pack["meta"]["city"] if s == pack["meta"]["slug"] else s)
    return names or [pack["meta"]["city"]]


def _slug_of_city(state, pack, city_name: str) -> str:
    for slug, name in zip(state.route, _city_names(state, pack)):
        if name == city_name:
            return slug
    return pack["meta"]["slug"]


def _city_name_of_slug(state, pack, slug: str) -> str:
    for s, name in zip(state.route, _city_names(state, pack)):
        if s == slug:
            return name
    return pack["meta"]["city"] if slug == pack["meta"]["slug"] else slug


def _days_lived(state) -> int:
    """走过的天数：自然走完 = 全程；提前回程 = 到那天为止。"""
    if state.day > state.days_total:
        return max(1, state.day - 1)
    return max(1, state.day)


# ---------------------------------------------------------------- 事实提取

def _footprints(state) -> dict:
    """{day: [地点名, ...]}——当天到过的地点顺序（log 为主，日记补全旧日子）。

    sleep 的 STATE 显示的是新一天的起始位置，记录为次日链头。
    """
    order = {}
    for e in state.log:
        day = e.get("day")
        if day is None:
            continue
        head = (e.get("cmd") or "").split(None, 1)[0].casefold() if e.get("cmd") else ""
        s = _log_state(e)
        loc = (s or {}).get("loc")
        if not loc:
            continue
        if head in SLEEP_WORDS:
            lst = order.setdefault(day, [])
            if not lst or lst[0] != loc:
                lst.insert(0, loc)
            continue
        lst = order.setdefault(day, [])
        if not lst or lst[-1] != loc:
            lst.append(loc)
    for e in state.journal:
        loc = (e.get("loc") or "").strip()
        if not loc:
            continue
        lst = order.setdefault(e["day"], [])
        if loc not in lst:
            lst.append(loc)
    return order


def _footprint_lines(footprints: dict) -> dict:
    """把足迹链渲染成每天一行，跨天重访标注（又去了一次）。

    每天第一个位置是起床/出发点，不标「又去了一次」。
    """
    lines, seen = {}, set()
    for day in sorted(footprints):
        parts = []
        for i, name in enumerate(footprints[day]):
            if i > 0 and name in seen:
                parts.append(f"{name}（又去了一次）")
            else:
                parts.append(name)
        for name in footprints[day]:
            seen.add(name)
        lines[day] = " → ".join(parts)
    return lines


def _revisits(footprints: dict) -> list:
    out, seen = [], set()
    for day in sorted(footprints):
        for i, name in enumerate(footprints[day]):
            if i > 0 and name in seen:
                out.append((day, name))
        seen.update(footprints[day])
    return out


def _blocked(state) -> list:
    """从 log 里捞出「被拦下」的时候：没开门、钱不够、太晚了、走不动。"""
    out = []
    for e in state.log:
        text = e.get("out") or ""
        for key, why in BLOCKED_HINTS:
            if key in text:
                out.append((e.get("day"), slot_of(e.get("t", 0)),
                            (e.get("cmd") or "").strip(), why))
                break
    return out


def _recognitions(state, pack) -> list:
    """世界记得你的那些时候——直接读 flags，比翻输出文字可靠。"""
    out = []
    slug = pack["meta"]["slug"]
    npcs = {n["id"]: n.get("name", n["id"]) for n in pack.get("npcs", [])}
    locs = {l["id"]: l.get("name", l["id"]) for l in pack.get("locations", [])}
    for key in state.flags:
        parts = str(key).split(":")
        here = len(parts) > 1 and parts[1] == slug
        if parts[0] == "recall" and len(parts) >= 3:
            who = npcs.get(parts[2], parts[2]) if here else parts[2]
            out.append(f"{who} 认出了你，回头客和游客是两种待遇")
        elif parts[0] == "habit" and len(parts) >= 4:
            where = locs.get(parts[2], parts[2]) if here else parts[2]
            out.append(f"第{parts[3]}天 同一个时辰又站到了 {where}")
        elif parts[0] == "mastered" and len(parts) >= 3:
            where = locs.get(parts[2], parts[2]) if here else parts[2]
            out.append(f"{where} 被你逛成了熟地")
    seen, uniq = set(), []
    for line in out:
        if line not in seen:
            seen.add(line)
            uniq.append(line)
    return uniq


def _notes(state) -> list:
    """玩家自语（按发生顺序，逐字）。"""
    out = []
    for e in state.log:
        note = (e.get("note") or "").strip()
        if note:
            out.append({"day": e.get("day"), "slot": slot_of(e.get("t", 0)),
                        "text": note})
    return out


def _item_price(pack, item_id: str) -> int:
    for l in pack.get("locations", []):
        for g in l.get("shop", []) or []:
            if g.get("id") == item_id:
                return int(g.get("price", 0) or 0)
    return 0


def _spend(state, pack) -> dict:
    """花销拆解：住宿与吃是算得准的，剩下的都算在车票与门票头上。"""
    meta = pack["meta"]
    nights = max(0, state.day - 1)
    hotel = nights * int(meta.get("hotel_rate", 0) or 0)
    food = 0
    for did in state.dishes_tried:
        d = pack.get("_dish", {}).get(did)
        if d:
            food += int(d.get("price", 0) or 0)
    gifts, gift_cost = [], 0
    for b in state.bought:
        gifts.append(b.get("name", ""))
        gift_cost += _item_price(pack, b.get("id", ""))
    rate = float(meta.get("cny_rate", 1) or 1)
    spent_local = getattr(state, "spent_local", 0)
    total = int(spent_local) if spent_local else int(round(int(state.spent or 0) * rate))
    tickets = max(0, total - hotel - food - gift_cost)
    return {"nights": nights, "hotel": hotel, "food": food,
            "gifts": [g for g in gifts if g], "gift_cost": gift_cost,
            "tickets": tickets, "total": total,
            "symbol": meta.get("currency_symbol", "")}


def _stories(state, pack) -> list:
    """[(讲的人, 故事名, 回声)]——回声是故事文本里最有余味的那一句。"""
    out = []
    for title in state.stories_heard:
        who, echo = "", ""
        for n in pack.get("npcs", []):
            st = n.get("story") or {}
            if st.get("title") == title:
                who = n.get("name", "")
                echo = (st.get("echo") or "").strip()
                break
        out.append((who, title, echo))
    return out


def _facts(state, pack) -> dict:
    footprints = _footprints(state)
    if state.ended and state.day > state.days_total:
        for d in list(footprints):
            if d > state.days_total:
                del footprints[d]
    places = set()
    for names in footprints.values():
        places.update(names)
    met = 0
    for box in (state.cities or {}).values():
        met += len(box.get("met", []) or [])
    mates = _companions()
    mate = mates.get(state.mate) or {}
    return {
        "days": _days_lived(state),
        "city_names": _city_names(state, pack),
        "mate_name": mate.get("name", ""),
        "footprints": footprints,
        "chains": _footprint_lines(footprints),
        "places": places,
        "met": met,
        "spend": _spend(state, pack),
        "stories": _stories(state, pack),
        "notes": _notes(state),
        "revisits": _revisits(footprints),
        "blocked": _blocked(state),
        "recognitions": _recognitions(state, pack),
    }


# ---------------------------------------------------------------- ① 封面

def _cover(state, pack, f) -> str:
    meta = pack["meta"]
    cities = "、".join(f["city_names"])
    span = f"{f['days']}天" if state.ended else f"行至第{state.day}天"
    mate = f"与{f['mate_name']}同行" if f["mate_name"] else "独行"
    return "\n".join([
        COVER_RULE,
        _center("卧游 · 旅行报告"),
        _center(f"{cities} · {meta['country']} ｜ {span} ｜ {mate}"),
        COVER_RULE,
    ])


# ---------------------------------------------------------------- ② 开头段

def _opening(state, pack, f) -> str:
    lines = [f"走过 {len(f['places'])} 处地方"]
    if f["met"]:
        lines.append(f"遇见 {f['met']} 个本地人")
    if len(f["city_names"]) > 1:
        lines.append(" ⇢ ".join(f["city_names"]))
    for who, title, echo in f["stories"]:
        lines.append(f"{who or '有人'}对你讲了「{title}」")
        if echo:
            lines.append(f"——「{echo}」")
    sp = f["spend"]
    sym = sp["symbol"]
    lines.append(f"住宿 {fmt_money(sym, sp['hotel'])}，吃 {fmt_money(sym, sp['food'])}")
    tail = fmt_money(sym, sp["tickets"]) + " 的车票与门票"
    if sp["gifts"]:
        lines.append(f"剩下的，变成了{'、'.join(sp['gifts'])}和 {tail}")
    else:
        lines.append(f"剩下的，变成了 {tail}")
    return "\n".join(lines)


# ---------------------------------------------------------------- ③ 观察段

def _facts_digest(state, pack, f) -> str:
    """喂给 LLM 的事实清单——同一份原样进附录，供人核对观察的出处。"""
    meta = pack["meta"]
    sp = f["spend"]
    sym = sp["symbol"]
    L = []
    span = f"{f['days']} 天" if state.ended else f"行至第 {state.day} 天（未结束）"
    mate = f"与{f['mate_name']}同行" if f["mate_name"] else "独行"
    L.append(f"城市：{'、'.join(f['city_names'])}（{meta['country']}），{span}，{mate}")
    L.append(f"花销：住宿 {fmt_money(sym, sp['hotel'])}（{sp['nights']} 晚）"
             f"／吃 {fmt_money(sym, sp['food'])}"
             f"／纪念品 {'、'.join(sp['gifts']) or '无'}"
             f"／车票与门票 {fmt_money(sym, sp['tickets'])}")

    by_day = {}
    for e in state.journal:
        by_day.setdefault(e["day"], []).append(e)
    days = sorted(set(by_day) | set(f["footprints"]))
    L.append("")
    L.append("逐日——")
    for d in days:
        weather = state.weather_by_day.get(str(d), "")
        head = f"第{d}天" + (f" · {weather}" if weather else "")
        L.append(head)
        chain = f["chains"].get(d)
        if chain:
            L.append(f"  足迹：{chain}")
        titles = [f"{e['type']}「{e['title']}」" for e in by_day.get(d, [])]
        if titles:
            L.append("  日记：" + "／".join(titles))

    if f["stories"]:
        L.append("")
        L.append("听到的故事：" + "／".join(
            f"「{t}」（{w or '本地人'}）" for w, t, _ in f["stories"]))

    L.append("")
    if f["notes"]:
        L.append(f"玩家自语（共 {len(f['notes'])} 条）：")
        for i, n in enumerate(f["notes"], 1):
            L.append(f"  {i}. {n['text']}")
    else:
        L.append("玩家自语：无")

    L.append("")
    if state.wishes:
        done = [w for w in state.wishes if w["done"]]
        L.append(f"心愿：抄下 {len(state.wishes)} 条，应验 {len(done)} 条")
        for w in state.wishes:
            L.append(f"  {'✓' if w['done'] else '✗'} {w['text']}")
    else:
        L.append("心愿：一条也没抄")

    if f["revisits"] or f["blocked"] or f["recognitions"]:
        L.append("")
        L.append("路上的重访与被拦——")
        for day, name in f["revisits"]:
            L.append(f"  第{day}天 又去了一次 {name}")
        for line in f["recognitions"]:
            L.append(f"  {line}")
        for day, slot, cmd, why in f["blocked"]:
            L.append(f"  第{day}天 {slot} {cmd or '（走着走着）'}：{why}")
    return "\n".join(L)


def _observe(digest: str) -> str:
    """唯一的 LLM 环节。没 key、出错、空回复——都当作没有这一段。"""
    try:
        if not llm.has_key():
            return ""
        client = llm.DeepSeek()
        text = client.chat(OBSERVE_SYSTEM, digest, temperature=0.5,
                           max_tokens=1200)
    except Exception:
        return ""
    text = (text or "").strip()
    return text


# ---------------------------------------------------------------- ④ 时间线

def _timeline(state, pack, f, with_notes: bool) -> str:
    by_day = {}
    for e in state.journal:
        by_day.setdefault(e["day"], []).append(e)
    notes_by_day = {}
    if with_notes:
        for n in f["notes"]:
            notes_by_day.setdefault(n["day"], []).append(n)

    days = sorted(set(by_day) | set(f["footprints"]) | set(notes_by_day))
    lines = [RULE_TIMELINE]
    if not days:
        lines.append("")
        lines.append("（还没有可写的一天——行李刚放下，门还开着）")
        return "\n".join(lines)
    for d in days:
        weather = state.weather_by_day.get(str(d), "")
        head = f"第{d}天"
        if weather:
            head += f" · {weather}"
        chain = f["chains"].get(d)
        if chain:
            head += f" ｜ {chain}"
        lines.append("")
        lines.append(head)
        rows = []
        for e in by_day.get(d, []):
            mark = MARKS.get(e["type"], "·")
            rows.append((e["slot"], f"{e['slot']}  {mark} {e['title']}"))
        pending = list(notes_by_day.get(d, []))
        out_rows = []
        for slot, row in rows:
            out_rows.append(row)
            for n in [x for x in pending if x["slot"] == slot]:
                out_rows.append(f"      🗨「{n['text']}」")
                pending.remove(n)
        for n in pending:      # 那个时段没记下任何日记，自语自己成行
            out_rows.append(f"{n['slot']}  🗨「{n['text']}」")
        if not out_rows:
            out_rows.append("      （这一天只是走路和看天）")
        lines.extend(out_rows)
    return "\n".join(lines)


# ---------------------------------------------------------------- ⑤ 拍立得

def _loc_index(pack) -> dict:
    idx = {}
    for l in pack.get("locations", []):
        idx[l.get("name", "")] = l
    return idx


def _manifest(slug: str) -> dict:
    """content/<slug>/photos/manifest.json → {地点键: [条目, ...]}。"""
    path = CONTENT_DIR / slug / "photos" / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return {}
    rows = []
    if isinstance(data, dict):
        if isinstance(data.get("photos"), list):
            rows = data["photos"]
        else:
            for key, v in data.items():
                for item in (v if isinstance(v, list) else [v]):
                    if isinstance(item, dict):
                        item = dict(item)
                        item.setdefault("loc", key)
                        rows.append(item)
    elif isinstance(data, list):
        rows = data
    out = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        for key in (item.get("loc"), item.get("name"), item.get("id")):
            if key:
                out.setdefault(str(key), []).append(item)
    return out


def _photo_pool(state, pack) -> list:
    idx = _loc_index(pack)
    pool = []
    for i, e in enumerate(state.journal):
        portrait = (e["type"] == "纪念"
                    and str(e.get("title", "")).startswith("有你的照片"))
        if e["type"] != "风景" and not portrait:
            continue
        weather = state.weather_by_day.get(str(e["day"]), "")
        loc_name = (e.get("loc") or "").strip()
        loc = idx.get(loc_name) if e.get("city") in (None, "", pack["meta"]["city"]) \
            else None
        pool.append({
            "idx": i, "day": e["day"], "slot": e.get("slot", ""),
            "city": e.get("city") or pack["meta"]["city"],
            "loc": loc_name, "title": e.get("title", ""),
            "text": e.get("text", ""), "portrait": portrait,
            "loc_id": (loc or {}).get("id", ""),
            "hidden": bool(loc) and not loc.get("starter", False),
            "weather": weather,
            "wet": any(x in weather for x in ("雨", "雪")),
            "night": e.get("slot") == "夜晚",
        })
    return pool


def _select_photos(pool: list, limit: int = PHOTO_LIMIT):
    """≤6 全洗；>6 精选：雨雪夜 → 隐藏地点 → 每城至少一张 → 天序补足。"""
    if len(pool) <= limit:
        return list(pool), 0
    ranked = sorted(pool, key=lambda p: (
        not p["portrait"], not (p["wet"] or p["night"]), not p["hidden"],
        p["day"], p["idx"]))
    chosen = [p for p in ranked if p["portrait"]][:limit]
    cities = []
    for p in pool:
        if p["city"] not in cities:
            cities.append(p["city"])
    for city in cities:
        if len(chosen) >= limit:
            break
        if any(c["city"] == city for c in chosen):
            continue
        for p in ranked:
            if p["city"] == city and p not in chosen:
                chosen.append(p)
                break
    for p in ranked:
        if len(chosen) >= limit:
            break
        if p not in chosen:
            chosen.append(p)
    chosen = chosen[:limit]
    chosen.sort(key=lambda p: (p["day"], p["idx"]))
    return chosen, len(pool) - len(chosen)


def _text_box(item: dict, num: int = 0, total: int = 0) -> str:
    label = f"{item['loc'] or item['title']} · {item['slot']}".strip(" ·")
    if num and total > 1:
        tag = f" {num}/{total} "
        lines = ["╭─" + tag + "─" * (BODY_COLS - 1 - len(tag)) + "╮"]
    else:
        lines = ["╭" + "─" * BODY_COLS + "╮"]
    for ln in _wrap(item["text"]):
        lines.append("│ " + ln)
    tail = max(1, PAGE_W - 5 - _width(label))
    lines.append("╰─ " + label + " " + "─" * tail + "╯")
    return "\n".join(lines)


def _in_months(month, span) -> bool:
    """month 落在 [起月, 止月] 里吗——区间可跨年，如 [12, 2]。"""
    if not span:
        return True
    try:
        a, b = int(span[0]), int(span[1])
        m = int(month)
    except (TypeError, ValueError, IndexError):
        return True
    if not 1 <= m <= 12:
        return True
    return a <= m <= b if a <= b else (m >= a or m <= b)


def _weather_ok(hint, weather: str) -> bool:
    """weather_hint 与当天天气对不对得上。null（没写）= 全配。"""
    if not hint:
        return True
    text = weather or ""
    wet = any(x in text for x in ("雨", "雪"))
    if hint == "雨":
        return wet
    if hint == "雪":
        return "雪" in text
    if hint == "晴":
        return not wet
    return True


def _photo_fits(row: dict, item: dict, weather: str, month) -> bool:
    """时辰档案的门：这张照片能不能不撒谎地代表这条日记。

    照片没写档案（老式 manifest）就一律放行——档案是收紧，不是新增门槛。
    """
    if row.get("mismatch"):
        return False
    slots = row.get("compatible_slots")
    if isinstance(slots, (list, tuple)) and item["slot"] not in slots:
        return False
    if not _weather_ok(row.get("weather_hint"), weather):
        return False
    return _in_months(month, row.get("season_months"))


def _photo_line(item: dict, manifest: dict, slug: str, weather: str = "",
                month=0):
    """返回 (照片行, 用到的 manifest 条目)；过不了门就 ("", None)。"""
    rows = manifest.get(item["loc_id"]) or manifest.get(item["loc"]) or []
    rows = [r for r in rows if _photo_fits(r, item, weather, month)]
    if not rows:
        return "", None
    row = rows[0]
    for r in rows:                       # 时段对得上的优先
        if r.get("slot") and r["slot"] == item["slot"]:
            row = r
            break
    rel = row.get("path") or f"content/{slug}/photos/{row.get('file', '')}"
    caption = row.get("caption") or row.get("scene_note") or item["title"]
    credit = row.get("credit") or row.get("author") or "佚名"
    return f"[照片] {rel} ｜ {caption} ｜ 摄影：{credit}", row


def _photos_block(state, pack, photos: str):
    pool = _photo_pool(state, pack)
    chosen, rest = _select_photos(pool)
    lines = [RULE_PHOTOS, f"（你选了：{FORM_LABEL[photos]}）"]
    meta_rows, credits = [], []
    if not chosen:
        lines.append("")
        lines.append("这一程你一张也没拍。眼睛比相机记得久，也说不定。")
        return "\n".join(lines), meta_rows, credits
    manifests = {}
    n_chosen = len(chosen)
    for i, item in enumerate(chosen, 1):
        line, row = "", None
        if photos in ("real", "both"):
            slug = _slug_of_city(state, pack, item["city"])
            if slug not in manifests:
                manifests[slug] = _manifest(slug)
            line, row = _photo_line(item, manifests[slug], slug,
                                    item.get("weather", ""), state.month)
        form = photos if line else "text"    # 没有真实照片就回落文字框
        lines.append("")
        if line:
            lines.append(line)
            credits.append(row)
        if form != "real":
            lines.append(_text_box(item, num=i, total=n_chosen))
        meta_rows.append({"day": item["day"], "slot": item["slot"],
                          "city": item["city"], "loc": item["loc"],
                          "title": item["title"], "form": form})
    if rest > 0:
        lines.append("")
        lines.append(f"（其余 {rest} 张收在游记里）")
    return "\n".join(lines), meta_rows, credits


# ---------------------------------------------------------------- ⑥ 心愿段

def _wishes_block(state, pack) -> str:
    if not state.wishes:
        return ""
    done = [w for w in state.wishes if w["done"]]
    undone = [w for w in state.wishes if not w["done"]]
    lines = [RULE_WISHES]
    if not undone:
        lines.append(f"你抄了 {len(state.wishes)} 条心愿")
        lines.append("每一条都应了验")
        return "\n".join(lines)
    lines.append(f"你抄了 {len(state.wishes)} 条心愿，{len(done)} 条应了验")
    quoted = "".join(f"「{w['text']}」" for w in undone)
    if len(undone) == 1:
        lines.append(f"没应验的那条是{quoted}")
    else:
        lines.append(f"没应验的是{quoted}")
    cities = []
    for w in undone:
        name = _city_name_of_slug(state, pack, w.get("city", ""))
        if name and name not in cities:
            cities.append(name)
    where = "、".join(cities) or pack["meta"]["city"]
    lines.append(("它" if len(undone) == 1 else "它们") + f"现在是你回{where}的理由")
    return "\n".join(lines)


# ---------------------------------------------------------------- ⑦ 显影段

def _score_mod():
    try:
        from . import score
    except Exception:
        return None
    return score


def _dim_keys(result: dict) -> list:
    dims = (result or {}).get("dims")
    if isinstance(dims, dict):
        return [k for k, v in dims.items() if v]
    if isinstance(dims, (list, tuple)):
        out = []
        for d in dims:
            if isinstance(d, str):
                out.append(d)
            elif isinstance(d, (list, tuple)) and d:
                out.append(d[0])
            elif isinstance(d, dict) and d.get("key"):
                out.append(d["key"])
        return out
    return []


def _dye_rows(mod, keys: list, result: dict) -> list:
    rows = []
    table = {}
    for d in (getattr(mod, "DIMS", None) or []):
        if isinstance(d, (list, tuple)) and len(d) >= 3:
            table[d[0]] = d
    for k in keys:
        d = table.get(k)
        if d:
            rows.append(f"- {d[1]} · {d[2]}")
    if not rows:
        for lab in (result.get("labels") or []):
            rows.append(f"- {lab}")
    return rows


def _develop_block(state, pack):
    mod = _score_mod()
    result, color = {}, {}
    if mod is not None:
        try:
            result = mod.compute(state, pack) or {}
        except Exception:
            result = {}
        try:
            color = mod.blend(_dim_keys(result)) or {}
        except Exception:
            color = {}
    lines = [RULE_DEVELOP]
    grade = str(result.get("grade") or "").strip()
    if grade:
        lines.append(grade)
    lines.append("")
    lines.append("这趟旅行洗出来，是一种颜色")
    name = str(color.get("name") or "").strip()
    if name:
        lines.append(_center(name))
    line = str(color.get("line") or "").strip()
    if line:
        lines.append(line)
    all_doms = color.get("dominant") or []
    if all_doms:
        parts = []
        for d in all_doms:
            if isinstance(d, (list, tuple)) and len(d) >= 2:
                label, dye = d[0], d[1]
            elif isinstance(d, dict):
                label = d.get("label", "")
                dye = d.get("dye", d.get("name", ""))
            else:
                continue
            dye_short = dye[:-1] if dye.endswith("色") else dye
            parts.append(f"{label}的{dye_short}")
        if parts:
            lines.append("——" + "、".join(parts) + "，把它染成了这样")
    return "\n".join(lines), color, mod, result


# ---------------------------------------------------------------- ⑧⑨ 尾注与附录

def report_path(state, out_dir: Path = None) -> Path:
    base = Path(out_dir) if out_dir else SAVE_DIR
    return base / f"{state.trip_id}_旅行报告.md"


def _endnote(state, out_dir: Path = None) -> str:
    path = report_path(state, out_dir)
    try:
        shown = path.relative_to(ROOT).as_posix()
    except ValueError:
        shown = path.as_posix()
    return f"（已自动保存到 {shown}；附录见文件末尾）"


def _credit_rows(credits: list) -> list:
    """用到的真实照片：作者 / 许可 / 文件页——CC 的署名义务，一张也不能省。"""
    rows, seen = [], set()
    for row in credits:
        if not isinstance(row, dict):
            continue
        key = row.get("source") or row.get("path") or row.get("file")
        if key in seen:
            continue
        seen.add(key)
        who = row.get("author") or row.get("credit") or "佚名"
        parts = [str(row.get("title") or row.get("file") or "").strip(), who]
        lic = str(row.get("license") or "").strip()
        if lic:
            parts.append(lic)
        src = str(row.get("source") or "").strip()
        if src:
            parts.append(src)
        rows.append("- " + " ｜ ".join(p for p in parts if p))
    return rows


def _appendix(digest: str, mod, keys: list, result: dict,
              credits: list = None) -> str:
    lines = [RULE_APPENDIX, "", "【观察出处对照】（观察段能说的，全在这里）", digest]
    rows = _dye_rows(mod, keys, result) if mod is not None else []
    lines.append("")
    lines.append("【染料记录】")
    lines.extend(rows or ["- （这一程还没染上任何一味）"])
    credit_rows = _credit_rows(credits or [])
    if credit_rows:
        lines.append("")
        lines.append("【图片来源】")
        lines.extend(credit_rows)
    return "\n".join(lines)


# ---------------------------------------------------------------- 公开 API

def make_report(state, pack, observe: bool = False, photos: str = "text",
                out_dir: Path = None) -> dict:
    """洗出一份旅行报告。返回 {"text": 全文, "meta": {...}}。"""
    if photos not in FORM_LABEL:
        photos = "text"
    f = _facts(state, pack)
    digest = _facts_digest(state, pack, f)
    obs = _observe(digest) if observe else ""

    develop, color, mod, result = _develop_block(state, pack)
    photo_block, photo_meta, credits = _photos_block(state, pack, photos)

    blocks = [_cover(state, pack, f), _opening(state, pack, f)]
    if obs:
        blocks.append(RULE_OBSERVE + "\n\n" + obs)
    blocks.append(_timeline(state, pack, f, with_notes=not obs))
    blocks.append(photo_block)
    wishes = _wishes_block(state, pack)
    if wishes:
        blocks.append(wishes)
    blocks.append(develop)
    blocks.append(_endnote(state, out_dir))
    blocks.append(_appendix(digest, mod, _dim_keys(result), result, credits))

    text = "\n\n".join(b for b in blocks if b).rstrip() + "\n"
    return {"text": text,
            "meta": {"color": color, "photos": photo_meta, "observed": bool(obs)}}


def save_report(state, pack, observe: bool = False, photos: str = "text",
                out_dir: Path = None) -> Path:
    """写 saves/<trip_id>_旅行报告.md 与同名 .meta.json，返回 md 路径。"""
    rep = make_report(state, pack, observe=observe, photos=photos,
                      out_dir=out_dir)
    md = report_path(state, out_dir)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(rep["text"], encoding="utf-8")
    meta_path = md.parent / (md.stem + ".meta.json")
    meta_path.write_text(
        json.dumps(rep["meta"], ensure_ascii=False, indent=2), encoding="utf-8")
    return md
