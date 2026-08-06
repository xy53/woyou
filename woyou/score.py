# -*- coding: utf-8 -*-
"""卧游 · 终局成色与显影调色。

计分哲学：布尔成色反刷量——第五份甜点不加分；坐两次黄昏的河岸与买五件
纪念品等值。每一种「成色」只问有没有，不问几次；量的那一面只留「日记涓滴」
一小截（至多 15 分）。所以一趟旅行的回味值是它的宽度，不是它的次数——
把同一件事做到第五遍，只会让日记厚一点，不会让分数好看一点。

显影：每种成色对应一味染料。把达成的染料等权调和，得到这趟旅行的颜色，
再在传统色谱里认出离它最近的那一个——旅行洗出来是什么色，由你走过的
成色决定，不由你走过的次数决定。
"""

# ---------------------------------------------------------------- 成色

# (key, label, dye_name, dye_hex)
DIMS = [
    ("story",     "一个听来的故事",   "墨色",   "#2B2B2B"),
    ("gem",       "一处深巷的发现",   "苔色",   "#69821B"),
    ("dish",      "当地的味道",       "饴色",   "#DEB068"),
    ("join",      "一次入乡随俗",     "朱色",   "#EB6101"),
    ("friend",    "一位熟起来的人",   "茜色",   "#B7282E"),
    ("habit",     "一个回访的老位置", "黛蓝",   "#3A4B6B"),
    ("bought",    "一件带回家的东西", "胭脂",   "#9D2933"),
    ("photo",     "一张拍下的景",     "银鼠",   "#91989F"),
    ("wish",      "抄下的心愿",       "柑子色", "#F6AD49"),
    ("weathered", "雨或夜里的景",     "青灰",   "#6B7D7D"),
    ("multicity", "第二座城",         "群青",   "#4C6CB3"),
]

LABEL_OF = {k: label for k, label, _, _ in DIMS}
DYE_OF = {k: (dye, hexv) for k, _, dye, hexv in DIMS}
DIM_ORDER = [k for k, _, _, _ in DIMS]
SHORT_OF = {
    "story": "故事", "gem": "发现", "dish": "味道", "join": "入乡",
    "friend": "熟人", "habit": "回访", "bought": "礼物", "photo": "光影",
    "wish": "心愿", "weathered": "雨夜", "multicity": "远行",
}

POINT_PER_DIM = 12          # 每种成色的定价：都一样，因为它们都只发生一次
TRICKLE_CAP = 15            # 日记涓滴的封顶：量只能贡献这么多

GRADES = [
    (30,  "走马观花——但至少，马和花都是真的。"),
    (60,  "不虚此行。"),
    (105, "满载而归，行李超重的是回忆。"),
    (None, "一期一会。这一程，值得讲很多年。"),
]


# ---------------------------------------------------------------- 判定

def _boxes(state):
    """所有城市的分容器（跨城旅行的成色是全程共享的）。"""
    return list((getattr(state, "cities", None) or {}).values())


def _has_story(state, pack):
    return len(getattr(state, "stories_heard", []) or []) >= 1


def _has_gem(state, pack):
    return int(getattr(state, "gems", 0) or 0) >= 1


def _has_dish(state, pack):
    return len(getattr(state, "dishes_tried", []) or []) >= 1


def _has_join(state, pack):
    return any(box.get("joined") for box in _boxes(state))


def _has_friend(state, pack):
    """熟起来：同一个人聊过两次以上——第一次是搭话，第二次才是认识。"""
    for box in _boxes(state):
        for n in (box.get("talked") or {}).values():
            if int(n or 0) >= 2:
                return True
    return False


def _has_habit(state, pack):
    """老位置：同一地点、同一时辰，跨了两天以上。"""
    for box in _boxes(state):
        for vis in (box.get("visited") or {}).values():
            for days in (vis.get("slot_days") or {}).values():
                if len(days or []) >= 2:
                    return True
    return False


def _has_bought(state, pack):
    return bool(getattr(state, "bought", None))


def _has_photo(state, pack):
    return any(e.get("type") == "风景" for e in getattr(state, "journal", []) or [])


def _has_wish(state, pack):
    return bool(getattr(state, "wishes", None))


def _weathered_key(key: str) -> bool:
    """看/听的键形如「时段|天气」：夜里的景，或雨雪里的景。"""
    slot, _, weather = str(key).partition("|")
    return slot == "夜晚" or any(x in weather for x in ("雨", "雪"))


def _has_weathered(state, pack):
    for box in _boxes(state):
        for vis in (box.get("visited") or {}).values():
            for field in ("looked", "heard"):
                for key in vis.get(field) or []:
                    if _weathered_key(key):
                        return True
    return False


def _has_multicity(state, pack):
    return len(getattr(state, "route", []) or []) > 1


CHECKS = {
    "story": _has_story,
    "gem": _has_gem,
    "dish": _has_dish,
    "join": _has_join,
    "friend": _has_friend,
    "habit": _has_habit,
    "bought": _has_bought,
    "photo": _has_photo,
    "wish": _has_wish,
    "weathered": _has_weathered,
    "multicity": _has_multicity,
}


