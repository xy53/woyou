# -*- coding: utf-8 -*-
"""卧游 (Woyou) · 命令行入口。

  uv run play.py                    交互游玩（无旅程时进开局向导）
  uv run play.py demo               直接在京都开一段离线旅程（不需要 API）
  uv run play.py new --city 京都 [--days 5 --budget 62000 --mate aman --seed 7]
  uv run play.py build --city 奈良  预先为某座城做调研与内容生成（需要 API）
  uv run play.py cmd <命令...>      对当前旅程执行一条命令
  uv run play.py export             导出游记
  uv run play.py report             重新显示旅程结算
  uv run play.py watch [--port N]   打开浏览器观战页
  uv run play.py trips / packs      列出旅程存档 / 已生成的城市
"""
import argparse
import io
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from woyou import content, llm  # noqa: E402
from woyou.engine import Trip  # noqa: E402
from woyou.state import active_trip_id, list_trips, load_state  # noqa: E402
from woyou.util import fuzzy_pick, slot_of, SAVE_DIR  # noqa: E402

MATES = {"aman": "阿满（吃货发小）", "yanqiu": "砚秋（历史控学长）",
         "xiaoqi": "小柒（摄影系妹妹）"}

INSPIRATION = """✈ 一点灵感（先玩已备好的城市；新城市要在旅程外用 build 制作）——
  东亚：京都✓ 奈良 东京 大阪 ｜ 中国：泉州 大理 苏州 喀什
  东南亚·南亚：清迈 曼谷 会安 瓦拉纳西 ｜ 中亚·西亚：撒马尔罕 伊斯坦布尔 伊斯法罕
  欧洲：威尼斯 佛罗伦萨 格拉纳达 波尔图 布拉格 爱丁堡 巴黎 布达佩斯
  非洲：马拉喀什 非斯 开罗 卢克索 ｜ 美洲：瓦哈卡 库斯科 哈瓦那 新奥尔良
  大洋洲：墨尔本 皇后镇   （✓ = 已备好，离线可玩）"""


def resolve_city(query: str, allow_build: bool = True, force: bool = False):
    """城市名 → 内容包 slug；没有已备好的包就提示去哪儿制作。"""
    packs = content.list_packs()
    cands = [(p["slug"], [p["slug"], p.get("city") or ""]) for p in packs]
    slug = fuzzy_pick(query, cands)
    if slug:
        return slug
    print("该城市的旅行内容尚未安装。")
    print("运行 `uv run play.py packs` 查看已有城市。")
    print(f"制作新城市可在旅程外运行：uv run play.py build --city {query}")
    return None


_WATCH_HINT = "💡 想旁观旅行？另开终端：uv run play.py watch"


def start_trip(args) -> Trip | None:
    slug = resolve_city(args.city)
    if not slug:
        return None
    trip = Trip.new(slug, days=args.days, budget=args.budget,
                    seed=args.seed, month=args.month, mate=args.mate or "")
    print(trip.opening())
    print(_WATCH_HINT, file=sys.stderr)
    return trip


def wizard() -> Trip | None:
    print("═══ 卧游 · 开局 ═══")
    print(INSPIRATION)
    try:
        city = input("\n想去哪座城？ > ").strip()
        if not city:
            print("下次想好目的地再出发吧。")
            return None
        slug = resolve_city(city)
        if not slug:
            return None
        pack = content.load_pack(slug)
        meta = pack["meta"]
        d = input(f"玩几天？（回车默认 {meta.get('default_days', 5)} 天，可中途 end trip 提前回程） > ").strip()
        days = int(d) if d.isdigit() else None
        b = input(f"预算多少{meta.get('currency', '')}？（回车默认 "
                  f"{meta['currency_symbol']}{meta.get('default_budget', 0):,}） > ").strip()
        budget = int(b) if b.isdigit() else None
        print("带旅游搭子吗？  0 独行   1 阿满（吃货发小）   2 砚秋（历史控学长）   3 小柒（摄影系妹妹）")
        m = input("选一个 > ").strip()
        mate = {"1": "aman", "2": "yanqiu", "3": "xiaoqi"}.get(m, "")
    except (EOFError, KeyboardInterrupt):
        print("\n（下次再出发）")
        return None
    trip = Trip.new(slug, days=days, budget=budget, mate=mate)
    print()
    print(trip.opening())
    return trip


