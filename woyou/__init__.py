# -*- coding: utf-8 -*-
"""卧游 (Woyou) —— 给 AI 玩的真实旅行模拟。

「澄怀观道，卧以游之。」

给 AI 玩家的最小接口（与 fox-river-valley 同风格）：

    from woyou import new_trip, cmd
    print(new_trip("kyoto", days=5, mate="aman"))   # 开一段旅程
    print(cmd("look"))                               # 之后逐条发命令

每条命令返回叙事文本 + 一行 STATE {...}。请只凭这些输出决策（盲玩）。
"""
from .engine import Trip
from .state import active_trip_id, list_trips

__all__ = ["Trip", "new_trip", "cmd", "resume", "active_trip_id", "list_trips"]

_current = None


def new_trip(slug: str, days=None, budget=None, seed=None, month=None,
             mate: str = "") -> str:
    """开一段新旅程，返回开场叙事。slug 为内容包名（如 kyoto）。"""
    global _current
    _current = Trip.new(slug, days=days, budget=budget, seed=seed,
                        month=month, mate=mate)
    return _current.opening()


def resume(trip_id: str = None) -> str:
    """接续存档（默认最近一段旅程），返回状态概览。"""
    global _current
    tid = trip_id or active_trip_id()
    if not tid:
        return "还没有任何旅程存档。用 new_trip(...) 开始。"
    _current = Trip.load(tid)
    return _current.cmd("status")


def cmd(text: str) -> str:
    """对当前旅程执行一条命令，返回叙事 + STATE 行。"""
    global _current
    if _current is None:
        tid = active_trip_id()
        if not tid:
            return "还没有进行中的旅程。先 new_trip(...)。"
        _current = Trip.load(tid)
    return _current.cmd(text)