def grade_of(score: int) -> str:
    for cut, text in GRADES:
        if cut is None or score < cut:
            return text
    return GRADES[-1][1]


def compute(state, pack=None) -> dict:
    """算这趟旅行的成色与回味值。

    成色是布尔的：达成多少次都只算一次，所以刷不出分来。
    回味值 = 成色数 × 12 + 日记涓滴（min(15, 日记条数)）。
    """
    dims = [k for k in DIM_ORDER if CHECKS[k](state, pack)]
    labels = [LABEL_OF[k] for k in dims]
    trickle = min(TRICKLE_CAP, len(getattr(state, "journal", []) or []))
    score = len(dims) * POINT_PER_DIM + trickle
    return {"dims": dims, "labels": labels, "score": score,
            "grade": grade_of(score), "trickle": trickle}


# ---------------------------------------------------------------- 显影

# (name, hex, line)：色名与色话是定稿创作
NAMED_COLORS = [
    ("素色",     "#E5DFD0", "还没被染过的胚布的颜色"),
    ("檀色",     "#B77B57", "旧木头被手摸久了的颜色"),
    ("暮山紫",   "#8A7BA8", "天黑前山退到最远处的颜色"),
    ("东方既白", "#D6E4E5", "夜熬到头、天先开口的颜色"),
    ("天水碧",   "#86B69A", "雨把湖气揉进天光的颜色"),
    ("月白",     "#E9F1F6", "月亮落在纸窗上的颜色"),
    ("竹青",     "#789262", "新竹还没经过冬天的颜色"),
    ("藕荷",     "#B4A4CB", "荷塘收场时最后一点粉的颜色"),
    ("秋香色",   "#C8B575", "桂花落在旧席上的颜色"),
    ("黛",       "#4A4E5A", "远山被暮色收编的颜色"),
    ("胭脂水",   "#E198B4", "胭脂在水里散开一半的颜色"),
    ("十样锦",   "#EEB8C3", "织锦里挑不出主角的颜色"),
    ("雨过天青", "#7FB2C6", "雨停那一刻天先亮出来的颜色"),
    ("缃色",     "#F0C239", "新麦晒足了太阳的颜色"),
    ("绾",       "#A98175", "旧绳结解开后留在掌心的颜色"),
    ("苍苔",     "#5E7A55", "老石阶背阴面的颜色"),
    ("酡颜",     "#D9836F", "微醺的人脸颊上的颜色"),
    ("霜色",     "#E9EDF0", "清晨第一个出门的人看见的颜色"),
    ("琥珀",     "#CA6924", "时间凝住不走的颜色"),
    ("螺子黛",   "#5A4B63", "古人拿来画眉的青黑色"),
    ("沉香",     "#6E5B48", "香烧尽之后木头记得的颜色"),
    ("天缥",     "#C6E6E8", "最浅的那一种天色"),
    ("赭石",     "#955539", "山壁被夕照咬住的颜色"),
    ("鸦青",     "#424C50", "乌鸦翅膀借走夜色的颜色"),
]

BLANK = NAMED_COLORS[0]     # 素色：一趟什么也没染上的旅行


def hex_to_rgb(h: str) -> tuple:
    h = str(h).lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(c))):02X}" for c in rgb)


def _dist2(a, b) -> int:
    return sum((int(x) - int(y)) ** 2 for x, y in zip(a, b))


def nearest_named(rgb) -> tuple:
    """在传统色谱里找离这块颜色最近的一个（欧氏距离，同距取谱中先出现的）。"""
    return min(NAMED_COLORS, key=lambda c: _dist2(rgb, hex_to_rgb(c[1])))


def blend(dim_keys) -> dict:
    """把达成的成色染料调和成这趟旅行的颜色。

    等权平均——因为每种成色本来就等价。返回调和色、离它最近的传统色名与
    色话，以及最贴近这块颜色的至多三味染料（「主色」，说明它像什么）。
    """
    keys, seen = [], set()
    for k in DIM_ORDER:                      # 顺序固定 → 结果可复现
        if k in (dim_keys or []) and k not in seen:
            seen.add(k)
            keys.append(k)
    if not keys:
        name, hexv, line = BLANK
        return {"hex": hexv, "name": name, "line": line, "dominant": []}

    rgbs = [hex_to_rgb(DYE_OF[k][1]) for k in keys]
    mixed = tuple(round(sum(c[i] for c in rgbs) / len(rgbs)) for i in range(3))
    name, _, line = nearest_named(mixed)
    ranked = sorted(keys, key=lambda k: _dist2(mixed, hex_to_rgb(DYE_OF[k][1])))
    top = ranked[:3]
    dominant = [(SHORT_OF.get(k, LABEL_OF[k]), DYE_OF[k][0]) for k in top]
    return {"hex": rgb_to_hex(mixed), "name": name, "line": line,
            "dominant": dominant}
