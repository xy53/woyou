# -*- coding: utf-8 -*-
"""卧游 · 旅程存档：TripState 及 save/load。

多城市自由续程：钱包/体力/心情/日记/心愿是全程共享的；
地图认知、到访记录、事件触发等按城市分容器存在 cities[slug] 里。
"""
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .util import SAVE_DIR, read_json, write_json, stable_rng


def new_city_box(arrival_day: int = 1) -> dict:
    return {
        "known_locs": [],      # 已标进地图的地点
        "visited": {},         # loc -> {looked:[], explored:int, photos:[]}
        "talked": {},          # npc -> 次数
        "met": [],             # 见过的 npc
        "events_fired": [],    # once 事件
        "moments_fired": [],   # 搭子时刻（loc id）
        "fees_paid": [],       # 已买过门票的地点
        "joined": [],          # 已入乡随俗过的地点
        "wander_seen": [],     # 漫步小景已见索引
        "arrival_day": arrival_day,
    }


@dataclass
class TripState:
    trip_id: str = ""
    slug: str = ""              # 当前所在城市的内容包 slug
    seed: str = "0"
    lang: str = "zh"
    month: int = 1              # 旅行月份（决定天气模式）
    day: int = 1                # 第几天（1 起）
    t: int = 0                  # 当日时刻 0..9
    days_total: int = 5
    money: int = 0              # 以当前城市货币计——钱、体力、时刻，仅有的三样身体性资源
    energy: int = 100
    loc: str = ""               # 当前地点 id
    mate: str = ""              # 旅游搭子 id（空 = 独行）
    cities: dict = field(default_factory=dict)        # slug -> city box
    route: list = field(default_factory=list)         # 途经城市 slug 顺序
    route_names: list = field(default_factory=list)   # 途经城市显示名
    weather_by_day: dict = field(default_factory=dict)  # "day" -> 天气名
    stories_heard: list = field(default_factory=list)
    gems: int = 0
    dishes_tried: list = field(default_factory=list)
    bought: list = field(default_factory=list)
    journal: list = field(default_factory=list)       # {day,slot,city,loc,type,title,text}
    wishes: list = field(default_factory=list)        # 玩家自己抄下的心愿 {id,city,wid,text,done,day}
    wish_cap: int = 5                                 # 手帐心愿页容量（每多一城 +3）
    flags: dict = field(default_factory=dict)
    spent: int = 0              # 折合人民币的总花销（粗略，报告用）
    spent_local: int = 0        # 当地货币的精确总花销（报告用）
    log: list = field(default_factory=list)           # 最近输出流（观战页用）
    player_notes: list = field(default_factory=list)   # 玩家自语独立存储
    footprints: dict = field(default_factory=dict)     # {day_str: [loc_name, ...]} 去过哪里
    timeline_seq: int = 0                               # 自语/日记共享递增序号
    share_messages: list = field(default_factory=list)    # [{day, seq, text}] 给观者的实时留言
    ended: bool = False
    score: dict = field(default_factory=dict)
    created_at: str = ""

    # ---- 便捷 ----
    def box(self) -> dict:
        """当前城市的分容器。"""
        if self.slug not in self.cities:
            self.cities[self.slug] = new_city_box(self.day)
        return self.cities[self.slug]

    # ---- 持久化 ----
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TripState":
        st = cls()
        for k, v in d.items():
            if hasattr(st, k):
                setattr(st, k, v)
        old = d.get("share_message", "")
        if old and not st.share_messages:
            st.share_messages = [{"day": st.day, "seq": 0, "text": old}]
        return st

    def save_path(self) -> Path:
        return SAVE_DIR / f"{self.trip_id}.json"

    def save(self) -> None:
        write_json(self.save_path(), self.to_dict())
        (SAVE_DIR / "active.txt").write_text(self.trip_id, encoding="utf-8")


def roll_weather(state: TripState, pack: dict, day: int) -> str:
    """某天在当前城市的天气（确定性、按需生成并缓存）。"""
    key = str(day)
    cached = state.weather_by_day.get(key)
    if cached:
        return cached
    meta = pack["meta"]
    table = meta.get("weather", {})
    pattern = table.get(str(state.month), table.get("default", [["晴", 1]]))
    rng = stable_rng(state.seed, state.slug, "weather", day)
    names = [w for w, _ in pattern]
    weights = [max(1, int(w)) for _, w in pattern]
    weather = rng.choices(names, weights=weights, k=1)[0]
    state.weather_by_day[key] = weather
    return weather


def enter_city(state: TripState, pack: dict) -> dict:
    """把状态切到某城（开局或跨城抵达共用）。返回 city box。"""
    meta = pack["meta"]
    state.slug = meta["slug"]
    if state.slug not in state.route:
        state.route.append(state.slug)
        state.route_names.append(meta.get("city", state.slug))
    first_time = state.slug not in state.cities
    box = state.box()
    if first_time:
        box["arrival_day"] = state.day
        box["known_locs"] = [l["id"] for l in pack["locations"] if l.get("starter")]
        start = meta["start_loc"]
        if start not in box["known_locs"]:
            box["known_locs"].insert(0, start)
        if len(state.route) > 1:      # 每多到一座城，手帐心愿页加厚
            state.wish_cap += 3
    state.loc = meta["start_loc"]
    return box


def new_state(pack: dict, days: int = None, budget: int = None, seed: str = None,
              month: int = None, mate: str = "") -> TripState:
    meta = pack["meta"]
    now = time.localtime()
    st = TripState()
    st.seed = str(seed) if seed is not None else str(int(time.time()))
    st.month = int(month) if month else now.tm_mon
    st.days_total = int(days) if days else int(meta.get("default_days", 5))
    st.money = int(budget) if budget else int(meta.get("default_budget", 3000))
    st.lang = meta.get("lang", "zh")
    st.mate = mate or ""
    st.trip_id = f"{meta['slug']}-{time.strftime('%Y%m%d-%H%M%S', now)}"
    st.created_at = time.strftime("%Y-%m-%d %H:%M:%S", now)
    enter_city(st, pack)
    roll_weather(st, pack, 1)
    return st


def load_state(trip_id: str) -> TripState:
    return TripState.from_dict(read_json(SAVE_DIR / f"{trip_id}.json"))


def active_trip_id():
    p = SAVE_DIR / "active.txt"
    if p.exists():
        tid = p.read_text(encoding="utf-8").strip()
        if tid and (SAVE_DIR / f"{tid}.json").exists():
            return tid
    saves = sorted(SAVE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    return saves[0].stem if saves else None


def list_trips() -> list:
    out = []
    if not SAVE_DIR.exists():
        return out
    for f in sorted(SAVE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        if f.name == "active.txt":
            continue
        try:
            d = read_json(f)
            out.append({"trip_id": d.get("trip_id", f.stem), "slug": d.get("slug"),
                        "route": d.get("route", []), "day": d.get("day"),
                        "days_total": d.get("days_total"), "ended": d.get("ended"),
                        "created_at": d.get("created_at")})
        except Exception:
            continue
    return out
