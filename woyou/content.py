# -*- coding: utf-8 -*-
"""卧游 · 内容包：加载、校验、查询。

内容包 = content/<slug>/pack.json，schema 见 validate_pack 与 docs（README）。
引擎只通过这里访问内容，保证生成器与手写包遵守同一约定。
"""
from pathlib import Path

from .util import CONTENT_DIR, read_json, fuzzy_pick, slot_of

LOC_TYPES = {"temple", "shrine", "market", "street", "river", "park", "path",
             "viewpoint", "museum", "shop", "nightlife", "cafe", "landmark", "square",
             "mosque", "church", "canal", "harbor", "bath", "palace", "bridge",
             "garden", "ruins"}

TYPE_LABEL = {
    "temple": "寺院", "shrine": "祠堂", "market": "市场", "street": "街巷",
    "river": "河岸", "park": "园林", "path": "小径", "viewpoint": "眺望",
    "museum": "博物馆", "shop": "店铺", "nightlife": "夜色", "cafe": "咖啡",
    "landmark": "地标", "square": "广场",
    "mosque": "清真寺", "church": "教堂", "canal": "运河", "harbor": "港口",
    "bath": "浴场", "palace": "宫殿", "bridge": "桥", "garden": "庭园",
    "ruins": "遗迹",
}


def pack_dir(slug: str) -> Path:
    return CONTENT_DIR / slug


def load_pack(slug: str) -> dict:
    path = pack_dir(slug) / "pack.json"
    if not path.exists():
        raise FileNotFoundError(f"内容包不存在：{path}")
    pack = read_json(path)
    errs = validate_pack(pack)
    if errs:
        raise ValueError("内容包校验失败：\n- " + "\n- ".join(errs[:20]))
    _index(pack)
    return pack


def list_packs() -> list:
    out = []
    if not CONTENT_DIR.exists():
        return out
    for d in sorted(CONTENT_DIR.iterdir()):
        if (d / "pack.json").exists():
            try:
                meta = read_json(d / "pack.json").get("meta", {})
                out.append({"slug": d.name, "city": meta.get("city"),
                            "country": meta.get("country")})
            except Exception:
                out.append({"slug": d.name, "city": "?", "country": "?"})
    return out


def _index(pack: dict) -> None:
    pack["_loc"] = {l["id"]: l for l in pack["locations"]}
    pack["_dist"] = {d["id"]: d for d in pack["districts"]}
    pack["_npc"] = {n["id"]: n for n in pack.get("npcs", [])}
    pack["_dish"] = {d["id"]: d for d in pack.get("dishes", [])}
    pack["_wish"] = {w["id"]: w for w in pack.get("wishes", [])}


# ---------------- 校验 ----------------

