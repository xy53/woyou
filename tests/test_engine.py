# -*- coding: utf-8 -*-
"""卧游引擎测试（离线，用京都手写包）。

运行：uv run --no-project python -m unittest discover tests -v
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from woyou import content, score  # noqa: E402
from woyou.engine import (Trip, STATE_PREFIX, HABIT_LINES, LOOK_AGAIN,  # noqa: E402
                          LISTEN_AGAIN, MASTERED_LINES, JOIN_NONE)
from woyou.state import new_state, roll_weather  # noqa: E402


def make_trip(seed="7", mate="", days=5, budget=62000, month=8, t=0):
    pack = content.load_pack("kyoto")
    st = new_state(pack, days=days, budget=budget, seed=seed, month=month)
    st.mate = mate
    st.trip_id = "test-trip"
    st.t = t
    return Trip(st, pack, autosave=False)


def state_line(out):
    for ln in out.splitlines():
        if ln.startswith(STATE_PREFIX):
            return json.loads(ln[len(STATE_PREFIX):])
    raise AssertionError("输出里没有 STATE 行:\n" + out)


class TestPack(unittest.TestCase):
    def test_pack_valid(self):
        pack = content.load_pack("kyoto")
        self.assertEqual(content.validate_pack(pack), [])

    def test_hidden_locs_reachable(self):
        pack = content.load_pack("kyoto")
        hidden = {l["id"] for l in pack["locations"] if not l.get("starter")}
        revealed = set()
        for l in pack["locations"]:
            for ex in l.get("explore", []):
                revealed.add((ex.get("reveal") or {}).get("loc"))
        for n in pack.get("npcs", []):
            for tp in n.get("topics", []):
                revealed.add((tp.get("reveal") or {}).get("loc"))
            revealed.add(((n.get("story") or {}).get("reveal") or {}).get("loc"))
        self.assertTrue(hidden <= revealed, f"隐藏点不可达: {hidden - revealed}")


class TestEngine(unittest.TestCase):
    def test_opening_and_state_line(self):
        t = make_trip()
        out = t.opening()
        s = state_line(out)
        self.assertEqual(s["day"], 1)
        self.assertEqual(s["city"], "京都")
        self.assertEqual(s["money"], 62000)
        # 心愿不代抄：开局只给菜单，玩家自己选
        self.assertEqual(len(t.state.wishes), 0)
        self.assertIn("心愿清单", out)
        self.assertIn("1.", out)

    def test_state_line_has_no_mood(self):
        """心境已从游戏里彻底移除：STATE 行不再有 mood，状态也没有该字段。"""
        t = make_trip()
        self.assertFalse(hasattr(t.state, "mood"))
        for cmd in ("look", "explore", "photo", "listen"):
            s = state_line(t.cmd(cmd))
            self.assertNotIn("mood", s, f"{cmd} 的 STATE 行仍带 mood")
        self.assertNotIn("mood", t.opening())

    def test_wish_pick_by_number_and_cap(self):
        t = make_trip()
        menu = t._wish_menu()
        self.assertGreaterEqual(len(menu), 12)   # 选项要足
        out = t.cmd("wish 1 2 3")
        self.assertEqual(len(t.state.wishes), 3)
        self.assertIn("抄下心愿", out)
        self.assertEqual(state_line(out)["t"], 0)     # 免费
        t.cmd("wish 1 2")                             # 再抄两条（编号已重排）
        self.assertEqual(len(t.state.wishes), 5)
        out = t.cmd("wish 1")                         # 超过容量
        self.assertEqual(len(t.state.wishes), 5)
        self.assertIn("写满", out)

    def test_wish_pick_by_name(self):
        t = make_trip()
        t.cmd("wish 汤豆腐")
        self.assertEqual(t.state.wishes[0]["wid"], "yudofu_try")

    def test_look_is_free_and_repeats_softly(self):
        t = make_trip()
        out = t.cmd("look")
        self.assertEqual(state_line(out)["t"], 0)     # look 不耗时
        out2 = t.cmd("look")                          # 同时段同天气再看
        self.assertTrue(any(x in out2 for x in LOOK_AGAIN))
        self.assertEqual(state_line(out2)["t"], 0)

    def test_go_and_fees(self):
        t = make_trip()
        money0 = t.state.money
        out = t.cmd("go 清水寺")   # 跨区：车费 230 + 门票 400
        self.assertIn("清水寺", out)
        self.assertEqual(t.state.loc, "kiyomizu")
        self.assertEqual(t.state.money, money0 - 230 - 400)
        self.assertEqual(state_line(out)["t"], 2)

    def test_zh_prefix_command(self):
        t = make_trip()
        t.cmd("去 锦市场")
        self.assertEqual(t.state.loc, "nishiki")
        t2 = make_trip()
        t2.cmd("去锦市场")   # 无空格中文
        self.assertEqual(t2.state.loc, "nishiki")

    def test_closed_look_exterior(self):
        t = make_trip()          # 清晨 t0
        t.cmd("go 锦市场")       # t1，市场 hours [2,6] 未开
        out = t.cmd("look")
        self.assertIn("还没开", out)

    def test_explore_layers_and_gem(self):
        t = make_trip(t=1)
        t.cmd("go 锦市场")       # t2 开门
        t.cmd("explore")
        t.cmd("explore")
        gems0 = t.state.gems
        out = t.cmd("explore")   # 第三层：锦天满宫 gem
        self.assertEqual(t.state.gems, gems0 + 1)
        self.assertIn("锦天满宫", out)
        self.assertIn("意外", {e["type"] for e in t.state.journal})

    def test_npc_story_unlock(self):
        t = make_trip(t=1)
        t.cmd("wish one_story")  # 玩家自己抄下「听一个故事」
        t.cmd("go 锦市场")
        t.cmd("talk")            # 初见
        out = t.cmd("talk")      # 第二次 → 茶泡饭故事 (after_talks=2)
        self.assertIn("茶泡饭", out)
        self.assertEqual(len(t.state.stories_heard), 1)
        w = next(w for w in t.state.wishes if w["wid"] == "one_story")
        self.assertTrue(w["done"])
        self.assertIn("心愿达成", out)

    def test_story_unlock_not_discounted_by_mate(self):
        """搭子已彻底去功能化：带砚秋也不再提前解锁故事。"""
        t = make_trip(mate="yanqiu", t=1)
        t.cmd("go 锦市场")
        out = t.cmd("talk")      # 初见，故事还不该出来
        self.assertNotIn("茶泡饭", out)
        self.assertEqual(t.state.stories_heard, [])
        out2 = t.cmd("talk")     # 第二次才到火候
        self.assertIn("茶泡饭", out2)

    def test_companions_have_no_perks(self):
        t = make_trip()
        for c in t.companions.values():
            self.assertNotIn("perk", c)
        self.assertTrue(t.companions["xiaoqi"].get("camera"))

    def test_npc_reveals_hidden_loc(self):
        t = make_trip(t=1)
        t.cmd("go 锦市场")
        t.cmd("talk")
        self.assertNotIn("rokuyosha", t.state.box()["known_locs"])
        t.cmd("talk")            # topic[0]
        t.cmd("talk")            # topic[1]
        out = t.cmd("talk")      # topic[2] 六曜社 reveal
        self.assertIn("六曜社", out)
        self.assertIn("rokuyosha", t.state.box()["known_locs"])

    def test_eat_and_journal(self):
        t = make_trip(t=1)
        t.cmd("go 锦市场")
        out = t.cmd("eat")       # 列菜单
        self.assertIn("豆乳甜甜圈", out)
        money0 = t.state.money
        t.cmd("eat 豆乳甜甜圈")
        self.assertEqual(t.state.money, money0 - 400)
        self.assertIn("tounyu_donut", t.state.dishes_tried)
        self.assertIn("风味", [e["type"] for e in t.state.journal])

    def test_photo_journal_and_dedupe(self):
        t = make_trip()
        t.cmd("photo")
        self.assertEqual(len([e for e in t.state.journal if e["type"] == "风景"]), 1)
        t.cmd("photo")           # 同景重复 → 不再记
        self.assertEqual(len([e for e in t.state.journal if e["type"] == "风景"]), 1)

    def test_camera_mate_portrait_once_per_city(self):
        """小柒的 camera 是唯一保留的搭子特性：每城一张「有你的照片」。"""
        t = make_trip(mate="xiaoqi")
        t.cmd("go 鸭川")         # river 属 scenic
        out = t.cmd("photo")
        self.assertIn("有你的照片", [e["title"].split("·")[0] for e in t.state.journal])
        self.assertIn("快门响了", out)
        t.cmd("go 哲学之道")
        out2 = t.cmd("photo")
        self.assertNotIn("快门响了", out2)   # 每城只有一次

    def test_buy_and_souvenir(self):
        t = make_trip()
        out = t.cmd("buy 手拭巾")
        self.assertIn("永乐屋", out)
        self.assertEqual(len(t.state.bought), 1)
        self.assertIn("纪念", [e["type"] for e in t.state.journal])

    def test_sleep_advances_day_and_costs_hotel(self):
        t = make_trip()
        money0 = t.state.money
        out = t.cmd("sleep")
        self.assertEqual(t.state.day, 2)
        self.assertEqual(t.state.t, 0)
        self.assertEqual(t.state.energy, 100)
        self.assertEqual(t.state.money, money0 - 6000)
        self.assertIn("第 2 天", out)

    def test_day_time_exhaustion_forces_sleep(self):
        t = make_trip()
        for _ in range(12):
            t.cmd("explore")
            if t.state.t >= 10:
                break
        out = t.cmd("explore")
        self.assertIn("sleep", out)
        self.assertEqual(t.state.day, 1)

    def test_trip_finale_and_score(self):
        t = make_trip(days=2)
        t.cmd("look")
        t.cmd("photo")           # 攒下一样成色，结算才有颜色可报
        t.cmd("sleep")
        out = t.cmd("sleep")     # 第2晚 → 结算
        self.assertTrue(t.state.ended)
        self.assertIn("回味值", out)
        self.assertIn("成色", out)
        self.assertIn("一张拍下的景", out)
        sc = t.state.score
        self.assertIn("total", sc)
        self.assertEqual(sc["total"], sc["score"])
        self.assertIn("photo", sc["dims"])
        self.assertIn("一张拍下的景", sc["labels"])
        self.assertNotIn("心境", sc["breakdown"])
        self.assertEqual(sc["breakdown"]["一张拍下的景"], 12)
        self.assertIn("日记涓滴", sc["breakdown"])
        self.assertEqual(sum(sc["breakdown"].values()), sc["total"])
        out2 = t.cmd("explore")
        self.assertIn("旅程已经结束", out2)

    def test_finale_says_nothing_took_when_no_dim(self):
        """一趟什么也没沾上的旅行：不列成色，只说颜色留在下一次。"""
        t = make_trip(days=1)
        out = t.cmd("end trip")
        self.assertEqual(t.state.score["labels"], [])
        self.assertIn("颜色还留在下一次", out)
        self.assertIn("report", out)

    def test_end_trip_early(self):
        t = make_trip()
        out = t.cmd("end trip")
        self.assertTrue(t.state.ended)
        self.assertIn("回味值", out)

    def test_note_is_free_and_logged(self):
        t = make_trip()
        out = t.cmd("note 这条街的光线像老照片")
        self.assertEqual(state_line(out)["t"], 0)
        self.assertEqual(t.state.log[-1]["note"], "这条街的光线像老照片")

    def test_determinism_same_seed(self):
        a, b = make_trip(seed="42"), make_trip(seed="42")
        seq = ["look", "listen", "wander", "go 锦市场", "explore", "talk",
               "join", "sleep", "look"]
        self.assertEqual([a.cmd(c) for c in seq], [b.cmd(c) for c in seq])

    def test_weather_determinism(self):
        a, b = make_trip(seed="w1"), make_trip(seed="w1")
        for d in range(1, 6):
            self.assertEqual(roll_weather(a.state, a.pack, d),
                             roll_weather(b.state, b.pack, d))

    def test_mate_moment_fires_once(self):
        t = make_trip(mate="aman", t=1)
        t.cmd("go 锦市场")       # t2 开门
        out = t.cmd("look")
        self.assertIn("阿满", out)
        self.assertIn("nishiki", t.state.box()["moments_fired"])
        out2 = t.cmd("look")
        self.assertNotIn("战术", out2)

    def test_closed_location_blocks_explore(self):
        t = make_trip()
        t.cmd("go 先斗町")       # hours [6,9]，清晨去
        out = t.cmd("explore")
        self.assertIn("不开", out)

    def test_hidden_loc_go_hint(self):
        t = make_trip()
        out = t.cmd("go 六曜社")
        self.assertIn("地图上还没有", out)

    def test_removed_verbs_are_unknown(self):
        """ask / do 已从动词表里删除，落到「没明白」兜底且零消耗。"""
        t = make_trip()
        for raw in ("ask 锦市场是怎么来的", "do 拍一张街景"):
            out = t.cmd(raw)
            self.assertIn("没明白", out)
            self.assertEqual(state_line(out)["t"], 0)
        self.assertEqual(t.state.journal, [])


class TestExperienceVerbs(unittest.TestCase):
    """新增的三个感官动词与明信片。"""

    def test_listen_is_free_and_uses_sounds(self):
        t = make_trip(seed="listen")           # 晴天，走 sounds.default
        loc = t.pack["_loc"]["sanjo"]
        out = t.cmd("listen")
        self.assertEqual(state_line(out)["t"], 0)      # 免费不耗时
        self.assertEqual(t.state.energy, 100)
        self.assertIn("你闭上眼。", out)
        self.assertIn(loc["sounds"]["default"], out)
        self.assertEqual([r["kind"] for r in t.records], ["listen"])

    def test_listen_again_same_slot(self):
        t = make_trip(seed="listen")
        out1 = t.cmd("listen")
        self.assertFalse(any(x in out1 for x in LISTEN_AGAIN))
        out2 = t.cmd("listen")
        self.assertTrue(any(x in out2 for x in LISTEN_AGAIN))
        self.assertIn("清晨|晴", t.state.box()["visited"]["sanjo"]["heard"])

    def test_listen_picks_weather_variant(self):
        t = make_trip(seed="7")                # 雷阵雨
        out = t.cmd("listen")
        self.assertIn(t.pack["_loc"]["sanjo"]["sounds"]["rain"], out)

    def test_join_first_time_then_practiced(self):
        t = make_trip(seed="7")
        t.cmd("go 下鸭神社")                   # 跨区 → t2
        t0 = t.state.t
        out = t.cmd("join")
        act = t.pack["_loc"]["shimogamo"]["join"]
        self.assertIn(act["text"], out)
        self.assertEqual(t.state.t, t0 + 1)    # 耗 1 刻
        self.assertIn("shimogamo", t.state.box()["joined"])
        self.assertIn("二礼二拍手一礼", [e["title"] for e in t.state.journal])
        self.assertEqual(t.records[-1], {"kind": "join", "loc": "shimogamo",
                                         "loc_type": "shrine"})
        n_journal = len(t.state.journal)
        out2 = t.cmd("join")                   # 同地重复：熟练，不再记日记
        self.assertIn("熟练", out2)
        self.assertNotIn(act["text"], out2)
        self.assertEqual(len(t.state.journal), n_journal)

    def test_join_unavailable_costs_nothing(self):
        t = make_trip(seed="7")
        t.cmd("go 哲学之道")                   # 该地没写 join
        t0, e0 = t.state.t, t.state.energy
        out = t.cmd("join")
        self.assertIn(JOIN_NONE, out)
        self.assertEqual((t.state.t, t.state.energy), (t0, e0))

    def test_wander_costs_a_turn_and_prefers_new(self):
        t = make_trip(seed="7")
        out = t.cmd("wander")
        seen = t.state.box()["wander_seen"]
        self.assertEqual(len(seen), 1)
        self.assertEqual(t.state.t, 1)
        self.assertEqual(t.state.energy, 95)
        self.assertIn(t.pack["wander"][seen[0]]["text"], out)
        self.assertEqual([r["kind"] for r in t.records], ["wander"])
        for _ in range(3):
            t.cmd("wander")
        self.assertEqual(len(seen), len(set(seen)))   # 优先出没见过的
        self.assertEqual(len(seen), 4)

    def test_wander_pool_respects_district(self):
        t = make_trip(seed="7")
        rakuchu = {i for i, w in enumerate(t.pack["wander"])
                   if w.get("district") in (None, "rakuchu")}
        for _ in range(4):
            t.cmd("wander")
        self.assertTrue(set(t.state.box()["wander_seen"]) <= rakuchu)

    def test_postcard_costs_money_and_journals(self):
        t = make_trip(seed="card")
        money0 = t.state.money
        out = t.cmd("postcard")
        self.assertEqual(t.state.money, money0 - 300)   # meta.postcard_price
        self.assertEqual(state_line(out)["t"], 1)
        self.assertIn("小小的太阳", out)                # 没写字时的默认背面
        entry = next(e for e in t.state.journal if e["title"] == "寄自京都的明信片")
        self.assertEqual(entry["type"], "纪念")
        self.assertEqual([r["kind"] for r in t.records], ["postcard"])

    def test_postcard_with_message(self):
        t = make_trip(seed="card")
        out = t.cmd("postcard 山很高，我很好")
        self.assertIn("背面你只写了一句", out)
        self.assertIn("山很高，我很好", out)
        entry = next(e for e in t.state.journal if e["title"] == "寄自京都的明信片")
        self.assertIn("山很高，我很好", entry["text"])


class TestWorldMemory(unittest.TestCase):
    """世界记忆三件套：习惯承认、本地人认人、逛成熟地。"""

    def test_habit_note_on_same_slot_across_days(self):
        t = make_trip(seed="habit")
        revisit = t.pack["_loc"]["kamogawa"]["revisit"]
        t.cmd("go 鸭川")                       # 第1天清晨
        out1 = t.cmd("look")
        self.assertNotIn(revisit, out1)        # 第一次不承认
        t.cmd("sleep")
        t.cmd("go 鸭川")                       # 第2天同一时辰
        out2 = t.cmd("look")
        self.assertIn(revisit, out2)           # 有 revisit 的地点用专属文本
        out3 = t.cmd("look")
        self.assertNotIn(revisit, out3)        # 每天每地至多一次
        t.cmd("sleep")
        t.cmd("go 鸭川")                       # 第3天：换成通用承认句
        out4 = t.cmd("look")
        self.assertTrue(any(h in out4 for h in HABIT_LINES))

    def test_habit_note_generic_lines(self):
        t = make_trip(seed="habit")            # 三条商店街没写 revisit
        t.cmd("look")
        t.cmd("sleep")
        out = t.cmd("look")
        self.assertTrue(any(h in out for h in HABIT_LINES))

    def test_npc_recall_next_day(self):
        t = make_trip(seed="recall", t=1)
        t.cmd("go 锦市场")                     # t2，阿婆 slots [2,6]
        t.cmd("talk")                          # 初见
        t.cmd("talk")                          # 记下见面的日子
        t.cmd("buy 京渍物礼盒")                # 留个「上回」的由头
        t.cmd("sleep")
        t.cmd("rest"); t.cmd("go 锦市场")      # 第2天 t2
        out = t.cmd("talk")
        self.assertIn("又是你", out)           # npc.recall
        self.assertIn("上回带走的京渍物礼盒", out)
        self.assertTrue(t.state.flags.get("recall:kyoto:obaa"))
        t.cmd("sleep")
        t.cmd("rest"); t.cmd("go 锦市场")      # 第3天
        out2 = t.cmd("talk")
        self.assertIn("朝你点点头", out2)      # 认过一次之后只给短问候
        self.assertNotIn("又是你", out2)

    def test_npc_recall_needs_a_previous_day(self):
        t = make_trip(seed="recall", t=1)
        t.cmd("go 锦市场")
        t.cmd("talk")                          # 初见
        out = t.cmd("talk")                    # 同一天再聊：不算认人
        self.assertNotIn("又是你", out)

    def test_mastered_location_and_map_tag(self):
        t = make_trip(seed="7")
        loc = t.pack["_loc"]["sanjo"]
        for _ in range(len(loc["explore"]) - 1):
            t.cmd("explore")
        out_last = t.cmd("explore")            # 逛尽的那一刻
        self.assertIn("逛成了熟地", out_last)
        out = t.cmd("explore")                 # 再逛：首次给专属完成感
        self.assertIn(loc["mastered"], out)
        self.assertTrue(t.state.flags.get("mastered:kyoto:sanjo"))
        out2 = t.cmd("explore")                # 之后换通用句
        self.assertNotIn(loc["mastered"], out2)
        self.assertTrue(any(m in out2 for m in MASTERED_LINES[1:]))
        line = next(l for l in t.cmd("map").splitlines() if "三条寺町" in l)
        self.assertIn("熟", line)

    def test_mastered_generic_when_loc_has_no_text(self):
        t = make_trip(seed="7")
        t.cmd("go 鸭川")                       # 鸭川没写 mastered
        for _ in range(len(t.pack["_loc"]["kamogawa"]["explore"])):
            t.cmd("explore")
        out = t.cmd("explore")
        self.assertIn(MASTERED_LINES[0], out)


class TestAmbient(unittest.TestCase):
    """行前功课的回忆，与搭子的环境反应。"""

    def test_ambient_trivia_on_first_look(self):
        t = make_trip(t=1)
        t.cmd("go 清水寺")
        out = t.cmd("look")
        self.assertIn("行前功课", out)
        self.assertIn("139根巨柱", out)        # 检索到的是清水寺那条，不是随机条目
        self.assertTrue(t.state.flags.get("trivia:kyoto:kiyomizu"))
        t.cmd("explore")                       # 换个时段再看
        out2 = t.cmd("look")
        self.assertNotIn("行前功课", out2)     # 每地只浮现一次

    def test_ambient_trivia_skipped_when_nothing_matches(self):
        t = make_trip()                        # 三条商店街与 trivia 无双字重叠
        out = t.cmd("look")
        self.assertNotIn("行前功课", out)

    def test_mate_ambient_once_per_loc_and_key(self):
        t = make_trip(seed="7", mate="aman")
        reacts = t.companions["aman"]["reactions"]["listen"]
        out1 = t.cmd("listen")
        self.assertTrue(any(r in out1 for r in reacts))   # 该 seed 过了概率门
        self.assertTrue(t.state.flags.get("amb:kyoto:sanjo:listen"))
        out2 = t.cmd("listen")
        self.assertFalse(any(r in out2 for r in reacts))  # 同(地点,情境)不二次出现

    def test_mate_ambient_never_repeats_whatever_the_dice(self):
        reacts = None
        for seed in [str(i) for i in range(12)]:
            t = make_trip(seed=seed, mate="xiaoqi")
            reacts = t.companions["xiaoqi"]["reactions"]["wander"]
            hits = sum(any(r in t.cmd("wander") for r in reacts) for _ in range(3))
            self.assertLessEqual(hits, 1, f"seed={seed} 重复出现了搭子反应")
        self.assertTrue(reacts)


class TestWishChecks(unittest.TestCase):
    """新动词带来的心愿判定类型。"""

    def test_wish_look_dusk_kamogawa(self):
        t = make_trip(seed="dusk")
        t.cmd("wish kamo_dusk")
        t.cmd("explore"); t.cmd("explore"); t.cmd("explore")   # 三条 6 刻 → 黄昏
        self.assertGreaterEqual(t.state.t, 6)
        t.cmd("go 鸭川")
        out = t.cmd("look")
        w = next(w for w in t.state.wishes if w["wid"] == "kamo_dusk")
        self.assertTrue(w["done"])
        self.assertIn("心愿达成", out)

    def test_wish_listen_bell(self):
        t = make_trip(seed="7", t=4)
        t.cmd("wish listen_bell")
        t.cmd("go 清水寺")                     # 跨区 2 刻 → t6 黄昏
        self.assertEqual(t.state.t, 6)
        out = t.cmd("listen")
        w = next(w for w in t.state.wishes if w["wid"] == "listen_bell")
        self.assertTrue(w["done"])
        self.assertIn("心愿达成", out)

    def test_wish_join_pray(self):
        t = make_trip(seed="7")
        t.cmd("wish join_pray")
        t.cmd("go 下鸭神社")
        out = t.cmd("join")
        w = next(w for w in t.state.wishes if w["wid"] == "join_pray")
        self.assertTrue(w["done"])
        self.assertIn("心愿达成", out)
        self.assertIn("心愿", [e["type"] for e in t.state.journal])

    def test_wish_postcard_send(self):
        t = make_trip(seed="card")
        t.cmd("wish postcard_send")
        w = next(w for w in t.state.wishes if w["wid"] == "postcard_send")
        self.assertFalse(w["done"])
        out = t.cmd("postcard 见字如面")
        self.assertTrue(w["done"])
        self.assertIn("心愿达成", out)


class TestScore(unittest.TestCase):
    """成色计分（布尔、反刷量）与显影调色。"""

    def assert_hex(self, h):
        self.assertIsInstance(h, str)
        self.assertTrue(h.startswith("#"), h)
        self.assertEqual(len(h), 7, h)
        int(h[1:], 16)                      # 不是十六进制会直接抛错

    # ---------- 布尔性 ----------
    def test_repeat_of_a_dim_only_adds_trickle(self):
        """吃两样菜 vs 一样菜：成色分完全相同，只差一滴日记涓滴。"""
        one = make_trip(seed="boolean")
        one.cmd("go 二年坂")
        one.cmd("eat 抹茶芭菲")
        two = make_trip(seed="boolean")
        two.cmd("go 二年坂")
        two.cmd("eat 抹茶芭菲")
        two.cmd("eat 蕨饼")                 # 第二份甜点不加分
        self.assertEqual(len(one.state.dishes_tried), 1)
        self.assertEqual(len(two.state.dishes_tried), 2)
        s1 = score.compute(one.state, one.pack)
        s2 = score.compute(two.state, two.pack)
        self.assertIn("dish", s1["dims"])
        self.assertEqual(s1["dims"], s2["dims"])
        self.assertEqual(s1["score"] - s1["trickle"], s2["score"] - s2["trickle"])
        self.assertEqual(s2["score"] - s1["score"], s2["trickle"] - s1["trickle"])

    def test_score_formula_and_grades(self):
        t = make_trip(seed="grade")
        s = score.compute(t.state, t.pack)
        self.assertEqual(s, {"dims": [], "labels": [], "score": 0,
                             "trickle": 0, "grade": score.GRADES[0][1]})
        t.state.journal = [{"type": "风味", "title": "x"}] * 40
        s = score.compute(t.state, t.pack)
        self.assertEqual(s["trickle"], 15)                  # 涓滴封顶
        self.assertEqual(s["score"], 15)
        self.assertEqual(score.grade_of(29), score.GRADES[0][1])
        self.assertEqual(score.grade_of(30), score.GRADES[1][1])
        self.assertEqual(score.grade_of(104), score.GRADES[2][1])
        self.assertEqual(score.grade_of(105), score.GRADES[3][1])
        self.assertEqual(len(score.DIMS) * 12 + 15, 147)    # 满色上限

    # ---------- 各维度判定 ----------
    def test_dim_dish(self):
        t = make_trip(seed="dish", t=1)
        t.cmd("go 锦市场")
        self.assertNotIn("dish", score.compute(t.state, t.pack)["dims"])
        t.cmd("eat 豆乳甜甜圈")
        self.assertIn("dish", score.compute(t.state, t.pack)["dims"])

    def test_dim_story_friend_and_wish(self):
        t = make_trip(seed="7", t=1)
        t.cmd("wish one_story")
        t.cmd("go 锦市场")
        t.cmd("talk")                       # 初见：还不算熟
        dims = score.compute(t.state, t.pack)["dims"]
        self.assertNotIn("friend", dims)
        self.assertNotIn("story", dims)
        t.cmd("talk")                       # 第二次：故事出来了，心愿也画钩
        dims = score.compute(t.state, t.pack)["dims"]
        self.assertIn("story", dims)
        self.assertIn("friend", dims)
        self.assertIn("wish", dims)

    def test_dim_habit_needs_a_second_day(self):
        t = make_trip(seed="habit")
        t.cmd("look")
        self.assertNotIn("habit", score.compute(t.state, t.pack)["dims"])
        t.cmd("sleep")
        t.cmd("look")                       # 第2天同一时辰的老位置
        self.assertIn("habit", score.compute(t.state, t.pack)["dims"])

    def test_dim_weathered_from_rain(self):
        t = make_trip(seed="7")             # 这个 seed 的第一天有雨
        self.assertIn("雨", roll_weather(t.state, t.pack, 1))
        self.assertNotIn("weathered", score.compute(t.state, t.pack)["dims"])
        t.cmd("look")
        self.assertIn("weathered", score.compute(t.state, t.pack)["dims"])

    def test_dim_weathered_from_night(self):
        t = make_trip(seed="clear", t=8)    # 夜晚
        t.state.box().setdefault("visited", {})["sanjo"] = {
            "looked": [], "explored": 0, "photos": [], "heard": ["夜晚|晴"]}
        self.assertIn("weathered", score.compute(t.state, t.pack)["dims"])

    def test_dim_photo_bought_join_and_labels_order(self):
        t = make_trip(seed="dims")
        t.cmd("photo")                      # 风景 → photo
        t.cmd("buy 手拭巾")                 # bought
        t.cmd("go 鸭川")
        t.cmd("join")                       # joined
        s = score.compute(t.state, t.pack)
        self.assertEqual(set(s["dims"]) & {"photo", "bought", "join"},
                         {"photo", "bought", "join"})
        order = [k for k, _, _, _ in score.DIMS]
        self.assertEqual(s["dims"], [k for k in order if k in s["dims"]])
        self.assertEqual(s["labels"], [score.LABEL_OF[k] for k in s["dims"]])
        self.assertEqual(s["score"], len(s["dims"]) * 12 + s["trickle"])

    def test_dim_gem_and_multicity(self):
        t = make_trip(seed="gem")
        self.assertNotIn("gem", score.compute(t.state, t.pack)["dims"])
        t.state.gems = 1
        self.assertIn("gem", score.compute(t.state, t.pack)["dims"])
        self.assertNotIn("multicity", score.compute(t.state, t.pack)["dims"])
        t.state.route = ["kyoto", "nara"]
        self.assertIn("multicity", score.compute(t.state, t.pack)["dims"])

    # ---------- 显影 ----------
    def test_blend_is_deterministic_and_order_free(self):
        keys = ["story", "dish", "wish", "habit"]
        a, b = score.blend(keys), score.blend(keys)
        self.assertEqual(a, b)
        self.assertEqual(a, score.blend(list(reversed(keys))))
        self.assertLessEqual(len(a["dominant"]), 3)
        self.assertEqual(len(a["dominant"]), 3)
        for label, dye in a["dominant"]:
            self.assertIn(label, score.LABEL_OF.values())
            self.assertTrue(dye)

    def test_blend_empty_is_plain_cloth(self):
        c = score.blend([])
        self.assertEqual(c["hex"], "#E5DFD0")
        self.assertEqual(c["name"], "素色")
        self.assertIn("胚布", c["line"])
        self.assertEqual(c["dominant"], [])

    def test_blend_single_dim_keeps_its_own_dye(self):
        c = score.blend(["story"])
        self.assertEqual(c["hex"], "#2B2B2B")
        self.assertEqual(c["dominant"], [("一个听来的故事", "墨色")])

    def test_every_hex_is_well_formed(self):
        for key, label, dye, hexv in score.DIMS:
            self.assertTrue(key and label and dye)
            self.assert_hex(hexv)
        for name, hexv, line in score.NAMED_COLORS:
            self.assertTrue(name and line)
            self.assert_hex(hexv)
        keys = [k for k, _, _, _ in score.DIMS]
        for n in range(1, len(keys) + 1):
            c = score.blend(keys[:n])
            self.assert_hex(c["hex"])
            self.assertTrue(c["name"] and c["line"])
            self.assertLessEqual(len(c["dominant"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
