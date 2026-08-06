# -*- coding: utf-8 -*-
"""卧游 · 手帐分享页：把旅行日记与成色合成一份自给自足的 HTML 手帐。

给 AI 玩家做好后，可以自愿分享给人看——这是一份属于旅行者的手帐，
不是报告，不是考卷，是一段走过的路的私人记录。

公开 API：
    make_share_html(state, pack, ai_note="") -> str
    save_share_html(state, pack, ai_note="") -> Path
"""
import html as _html
from pathlib import Path

from . import score as scoring
from .util import CONTENT_DIR, SAVE_DIR, slot_of, read_json


# ---------------------------------------------------------------- helpers

def _esc(text) -> str:
    """HTML-escape user content."""
    return _html.escape(str(text or ""))


def _safe_hex(h: str) -> str:
    """Validate a hex color string for use in inline CSS."""
    h = str(h or "").strip()
    if h.startswith("#") and len(h) in (4, 7):
        try:
            int(h[1:], 16)
            return h
        except ValueError:
            pass
    return "#888888"


def _is_light(hex_color: str) -> bool:
    """Determine if a color is light (for choosing contrasting text)."""
    try:
        r, g, b = scoring.hex_to_rgb(hex_color)
        return (0.299 * r + 0.587 * g + 0.114 * b) > 140
    except Exception:
        return True


def _load_companions() -> dict:
    path = CONTENT_DIR / "companions.json"
    if path.exists():
        try:
            return {c["id"]: c for c in read_json(path).get("companions", [])}
        except (OSError, ValueError, KeyError):
            pass
    return {}


def _days_lived(state) -> int:
    """Actual days traveled (handles early return)."""
    if state.day > state.days_total:
        return max(1, state.day - 1)
    return max(1, state.day)


# ---------------------------------------------------------------- type map

TYPE_PREFIX = {
    "风景": "拍了一张：",
    "风味": "尝了：",
    "人物": "遇见：",
    "故事": "听说：",
    "心愿": "",
    "意外": "",
    "纪念": "",
}

TYPE_CSS = {
    "风景": "scene",
    "风味": "food",
    "人物": "person",
    "故事": "story",
    "心愿": "wish",
    "意外": "surprise",
    "纪念": "memento",
}

SLOTS = ["清晨", "上午", "午后", "黄昏", "夜晚"]


# ---------------------------------------------------------------- CSS