def repl(trip: Trip | None):
    if trip is None:
        tid = active_trip_id()
        if tid:
            trip = Trip.load(tid)
            print(trip.cmd("status"))
        else:
            trip = wizard()
            if trip is None:
                return
    print("\n（输入命令开始旅行；quit 退出，进度自动保存）")
    while True:
        try:
            raw = input("\n〉 ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n（旅程已保存，回头见）")
            break
        if not raw:
            continue
        if raw.lower() in ("quit", "exit", "q", "退出"):
            print("（旅程已保存，回头见）")
            break
        print()
        print(trip.cmd(raw))


# ---------------------------------------------------------------- 观战页

_pack_cache = {}

_SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".gif": "image/gif"}


_NOTE_CMDS = re.compile(r"^(note|postcard|share)\b", re.I)
_POSTCARD_MSG = re.compile(r"背面你只写了一句：「[^」]*」")


def _observer_data() -> dict:
    tid = active_trip_id()
    if not tid:
        return {"empty": True}
    try:
        st = load_state(tid)
    except Exception:
        return {"empty": True}
    slug = st.slug
    if slug not in _pack_cache:
        try:
            _pack_cache[slug] = content.load_pack(slug)
        except Exception:
            return {"empty": True}
    pack = _pack_cache[slug]
    meta = pack["meta"]
    box = st.cities.get(slug, {})
    known = box.get("known_locs", [])
    cur_loc = pack["_loc"].get(st.loc, {})

    map_data = []
    for dist in pack["districts"]:
        locs = []
        for lid in known:
            l = pack["_loc"].get(lid)
            if not l or l["district"] != dist["id"]:
                continue
            locs.append({
                "name": l["name"],
                "type": content.TYPE_LABEL.get(l["type"], l["type"]),
                "here": lid == st.loc,
                "hidden": not l.get("starter", False),
            })
        if locs:
            map_data.append({"district": dist["name"], "locs": locs})

    share_iter = iter(getattr(st, "share_messages", None) or [])
    next_share = next(share_iter, None)

    log = []
    for e in st.log[-40:]:
        text = e.get("out", "")
        lines = [ln for ln in text.split("\n") if not ln.startswith("STATE {")]
        cmd_raw = e.get("cmd", "")
        if _NOTE_CMDS.match(cmd_raw):
            cmd_display = cmd_raw.split()[0]
        else:
            cmd_display = cmd_raw
        cleaned = "\n".join(lines).strip()
        if cmd_display == "postcard":
            cleaned = _POSTCARD_MSG.sub("写了什么，只有收信人知道。", cleaned)
        entry = {
            "day": e.get("day"), "slot": slot_of(e.get("t", 0)),
            "cmd": cmd_display, "text": cleaned,
        }
        if cmd_display == "share" and next_share is not None:
            entry["share_text"] = next_share["text"]
            next_share = next(share_iter, None)
        log.append(entry)

    if st.ended and log:
        last = log[-1]
        text = last.get("text", "")
        marker = "旅 程 结 算"
        idx = text.find(marker)
        if idx >= 0:
            last["text"] = text[:idx].rstrip()

    route_names = []
    for s in st.route:
        if s in _pack_cache:
            route_names.append(_pack_cache[s]["meta"]["city"])
        else:
            try:
                _pack_cache[s] = content.load_pack(s)
                route_names.append(_pack_cache[s]["meta"]["city"])
            except Exception:
                route_names.append(s)

    d = {
        "trip_id": st.trip_id, "city": meta["city"], "country": meta["country"],
        "loc": cur_loc.get("name", ""), "day": st.day, "days_total": st.days_total,
        "slot": slot_of(st.t), "weather": st.weather_by_day.get(str(st.day), ""),
        "money": st.money, "cur": meta["currency_symbol"],
        "energy": st.energy,
        "mate": MATES.get(st.mate, "").split("（")[0] if st.mate else None,
        "route": route_names, "ended": st.ended, "score": st.score,
        "journal": [{"type": e.get("type"), "title": e.get("title"),
                     "day": e.get("day"), "slot": e.get("slot"),
                     "loc": e.get("loc")} for e in st.journal],
        "wishes": [{"text": w["text"], "done": w["done"]}
                                          for w in st.wishes],
        "gems": st.gems, "stories": len(st.stories_heard),
        "map": map_data, "log": log,
    }

    if st.ended:
        from woyou.report import build_finale_data
        fd = build_finale_data(st, pack)
        d["finale"] = {
            "color_name": fd["color_name"],
            "color_line": fd["color_line"],
            "color_hex": fd["color_hex"],
            "dye_summary": fd["dye_summary_parts"],
            "dye_rows": fd["dye_rows"],
            "places": len(fd["places"]),
            "photos": len(fd["photos"]),
            "stories": len(fd["stories"]),
            "wishes_total": len(fd["wishes"]),
            "wishes_done": len(fd["done_wishes"]),
            "bought": len(fd["bought"]),
            "days": fd["days"],
        }
        d["share_messages"] = [m["text"] for m in (st.share_messages or [])]

    return d


class ObserverHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            body = (ROOT / "web" / "observer.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/data"):
            self._send_json(200, _observer_data())
        elif path.startswith("/photos/"):
            self._serve_photo(path)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                body = None
        except Exception:
            body = None
        path = urlsplit(self.path).path
        if path == "/report":
            self._handle_report(body or {})
        else:
            self.send_response(404)
            self.end_headers()

    # ---- 路由实现 ----
    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_report(self, body: dict) -> None:
        tid = active_trip_id()
        if not tid:
            self._send_json(200, {"ok": False, "error": "没有进行中的旅程存档。"})
            return
        try:
            trip = Trip.load(tid)
        except Exception as e:
            self._send_json(200, {"ok": False, "error": f"读取存档失败：{e}"})
            return
        try:
            from woyou.report import build_finale_data, render_settlement_text
        except ImportError:
            self._send_json(200, {"ok": False,
                                   "error": "结算模块（woyou/report.py）还没就位。"})
            return
        try:
            fd = build_finale_data(trip.state, trip.pack)
            text = render_settlement_text(fd)
        except Exception as e:
            self._send_json(200, {"ok": False, "error": f"结算生成失败：{e}"})
            return
        self._send_json(200, {"ok": True, "text": text})

    def _serve_photo(self, path: str) -> None:
        """GET /photos/<slug>/<file> → content/<slug>/photos/<file>（只读，防路径穿越）。"""
        parts = path.split("/")   # ["", "photos", "<slug>", "<file>"]
        if len(parts) != 4:
            self.send_response(404)
            self.end_headers()
            return
        slug, name = unquote(parts[2]), unquote(parts[3])
        if (not _SAFE_NAME.fullmatch(slug) or not _SAFE_NAME.fullmatch(name)
                or slug in (".", "..") or name in (".", "..")):
            self.send_response(400)
            self.end_headers()
            return
        base = (ROOT / "content" / slug / "photos").resolve()
        fpath = (base / name).resolve()
        if fpath != base and not str(fpath).startswith(str(base) + os.sep):
            self.send_response(403)
            self.end_headers()
            return
        if not fpath.is_file():
            self.send_response(404)
            self.end_headers()
            return
        ctype = _PHOTO_TYPES.get(fpath.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(fpath.read_bytes())

    def log_message(self, *a):
        pass


def watch(port: int, open_browser: bool = True):
    server = ThreadingHTTPServer(("127.0.0.1", port), ObserverHandler)
    url = f"http://127.0.0.1:{port}/"
    print(f"◉ 观战页开在 {url} （仅限本机访问，Ctrl+C 关闭）")
    print("  另开一个终端开始旅行：uv run play.py cmd <命令>，页面每 2 秒自动刷新。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n（观战结束）")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(prog="play.py", add_help=True,
                                 description="卧游 · 真实城市的文字旅行模拟")
    sub = ap.add_subparsers(dest="sub")

    p_new = sub.add_parser("new", help="开新旅程")
    p_new.add_argument("--city", required=True, help="城市名或包名（中文/英文）")
    p_new.add_argument("--days", type=int)
    p_new.add_argument("--budget", type=int)
    p_new.add_argument("--seed")
    p_new.add_argument("--month", type=int, help="旅行月份 1-12（影响天气）")
    p_new.add_argument("--mate", choices=list(MATES), help="旅游搭子")
    p_new.add_argument("--repl", action="store_true", help="开局后进入交互模式")

    p_demo = sub.add_parser("demo", help="京都离线示范之旅")
    p_demo.add_argument("--mate", choices=list(MATES))
    p_demo.add_argument("--days", type=int)
    p_demo.add_argument("--seed")
    p_demo.add_argument("--repl", action="store_true")

    p_build = sub.add_parser("build", help="预生成一座城（需要 DeepSeek API）")
    p_build.add_argument("--city", required=True)
    p_build.add_argument("--force", action="store_true", help="覆盖重建")

    p_cmd = sub.add_parser("cmd", help="对当前旅程执行一条命令")
    p_cmd.add_argument("words", nargs=argparse.REMAINDER, help="命令内容")

    p_exp = sub.add_parser("export", help="导出游记")

    p_report = sub.add_parser("report", help="重新显示旅程结算")
    p_report.add_argument("--trip", help="存档 id（默认当前 active）")

    p_watch = sub.add_parser("watch", help="浏览器观战页")
    p_watch.add_argument("--port", type=int, default=8642)
    p_watch.add_argument("--no-open", action="store_true")

    sub.add_parser("trips", help="列出旅程存档")
    sub.add_parser("packs", help="列出已生成的城市")
    sub.add_parser("repl", help="交互游玩")

    args = ap.parse_args()

    if args.sub == "new":
        trip = start_trip(args)
        if trip and args.repl:
            repl(trip)
    elif args.sub == "demo":
        trip = Trip.new("kyoto", days=args.days, seed=args.seed,
                        mate=args.mate or "")
        print(trip.opening())
        print(_WATCH_HINT, file=sys.stderr)
        if args.repl:
            repl(trip)
    elif args.sub == "build":
        if not llm.has_key():
            print("未配置 DEEPSEEK_API_KEY（写进项目根目录 .env，参考 .env.example）。")
            return
        from woyou.generate import build_city
        try:
            slug = build_city(args.city, force=args.force)
            print(f"\n现在可以出发了：uv run play.py new --city {slug}")
        except Exception as e:
            print(f"生成失败：{e}")
    elif args.sub == "cmd":
        text = " ".join(args.words).strip()
        tid = active_trip_id()
        if not tid:
            print("没有进行中的旅程。先 uv run play.py demo 或 new --city …")
            return
        trip = Trip.load(tid)
        print(trip.cmd(text))
    elif args.sub == "export":
        tid = active_trip_id()
        if not tid:
            print("没有旅程存档。")
            return
        trip = Trip.load(tid)
        from woyou import journal as journal_mod
        path = journal_mod.export_markdown(trip.state, trip.pack)
        print(f"游记：{path}")
    elif args.sub == "report":
        tid = args.trip or active_trip_id()
        if not tid:
            print("没有旅程存档。先 uv run play.py demo 或 new --city … 开始一段旅程。")
            return
        try:
            trip = Trip.load(tid)
        except Exception as e:
            print(f"读取存档失败：{e}")
            return
        try:
            from woyou.report import build_finale_data, render_settlement_text
        except ImportError:
            print("结算模块（woyou/report.py）还没就位。")
            return
        try:
            fd = build_finale_data(trip.state, trip.pack)
            print(render_settlement_text(fd))
        except Exception as e:
            print(f"结算生成失败：{e}")
    elif args.sub == "watch":
        watch(args.port, open_browser=not args.no_open)
    elif args.sub == "trips":
        rows = list_trips()
        if not rows:
            print("（还没有旅程）")
        for r in rows:
            mark = "✓完结" if r["ended"] else f"第{r['day']}/{r['days_total']}天"
            print(f"{r['trip_id']}  {'→'.join(r['route'])}  {mark}  {r['created_at']}")
    elif args.sub == "packs":
        for p in content.list_packs():
            print(f"{p['slug']}  {p['city']}（{p['country']}）")
        if not content.list_packs():
            print("（还没有城市内容包；uv run play.py build --city <城市名>）")
    else:
        repl(None)


if __name__ == "__main__":
    main()
