# -*- coding: utf-8 -*-
"""卧游 · 旅行日记与游记导出（原始条目版；报告体见 report.py）。"""
from pathlib import Path

from .util import ROOT, slot_of

TYPE_MARK = {"风景": "🏞", "风味": "🍜", "人物": "👤", "故事": "📜",
             "意外": "✨", "心愿": "🎐", "纪念": "🎁"}


def add_entry(state, etype: str, title: str, text: str,
              loc_name: str = "", city: str = "",
              seq: int = 0, via: str = "") -> dict:
    entry = {
        "seq": seq,
        "day": state.day,
        "slot": slot_of(state.t),
        "city": city,
        "loc": loc_name,
        "type": etype,
        "title": title,
        "text": text.strip(),
    }
    if via:
        entry["via"] = via
    state.journal.append(entry)
    return entry


def journal_brief(state, last: int = 12) -> str:
    if not state.journal:
        return "日记本还是空的。去看看、走走、和人聊聊吧。"
    lines = []
    total = len(state.journal)
    if state.ended:
        entries = state.journal
    else:
        entries = state.journal[-last:]
    if not state.ended and total > last:
        lines.append(f"（共 {total} 条，只列最近 {last} 条；export 导出的游记是全的）")
    for e in entries:
        mark = TYPE_MARK.get(e["type"], "·")
        lines.append(f"{mark} 第{e['day']}天·{e['slot']}｜{e['title']}")
    last_note = None
    pn = getattr(state, "player_notes", None) or []
    if pn:
        last_note = pn[-1].get("text", "").strip() if isinstance(pn[-1], dict) else str(pn[-1]).strip()
    else:
        for e in reversed(state.log):
            note = (e.get("note") or "").strip()
            if note:
                last_note = note
                break
    if last_note:
        lines.append(f"\n✎ 最近一条自语：{last_note}")
    return "\n".join(lines)


def export_markdown(state, pack, out_path: Path = None) -> Path:
    meta = pack["meta"]
    multi = len(state.route) > 1
    title = "行旅记" if multi else f"{meta['city']}游记"
    lines = [f"# {title}", ""]
    names = state.route_names or state.route
    where = " → ".join(names) if multi else f"{meta['country']} · {meta['city']}"
    lines.append(f"> {where}，{min(state.day, state.days_total)} 天。")
    lines.append("")

    raw_notes = getattr(state, "player_notes", None) or []
    if not raw_notes:
        # backward compat: old saves without player_notes
        for l in state.log:
            if l.get("note"):
                raw_notes.append({"day": l.get("day", 0), "seq": 0, "text": l["note"]})

    # Build unified timeline items
    items = []
    for e in state.journal:
        items.append({"kind": "entry", "seq": e.get("seq", 0), "day": e["day"], "data": e})
    end_seq = None
    if state.ended and state.journal:
        end_seq = max(e.get("seq", 0) for e in state.journal)
    for n in raw_notes:
        if state.ended:
            n_seq = n.get("seq", 0)
            if end_seq is not None and n_seq > 0 and n_seq > end_seq:
                continue
            if n_seq == 0 and n.get("day", 0) > min(state.day, state.days_total):
                continue
        items.append({"kind": "note", "seq": n.get("seq", 0), "day": n["day"], "data": n})

    for sm in (getattr(state, "share_messages", None) or []):
        items.append({"kind": "share", "seq": sm.get("seq", 0), "day": sm["day"], "data": sm})

    items.sort(key=lambda x: (x["day"], x["seq"]))

    # Group by day
    from itertools import groupby
    for d, day_items in groupby(items, key=lambda x: x["day"]):
        weather = state.weather_by_day.get(str(d), "")
        day_items = list(day_items)
        cities = []
        for item in day_items:
            if item["kind"] == "entry":
                c = item["data"].get("city", "")
                if c and c not in cities:
                    cities.append(c)
        head = f"## 第{d}天"
        if cities:
            head += " · " + "·".join(cities)
        if weather:
            head += f" · {weather}"
        lines.append(head)
        lines.append("")

        for item in day_items:
            if item["kind"] == "entry":
                e = item["data"]
                mark = TYPE_MARK.get(e["type"], "·")
                where = f"（{e['loc']}）" if e.get("loc") else ""
                lines.append(f"**{mark} {e['slot']} · {e['title']}**{where}")
                lines.append("")
                lines.append(e["text"])
                if e.get("message"):
                    lines.append(f"背面你只写了一句：「{e['message']}」")
                lines.append("")
            elif item["kind"] == "share":
                lines.append(f"🖋 给ta的话：{item['data']['text']}")
                lines.append("")
            elif item["kind"] == "note":
                n = item["data"]
                text = n.get("text", "") if isinstance(n, dict) else str(n)
                note_lines = text.split("\n")
                lines.append(f"> 我说：{note_lines[0]}")
                for extra in note_lines[1:]:
                    lines.append(f"> {extra}")
                lines.append("")

    if state.wishes:
        lines.append("## 心愿单")
        lines.append("")
        done = [w for w in state.wishes if w["done"]]
        undone = [w for w in state.wishes if not w["done"]]
        for w in done:
            suffix = f"（第{w['day']}天）" if w.get("day") else ""
            lines.append(f"- ✅ {w['text']}{suffix}")
        if undone:
            lines.append("")
            lines.append("留给下次的——旅行没做完的事，是这座城发给你的回程票：")
            lines.append("")
            for w in undone:
                lines.append(f"- 🌱 {w['text']}")
        lines.append("")

    if state.bought:
        lines.append("## 带回家的东西")
        lines.append("")
        for b in state.bought:
            lines.append(f"- {b['name']}（{b.get('city', '')}）")
        lines.append("")

    # Finale page
    if state.ended:
        from . import report as report_mod
        fd = report_mod.build_finale_data(state, pack)
        lines.append("## 旅程末页")
        lines.append("")

        if fd["color_name"]:
            lines.append("这趟旅行洗出来，是一种颜色")
            lines.append(f"**{fd['color_name']}**")
            if fd["color_line"]:
                lines.append(f"> {fd['color_line']}")
            lines.append("")

        if fd["dye_summary_parts"]:
            lines.append("——" + "、".join(fd["dye_summary_parts"]) + "，把它染成了这样")
            lines.append("")

        if fd["dye_rows"]:
            for row in fd["dye_rows"]:
                lines.append(row)
            lines.append("")

        lines.append("*一期一会。*")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*这是旅途的回忆手帐——记下来的是留住的部分。*")
    lines.append("*觉得哪里想改，直接打开这个文件动手就好。*")
    lines.append("")

    out_path = out_path or (ROOT / "saves" / f"{state.trip_id}_游记.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