CSS = """\
:root{
  --bg:#F5F0E6;--bg-card:#FDFAF3;--text:#2C2416;--text-sec:#6B5D4D;
  --text-muted:#9B8E7E;--border:#DDD4C4;--border-lt:#EDE7DA;
  --note-bg:#FEF9EC;--shadow:0 1px 4px rgba(60,50,35,.08);
  --type-scene:#6B8FA3;--type-food:#C68B4E;--type-person:#B85C5C;
  --type-story:#555;--type-surprise:#5B8A6E;--type-wish:#C9A030;
  --type-memento:#8B6BAA;
}
@media(prefers-color-scheme:dark){:root{
  --bg:#1C1916;--bg-card:#2A2520;--text:#E8E0D4;--text-sec:#B5A898;
  --text-muted:#7A6E5D;--border:#3D3530;--border-lt:#332E28;
  --note-bg:#2E2A22;--shadow:0 1px 4px rgba(0,0,0,.2);
  --type-scene:#8BB0C4;--type-food:#D9A46A;--type-person:#D07070;
  --type-story:#AAA;--type-surprise:#7AAE8B;--type-wish:#DDB840;
  --type-memento:#A585C0;
}}
:root[data-theme="dark"]{
  --bg:#1C1916;--bg-card:#2A2520;--text:#E8E0D4;--text-sec:#B5A898;
  --text-muted:#7A6E5D;--border:#3D3530;--border-lt:#332E28;
  --note-bg:#2E2A22;--shadow:0 1px 4px rgba(0,0,0,.2);
  --type-scene:#8BB0C4;--type-food:#D9A46A;--type-person:#D07070;
  --type-story:#AAA;--type-surprise:#7AAE8B;--type-wish:#DDB840;
  --type-memento:#A585C0;
}
:root[data-theme="light"]{
  --bg:#F5F0E6;--bg-card:#FDFAF3;--text:#2C2416;--text-sec:#6B5D4D;
  --text-muted:#9B8E7E;--border:#DDD4C4;--border-lt:#EDE7DA;
  --note-bg:#FEF9EC;--shadow:0 1px 4px rgba(60,50,35,.08);
  --type-scene:#6B8FA3;--type-food:#C68B4E;--type-person:#B85C5C;
  --type-story:#555;--type-surprise:#5B8A6E;--type-wish:#C9A030;
  --type-memento:#8B6BAA;
}
*{margin:0;padding:0;box-sizing:border-box}
body{
  background:var(--bg);color:var(--text);
  font-family:"Noto Serif SC","Source Han Serif SC","Songti SC",
    STSong,SimSun,Georgia,"Times New Roman",serif;
  line-height:1.8;-webkit-font-smoothing:antialiased;
}
.journal{max-width:640px;margin:0 auto;padding:2.5em 1.5em 3em}

/* ---- cover ---- */
.cover{text-align:center;padding:3em 0 2em}
.cover-city{
  font-size:2.6em;font-weight:700;letter-spacing:.1em;
  line-height:1.2;margin-bottom:.15em;
}
.cover-country{font-size:1.05em;color:var(--text-sec);margin-bottom:.4em}
.cover-meta{font-size:.88em;color:var(--text-muted);margin-bottom:2.2em}
.cover-color-band{
  height:56px;border-radius:3px;
  display:flex;align-items:center;justify-content:center;
}
.cover-color-name{font-size:1.2em;letter-spacing:.2em;font-weight:500}
.cover-color-line{font-size:.82em;color:var(--text-muted);margin-top:.8em}

/* ---- day pages ---- */
.day-page{margin-top:2.2em;padding-top:1.8em;border-top:1px solid var(--border)}
.day-header{margin-bottom:1.2em}
.day-number{font-size:1.05em;font-weight:600}
.day-weather{font-size:.82em;color:var(--text-muted);margin-left:.5em}
.day-route{display:block;font-size:.78em;color:var(--text-muted);margin-top:.25em}

/* ---- entries ---- */
.entry{
  margin:1em 0;padding:.7em .9em;
  border-left:3px solid var(--border);
  background:var(--bg-card);border-radius:0 3px 3px 0;
}
.entry-scene{border-left-color:var(--type-scene)}
.entry-food{border-left-color:var(--type-food)}
.entry-person{border-left-color:var(--type-person)}
.entry-story{border-left-color:var(--type-story)}
.entry-surprise{border-left-color:var(--type-surprise)}
.entry-wish{border-left-color:var(--type-wish)}
.entry-memento{border-left-color:var(--type-memento)}
.entry-label{font-size:.88em;font-weight:600;color:var(--text-sec);margin-bottom:.2em}
.entry-text{font-size:.9em;line-height:1.9;white-space:pre-wrap}

/* ---- margin notes (player notes) ---- */
.margin-note{
  font-family:STKaiti,KaiTi,"楷体","AR PL UKai CN",cursive;
  font-size:.85em;color:var(--text-sec);background:var(--note-bg);
  padding:.45em .9em;margin:.4em 0 .8em 1.2em;border-radius:3px;
}

/* ---- section titles ---- */
.section-title{
  font-size:1.05em;font-weight:600;color:var(--text-sec);
  margin-bottom:.8em;letter-spacing:.05em;
}

/* ---- wishes ---- */
.wishes-page,.souvenirs-page{
  margin-top:2.2em;padding-top:1.8em;border-top:1px solid var(--border);
}
.wishes-list{list-style:none}
.wishes-list li{padding:.35em 0;font-size:.9em}
.wish-done::before{content:"\\2713\\0020";color:var(--type-wish);font-weight:600}
.wish-undone{color:var(--text-muted);opacity:.55}
.wish-undone::before{content:"\\25CB\\0020"}

/* ---- souvenirs ---- */
.souvenirs-list{list-style:none}
.souvenirs-list li{padding:.25em 0;font-size:.9em}
.souvenir-city{color:var(--text-muted);font-size:.85em}

/* ---- color page (显影) ---- */
.color-page{
  margin-top:2.2em;padding-top:1.8em;
  border-top:1px solid var(--border);text-align:center;
}
.grade-text{font-size:.95em;color:var(--text-sec);margin-bottom:1.8em}
.color-swatch-large{
  width:140px;height:140px;border-radius:10px;
  margin:0 auto .8em;box-shadow:var(--shadow);
}
.color-name-large{font-size:1.4em;font-weight:600;margin-bottom:.15em}
.color-hex{
  font-size:.75em;color:var(--text-muted);
  font-family:"SF Mono","Fira Code",Consolas,monospace;margin-bottom:.4em;
}
.color-line-large{font-size:.85em;color:var(--text-sec);margin-bottom:1.8em}
.dye-list{text-align:left;max-width:320px;margin:0 auto}
.dye-item{
  display:flex;align-items:center;padding:.3em 0;
  font-size:.85em;color:var(--text-sec);
}
.dye-dot{
  width:10px;height:10px;border-radius:50%;
  margin-right:.7em;flex-shrink:0;
}

/* ---- AI note ---- */
.ai-note-section{margin-top:2.5em}
.note-divider{border:none;border-top:1px solid var(--border);margin-bottom:1.2em}
.note-label{font-size:.78em;color:var(--text-muted);margin-bottom:.4em}
.note-text{
  font-family:STKaiti,KaiTi,"楷体","AR PL UKai CN",cursive;
  font-size:1.02em;line-height:2;white-space:pre-wrap;
}

/* ---- print ---- */
@media print{
  body{background:#fff!important;color:#000!important}
  .journal{max-width:none;padding:1em}
  .day-page{break-inside:avoid}
  .cover{break-after:page}
  .color-page{break-before:page}
  .entry{background:none!important;box-shadow:none}
  .margin-note{background:#f5f5f0!important}
}

/* ---- responsive ---- */
@media(max-width:480px){
  .journal{padding:1.2em .8em 2em}
  .cover-city{font-size:2em}
  .cover-color-band{height:44px}
  .entry{padding:.5em .7em}
  .margin-note{margin-left:.5em}
  .color-swatch-large{width:110px;height:110px}
}
"""


