# -*- coding: utf-8 -*-
"""卧游 · 旅行日记与游记导出（原始条目版；报告体见 report.py）。"""
from pathlib import Path

from .util import ROOT, slot_of

TYPE_MARK = {"风景": "🏞", "风味": "🍜", "人物": "👤", "故事": "📜",
             "意外": "✨", "心愿": "🎐", "纪念": "🎁"}


def add_entry(state, etype: str, title: str, text: str,
              loc_name: str = "", city: str = "") -> dict:
    entry = {
        "day": state.day,
        "slot": slot_of(state.t),
        "city": city,
        "loc": loc_name,
        "type": etype,
        "title": title,
        "text": text.strip(),
    }
    state.journal.append(entry)
    return entry


def journal_brief(state, last: int = 12) -> str:
    if not state.journal:
        return "日记本还是空的。去看看、走走、和人聊聊吧。"
    lines = []
    total = len(state.journal)
    entries = state.journal[-last:]
    if total > last:
        lines.append(f"（共 {total} 条，只列最近 {last} 条；export 导出的游记是全的）")
    for e in entries:
        mark = TYPE_MARK.get(e["type"], "·")
        lines.append(f"{mark} 第{e['day']}天·{e['slot']}｜{e['title']}")
    return "\n".join(lines)


def export_markdown(state, pack, out_path: Path = None) -> Path:
    meta = pack["meta"]
    multi = len(state.route) > 1
    title = "行旅记" if multi else f"{meta['city']}游记"
    lines = [f"# {title}", ""]
    names = state.route_names or state.route
    where = " → ".join(names) if multi else f"{meta['country']} · {meta['city']}"
    lines.append(f"> {where}，{min(state.day, state.days_total)} 天。")
    if state.ended and state.score:
        s = state.score
        lines.append(f"> 回味值 **{s.get('total', 0)}** —— {s.get('grade', '')}")
    lines.append("")

    by_day = {}
    for e in state.journal:
        by_day.setdefault(e["day"], []).append(e)
    notes_by_day = {}
    for l in state.log:
        if l.get("note"):
            notes_by_day.setdefault(l["day"], []).append(l["note"])

    for d in sorted(set(by_day) | set(notes_by_day)):
        weather = state.weather_by_day.get(str(d), "")
        cities = []
        for e in by_day.get(d, []):
            if e.get("city") and e["city"] not in cities:
                cities.append(e["city"])
        head = f"## 第{d}天"
        if cities:
            head += " · " + "·".join(cities)
        if weather:
            head += f" · {weather}"
        lines.append(head)
        lines.append("")
        for e in by_day.get(d, []):
            mark = TYPE_MARK.get(e["type"], "·")
            where = f"（{e['loc']}）" if e.get("loc") else ""
            lines.append(f"**{mark} {e['slot']} · {e['title']}**{where}")
            lines.append("")
            lines.append(e["text"])
            lines.append("")
        margin = notes_by_day.get(d, [])
        if margin:
            lines.append("路上的自语——")
            lines.append("")
            for n in margin:
                lines.append(f"> 🗨 {n}")
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

    out_path = out_path or (ROOT / "saves" / f"{state.trip_id}_游记.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