def validate_pack(pack: dict) -> list:
    """轻量结构校验，返回错误信息列表（空 = 通过）。"""
    errs = []

    def need(d, key, typ, where):
        if key not in d:
            errs.append(f"{where} 缺少字段 {key}")
            return None
        if typ and not isinstance(d[key], typ):
            errs.append(f"{where}.{key} 类型应为 {typ}")
            return None
        return d[key]

    if not isinstance(pack, dict):
        return ["pack 必须是 JSON 对象"]
    meta = need(pack, "meta", dict, "pack")
    if meta:
        for k in ("slug", "city", "country", "currency_symbol", "start_loc", "intro", "city_brief"):
            need(meta, k, None, "meta")
        for k in ("hotel_rate", "transit_fare", "default_budget"):
            if k in meta and not isinstance(meta[k], int):
                errs.append(f"meta.{k} 应为整数")

    districts = need(pack, "districts", list, "pack") or []
    dist_ids = set()
    for d in districts:
        if isinstance(d, dict) and "id" in d:
            dist_ids.add(d["id"])
            need(d, "name", None, f"district[{d.get('id')}]")

    locs = need(pack, "locations", list, "pack") or []
    loc_ids = set()
    starters = 0
    for l in locs:
        if not isinstance(l, dict):
            errs.append("locations 中有非对象项")
            continue
        lid = l.get("id")
        where = f"location[{lid}]"
        for k in ("id", "name", "district", "type", "brief"):
            need(l, k, None, where)
        if lid:
            if lid in loc_ids:
                errs.append(f"{where} id 重复")
            loc_ids.add(lid)
        if l.get("district") not in dist_ids:
            errs.append(f"{where} district 未定义: {l.get('district')}")
        if l.get("type") not in LOC_TYPES:
            errs.append(f"{where} type 非法: {l.get('type')}")
        look = l.get("look")
        if not isinstance(look, dict) or "default" not in look:
            errs.append(f"{where} look 需要至少含 default")
        if l.get("starter"):
            starters += 1
        for i, ex in enumerate(l.get("explore", [])):
            if not isinstance(ex, dict) or "text" not in ex:
                errs.append(f"{where}.explore[{i}] 需要 text")
    if starters < 3:
        errs.append("starter 地点少于 3 个（初始地图太空）")

    meta = pack.get("meta", {})
    if isinstance(meta, dict) and meta.get("start_loc") not in loc_ids:
        errs.append(f"meta.start_loc 未定义: {meta.get('start_loc')}")

    for n in pack.get("npcs", []):
        where = f"npc[{n.get('id')}]"
        for k in ("id", "name", "loc", "meet", "persona"):
            need(n, k, None, where)
        if n.get("loc") not in loc_ids:
            errs.append(f"{where} loc 未定义: {n.get('loc')}")
        st = n.get("story")
        if st and ("text" not in st or "title" not in st):
            errs.append(f"{where}.story 需要 title/text")

    for d in pack.get("dishes", []):
        where = f"dish[{d.get('id')}]"
        for k in ("id", "name", "price", "text"):
            need(d, k, None, where)
        for lid in d.get("locs", []):
            if lid not in loc_ids:
                errs.append(f"{where} locs 引用未定义地点 {lid}")

    for e in pack.get("events", []):
        where = f"event[{e.get('id')}]"
        for k in ("id", "text", "chance"):
            need(e, k, None, where)

    for w in pack.get("wishes", []):
        where = f"wish[{w.get('id')}]"
        for k in ("id", "text", "check"):
            need(w, k, None, where)
        chk = w.get("check")
        if isinstance(chk, dict):
            if chk.get("type") not in {"look", "visit", "eat", "story", "photo",
                                       "buy", "gem", "discovery", "explore",
                                       "npc", "rest", "listen", "join",
                                       "wander", "postcard"}:
                errs.append(f"{where}.check.type 非法: {chk.get('type')}")
        else:
            errs.append(f"{where}.check 应为对象")
    if len(pack.get("wishes", [])) < 5:
        errs.append("wishes 少于 5 条（不够抽心愿单）")

    # reveal 引用检查
    npc_ids = {n.get("id") for n in pack.get("npcs", [])}
    def check_reveal(rv, where):
        if not rv:
            return
        if "loc" in rv and rv["loc"] not in loc_ids:
            errs.append(f"{where} reveal.loc 未定义: {rv['loc']}")
        if "npc" in rv and rv["npc"] not in npc_ids:
            errs.append(f"{where} reveal.npc 未定义: {rv['npc']}")
    for l in locs:
        if isinstance(l, dict):
            for i, ex in enumerate(l.get("explore", [])):
                if isinstance(ex, dict):
                    check_reveal(ex.get("reveal"), f"location[{l.get('id')}].explore[{i}]")
    for n in pack.get("npcs", []):
        if n.get("story"):
            check_reveal(n["story"].get("reveal"), f"npc[{n.get('id')}].story")
        for i, tp in enumerate(n.get("topics", [])):
            if isinstance(tp, dict):
                check_reveal(tp.get("reveal"), f"npc[{n.get('id')}].topics[{i}]")
    return errs


# ---------------- 查询 ----------------

def get_loc(pack: dict, loc_id: str) -> dict:
    return pack["_loc"][loc_id]


def get_district(pack: dict, dist_id: str) -> dict:
    return pack["_dist"][dist_id]


def resolve_loc(pack: dict, query: str, known: list):
    """在已知地点里模糊匹配名字，返回 loc_id 或 None。"""
    cands = []
    for lid in known:
        l = pack["_loc"].get(lid)
        if l:
            names = [l["id"], l["name"], l.get("name_local", "")] + list(l.get("aliases", []))
            cands.append((lid, names))
    return fuzzy_pick(query, cands)


def loc_open(loc: dict, t: int) -> bool:
    hours = loc.get("hours")
    if not hours:
        return True
    return hours[0] <= t <= hours[1]


def hours_text(loc: dict) -> str:
    hours = loc.get("hours")
    if not hours:
        return "全天"
    return f"{slot_of(hours[0])}—{slot_of(hours[1])}"


def pick_text(block, slot: str, weather: str) -> str:
    """look/photo 这类多变体文本的选取：天气变体优先，其次时段，最后 default。

    约定 key：default / morning(清晨) / forenoon(上午) / afternoon(午后)
              / dusk(黄昏) / night(夜晚) / rain(雨天)
    """
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    slot_key = {"清晨": "morning", "上午": "forenoon", "午后": "afternoon",
                "黄昏": "dusk", "夜晚": "night"}.get(slot, "default")
    rainy = any(x in (weather or "") for x in ("雨", "雪"))
    if rainy and "rain" in block:
        return block["rain"]
    if slot_key in block:
        return block[slot_key]
    return block.get("default", "")


def npcs_at(pack: dict, loc_id: str, t: int) -> list:
    """当前时刻在场的 NPC 列表。"""
    out = []
    for n in pack.get("npcs", []):
        if n.get("loc") != loc_id:
            continue
        slots = n.get("slots")
        if slots and not (slots[0] <= t <= slots[1]):
            continue
        out.append(n)
    return out


def dishes_at(pack: dict, loc_id: str) -> list:
    out = []
    loc = pack["_loc"].get(loc_id, {})
    for did in loc.get("dishes", []):
        if did in pack["_dish"]:
            out.append(pack["_dish"][did])
    for d in pack.get("dishes", []):
        if loc_id in d.get("locs", []) and d not in out:
            out.append(d)
    return out