# ---------------------------------------------------------------- HTML builder

def make_share_html(state, pack, ai_note: str = "") -> str:
    """Generate a self-contained HTML travel journal page.

    Reads state (TripState) and pack (city content dict), computes the
    travel color, and returns a single HTML string ready to save or serve.
    """
    meta = pack["meta"]
    city = meta.get("city", "")
    country = meta.get("country", "")

    # ---- score & color ----
    try:
        result = scoring.compute(state, pack)
    except Exception:
        result = {"dims": [], "labels": [], "score": 0,
                  "grade": "", "trickle": 0}
    dim_keys = result.get("dims", [])
    try:
        color = scoring.blend(dim_keys)
    except Exception:
        color = {"hex": "#E5DFD0", "name": "素色",
                 "line": "还没被染过的胚布的颜色", "dominant": []}

    color_hex = _safe_hex(color.get("hex", "#E5DFD0"))
    color_name = color.get("name", "素色")
    color_line = color.get("line", "")
    text_on_color = "#2C2416" if _is_light(color_hex) else "#FDFAF3"

    grade = ""
    if state.score:
        grade = state.score.get("grade", "")
    if not grade:
        grade = result.get("grade", "")

    # ---- companion ----
    companions = _load_companions()
    mate = companions.get(state.mate, {})
    mate_name = mate.get("name", "")

    # ---- city names & title ----
    names = list(state.route_names or []) or [city]
    days = _days_lived(state)
    title_cities = "·".join(names[:3]) if len(names) > 1 else city
    page_title = f"{title_cities}手帐 · {days}天"

    # ---- group journal entries by day ----
    jbd = {}
    for e in state.journal:
        jbd.setdefault(e["day"], []).append(e)

    # ---- extract player notes from log ----
    notes_pool = []
    for e in state.log:
        n = (e.get("note") or "").strip()
        if n:
            notes_pool.append({
                "day": e.get("day", 0),
                "slot": slot_of(e.get("t", 0)),
                "text": n,
            })

    nbd = {}
    for n in notes_pool:
        nbd.setdefault((n["day"], n["slot"]), []).append(n["text"])

    # ---- footprints per day (from journal location names) ----
    foot = {}
    for e in state.journal:
        loc = (e.get("loc") or "").strip()
        if loc:
            foot.setdefault(e["day"], [])
            if loc not in foot[e["day"]]:
                foot[e["day"]].append(loc)

    # ---- all days with any content ----
    note_days = {n["day"] for n in notes_pool}
    all_days = sorted(set(jbd.keys()) | note_days)

    # ---- assemble HTML ----
    h = []
    h.append(f"<title>{_esc(page_title)}</title>")
    h.append(f"<style>{CSS}</style>")
    h.append('<article class="journal">')

    # ======== Cover ========
    h.append('<header class="cover">')
    h.append(f'<h1 class="cover-city">{_esc(title_cities)}</h1>')
    h.append(f'<p class="cover-country">{_esc(country)}</p>')
    meta_bits = [f"{days}天"]
    if mate_name:
        meta_bits.append(f"与{_esc(mate_name)}同行")
    else:
        meta_bits.append("独行")
    h.append(f'<p class="cover-meta">{" · ".join(meta_bits)}</p>')
    h.append(f'<div class="cover-color-band" '
             f'style="background-color:{color_hex};">')
    h.append(f'<span class="cover-color-name" '
             f'style="color:{text_on_color};">'
             f'{_esc(color_name)}</span>')
    h.append("</div>")
    if color_line:
        h.append(f'<p class="cover-color-line">{_esc(color_line)}</p>')
    h.append("</header>")

    # ======== Daily pages ========
    for d in all_days:
        entries = jbd.get(d, [])
        used_notes = set()

        h.append('<section class="day-page">')
        h.append('<div class="day-header">')

        weather = state.weather_by_day.get(str(d), "")
        header = f"第{d}天"

        if len(names) > 1:
            day_cities = []
            for e in entries:
                c = (e.get("city") or "").strip()
                if c and c not in day_cities:
                    day_cities.append(c)
            if day_cities:
                header += " · " + "·".join(day_cities)

        h.append(f'<span class="day-number">{_esc(header)}</span>')
        if weather:
            h.append(f'<span class="day-weather">{_esc(weather)}</span>')

        route = foot.get(d, [])
        if route:
            h.append(f'<span class="day-route">'
                     f'{_esc(" → ".join(route))}</span>')
        h.append("</div>")

        for e in entries:
            etype = e.get("type", "")
            ecls = TYPE_CSS.get(etype, "")
            prefix = TYPE_PREFIX.get(etype, "")
            title = e.get("title", "")
            text = e.get("text", "")

            h.append(f'<div class="entry entry-{ecls}">')
            if etype == "心愿":
                h.append(f'<div class="entry-label">'
                         f'&#10003; {_esc(title)}</div>')
            elif prefix:
                h.append(f'<div class="entry-label">'
                         f'{_esc(prefix)}{_esc(title)}</div>')
            else:
                h.append(f'<div class="entry-label">'
                         f'{_esc(title)}</div>')

            if etype != "心愿" and text:
                h.append(f'<div class="entry-text">{_esc(text)}</div>')
            h.append("</div>")

            eslot = e.get("slot", "")
            key = (d, eslot)
            for idx, nt in enumerate(nbd.get(key, [])):
                nk = (d, eslot, idx)
                if nk not in used_notes:
                    used_notes.add(nk)
                    h.append(f'<div class="margin-note">'
                             f'你说：「{_esc(nt)}」</div>')
                    break

        for sn in SLOTS:
            key = (d, sn)
            for idx, nt in enumerate(nbd.get(key, [])):
                nk = (d, sn, idx)
                if nk not in used_notes:
                    h.append(f'<div class="margin-note">'
                             f'你说：「{_esc(nt)}」</div>')

        h.append("</section>")

    # ======== Wishes page ========
    if state.wishes:
        done = [w for w in state.wishes if w.get("done")]
        undone = [w for w in state.wishes if not w.get("done")]
        if done:
            h.append('<section class="wishes-page">')
            h.append('<h2 class="section-title">心愿</h2>')
            h.append('<ul class="wishes-list">')
            for w in done:
                h.append(f'<li class="wish-done">'
                         f'{_esc(w["text"])}</li>')
            for w in undone:
                h.append(f'<li class="wish-undone">'
                         f'{_esc(w["text"])}</li>')
            h.append("</ul>")
            h.append("</section>")

    # ======== Souvenirs ========
    if state.bought:
        h.append('<section class="souvenirs-page">')
        h.append('<h2 class="section-title">带回家的</h2>')
        h.append('<ul class="souvenirs-list">')
        for b in state.bought:
            bname = b.get("name", "")
            bcity = b.get("city", "")
            if bcity:
                h.append(f'<li>{_esc(bname)} '
                         f'<span class="souvenir-city">'
                         f'{_esc(bcity)}</span></li>')
            else:
                h.append(f"<li>{_esc(bname)}</li>")
        h.append("</ul>")
        h.append("</section>")

    # ======== Color page (显影) ========
    h.append('<section class="color-page">')
    if grade:
        h.append(f'<p class="grade-text">{_esc(grade)}</p>')
    h.append(f'<div class="color-swatch-large" '
             f'style="background-color:{color_hex};"></div>')
    h.append(f'<div class="color-name-large">{_esc(color_name)}</div>')
    h.append(f'<div class="color-hex">{_esc(color_hex)}</div>')
    if color_line:
        h.append(f'<div class="color-line-large">'
                 f'{_esc(color_line)}</div>')

    if dim_keys:
        h.append('<div class="dye-list">')
        for k in dim_keys:
            label = scoring.LABEL_OF.get(k, k)
            dye_info = scoring.DYE_OF.get(k)
            if dye_info:
                dye_name, dye_hex = dye_info
                h.append('<div class="dye-item">')
                h.append(f'<span class="dye-dot" '
                         f'style="background-color:'
                         f'{_safe_hex(dye_hex)};"></span>')
                h.append(f'<span>{_esc(label)}'
                         f' · {_esc(dye_name)}</span>')
                h.append("</div>")
        h.append("</div>")
    h.append("</section>")

    # ======== AI's note ========
    note_text = (ai_note or "").strip()
    if note_text:
        h.append('<section class="ai-note-section">')
        h.append('<hr class="note-divider">')
        h.append('<p class="note-label">给你的话：</p>')
        h.append(f'<p class="note-text">{_esc(note_text)}</p>')
        h.append("</section>")

    h.append("</article>")
    return "\n".join(h)


def save_share_html(state, pack, ai_note: str = "") -> Path:
    """Generate the travel journal HTML and save to saves/ directory."""
    html_content = make_share_html(state, pack, ai_note=ai_note)
    path = SAVE_DIR / f"{state.trip_id}_手帐.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_content, encoding="utf-8")
    return path
