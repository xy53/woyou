# -*- coding: utf-8 -*-
"""卧游 · 旅行报告测试（离线，用京都手写包）。

运行：uv run --no-project python -m unittest discover tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from woyou import content, report, score  # noqa: E402
from woyou.engine import Trip  # noqa: E402
from woyou.state import new_state  # noqa: E402

ECHOES = {
    "obaa": "盐把话说了，萝卜就不用自己喊咸",
    "guide": "语言比栅栏长命",
    "busker": "那天我给我奶奶打了个电话——也说不上为什么",
    "okami": "收进来，腌上四百年，就成了自己的",
    "bookkeeper": "传奇在这座城的下场就是这样：变成街坊",
}


def make_trip(seed="7", mate="", days=3, budget=62000, month=8, t=0,
              trip_id="test-report"):
    pack = content.load_pack("kyoto")
    st = new_state(pack, days=days, budget=budget, seed=seed, month=month)
    st.mate = mate
    st.trip_id = trip_id
    st.t = t
    return Trip(st, pack, autosave=False)


def played(**kw) -> Trip:
    """一段有日记、有故事、有自语、有照片的短旅程（3 天，京都）。"""
    t = make_trip(**kw)
    t.opening()
    for c in ["wish 1 3", "look", "photo", "note 这条街的光线像老照片",
              "explore", "go 锦市场", "talk", "talk", "eat 豆乳甜甜圈",
              "photo", "sleep", "go 鸭川", "look", "photo",
              "note 河水把黄昏拉长了", "sleep", "go 清水寺", "look", "photo",
              "talk", "talk", "end trip"]:
        t.cmd(c)
    return t


def body_of(text: str) -> str:
    """正文 = 附录之前的部分。"""
    return text.split(report.RULE_APPENDIX)[0]


def section(text: str, rule: str, until: str = None) -> str:
    part = text.split(rule)[1]
    return part.split(until)[0] if until else part


class FakeClient:
    """替身 DeepSeek：记下调用参数，回一段写死的观察。"""
    calls = []

    def __init__(self, *a, **kw):
        pass

    def chat(self, system, user, **kw):
        FakeClient.calls.append({"system": system, "user": user, "kw": kw})
        return ("锦市场的水在石板底下流了四百年\n"
                "你在上面站了一会儿\n\n"
                "你说，这条街的光线像老照片\n"
                "你说，河水把黄昏拉长了\n")


class TestEcho(unittest.TestCase):
    """内容包的「回声」：必须是故事原文里逐字的一句。"""

    def test_kyoto_echoes_are_verbatim_substrings(self):
        pack = content.load_pack("kyoto")
        got = {}
        for n in pack["npcs"]:
            st = n.get("story") or {}
            self.assertIn("echo", st, f"{n['id']} 的故事没有 echo")
            self.assertIn(st["echo"], st["text"],
                          f"{n['id']} 的 echo 不是 text 的逐字子串")
            got[n["id"]] = st["echo"]
        self.assertEqual(got, ECHOES)

    def test_echo_is_quoted_in_report(self):
        t = played()
        text = report.make_report(t.state, t.pack)["text"]
        self.assertIn("茶泡饭的暗语", text)
        self.assertIn(f"——「{ECHOES['obaa']}」", text)


class TestOfflineReport(unittest.TestCase):
    """不开观察：纯离线、零 API，报告照样是完整的。"""

    def setUp(self):
        self.trip = played()
        with mock.patch.object(report.llm, "has_key") as has_key:
            self.rep = report.make_report(self.trip.state, self.trip.pack)
            self.assertFalse(has_key.called, "没开 observe 却摸了 API")
        self.text = self.rep["text"]

    def test_cover_and_sections(self):
        head = self.text.splitlines()
        self.assertTrue(head[0].startswith("═"))
        self.assertIn("卧游 · 旅行报告", self.text)
        self.assertIn("京都 · 日本", self.text)
        self.assertIn("3天", self.text)
        for rule in (report.RULE_TIMELINE, report.RULE_PHOTOS,
                     report.RULE_WISHES, report.RULE_DEVELOP,
                     report.RULE_APPENDIX):
            self.assertIn(rule, self.text)
        self.assertIn("这趟旅行洗出来，是一种颜色", self.text)
        self.assertFalse(self.rep["meta"]["observed"])
        self.assertIn("观察出处对照", self.text)

    def test_unfinished_trip_says_which_day(self):
        t = make_trip(trip_id="test-unfinished")
        t.opening()
        t.cmd("look")
        t.cmd("sleep")
        text = report.make_report(t.state, t.pack)["text"]
        self.assertIn("行至第2天", text)

    def test_opening_facts_and_spend_breakdown(self):
        self.assertIn("走过 ", self.text)
        self.assertIn("遇见 ", self.text)
        self.assertIn("住宿 ", self.text)
        self.assertIn("的车票与门票", self.text)
        # 单城旅程不加城市链
        self.assertNotIn("⇢", self.text)

    def test_timeline_chain_and_marks(self):
        line = next(l for l in self.text.splitlines() if l.startswith("第1天"))
        self.assertIn("｜", line)
        self.assertIn("→", line)
        self.assertIn("📷", self.text)     # 风景
        self.assertIn("📜", self.text)     # 故事

    def test_photos_section_has_boxes(self):
        block = section(self.text, report.RULE_PHOTOS, report.RULE_WISHES)
        self.assertIn("（你选了：文字）", block)
        self.assertIn("╭", block)
        self.assertIn("╰", block)
        self.assertTrue(self.rep["meta"]["photos"])
        self.assertTrue(all(p["form"] == "text"
                            for p in self.rep["meta"]["photos"]))

    def test_color_meta_from_score(self):
        color = self.rep["meta"]["color"]
        for k in ("hex", "name", "line", "dominant"):
            self.assertIn(k, color)
        self.assertIn(color["name"], self.text)
        self.assertIn(color["line"], self.text)


class TestNoNumbersInBody(unittest.TestCase):
    """无数字原则：分数只活在附录里，正文一个也不许露。"""

    def test_body_has_no_score(self):
        t = played()
        rep = report.make_report(t.state, t.pack)
        body = body_of(rep["text"])
        result = score.compute(t.state, t.pack)
        self.assertNotIn("回味值", body)
        self.assertNotIn("成色", body)
        self.assertNotIn(rep["meta"]["color"]["hex"], body)
        develop = section(body, report.RULE_DEVELOP, "（已自动保存到")
        self.assertIn(result["grade"], develop)
        self.assertFalse([c for c in develop if c.isdigit()],
                         f"显影段出现了数字：{develop!r}")
        self.assertNotIn(str(result["score"]), develop)
        for label in result["labels"]:      # 成色清单在显影段
            self.assertIn(label, develop)

    def test_appendix_keeps_the_numbers(self):
        t = played()
        text = report.make_report(t.state, t.pack)["text"]
        appendix = text.split(report.RULE_APPENDIX)[1]
        self.assertIn("花销：住宿", appendix)
        self.assertTrue([c for c in appendix if c.isdigit()])


class TestNotes(unittest.TestCase):
    """玩家自语：逐字，且整份报告里只出现一次。"""

    def test_notes_in_timeline_verbatim_when_not_observed(self):
        t = played()
        text = report.make_report(t.state, t.pack)["text"]
        body = body_of(text)
        for note in ("这条街的光线像老照片", "河水把黄昏拉长了"):
            self.assertIn(f"🗨「{note}」", body)
            self.assertEqual(body.count(note), 1, f"「{note}」在正文里出现了不止一次")

    def test_no_notes_no_speech_bubbles(self):
        t = make_trip(trip_id="test-quiet")
        t.opening()
        t.cmd("look")
        t.cmd("photo")
        text = report.make_report(t.state, t.pack)["text"]
        self.assertNotIn("🗨", text)
        self.assertIn("玩家自语：无", text)


class TestWishes(unittest.TestCase):
    def test_section_absent_when_nothing_copied(self):
        t = make_trip(trip_id="test-nowish")
        t.opening()
        for c in ("look", "photo", "go 锦市场", "end trip"):
            t.cmd(c)
        self.assertEqual(t.state.wishes, [])
        text = report.make_report(t.state, t.pack)["text"]
        self.assertNotIn(report.RULE_WISHES, text)
        self.assertNotIn("你抄了", text)
        self.assertIn("心愿：一条也没抄", text)   # 附录里仍如实记着

    def test_undone_wish_is_a_reason_to_come_back(self):
        t = played()
        text = report.make_report(t.state, t.pack)["text"]
        block = section(text, report.RULE_WISHES, report.RULE_DEVELOP)
        self.assertIn("你抄了 2 条心愿", block)
        self.assertIn("回京都的理由", block)

    def test_all_done_changes_the_last_two_lines(self):
        t = played(trip_id="test-allwish")
        for w in t.state.wishes:
            w["done"] = True
        text = report.make_report(t.state, t.pack)["text"]
        block = section(text, report.RULE_WISHES, report.RULE_DEVELOP)
        self.assertIn("每一条都应了验", block)
        self.assertNotIn("没应验", block)
        self.assertNotIn("的理由", block)


class TestPhotoSelection(unittest.TestCase):
    """>6 张就得精选：雨雪夜优先 → 隐藏地点 → 每城至少一张 → 天序补足。"""

    def build(self):
        t = make_trip(trip_id="test-photos")
        t.opening()
        st = t.state
        st.weather_by_day = {"1": "晴", "2": "小雨", "3": "晴", "4": "晴"}
        st.journal = []
        rows = [
            (1, "清晨", "三条寺町商店街", "普通一"),
            (1, "午后", "锦市场", "普通二"),
            (2, "上午", "鸭川河岸", "雨中一"),
            (2, "午后", "哲学之道", "雨中二"),
            (3, "夜晚", "先斗町", "夜里一"),
            (3, "上午", "金阁寺", "普通三"),
            (4, "上午", "六曜社珈琲店", "深巷一"),     # starter=false
            (4, "午后", "清水寺", "普通四"),
        ]
        for day, slot, loc, title in rows:
            st.journal.append({"day": day, "slot": slot, "city": "京都",
                               "loc": loc, "type": "风景", "title": title,
                               "text": f"{title}的取景框里，{loc}在{slot}的样子。"})
        return t

    def test_picks_six_and_notes_the_rest(self):
        t = self.build()
        rep = report.make_report(t.state, t.pack)
        block = section(rep["text"], report.RULE_PHOTOS, report.RULE_DEVELOP)
        self.assertEqual(len(rep["meta"]["photos"]), 6)
        self.assertIn("（其余 2 张收在游记里）", block)
        for must in ("雨中一", "雨中二", "夜里一", "深巷一"):
            self.assertIn(must, block, f"{must} 该被选中")
        days = [p["day"] for p in rep["meta"]["photos"]]
        self.assertEqual(days, sorted(days), "洗出来的照片要按天序排")
        self.assertNotIn("普通三", block)
        self.assertNotIn("普通四", block)

    def test_every_city_keeps_at_least_one(self):
        t = self.build()
        st = t.state
        st.weather_by_day = {str(d): "小雨" for d in range(1, 6)}
        st.journal = [e for e in st.journal][:7]
        for e in st.journal:                       # 京都的七张全是雨天，优先级最高
            e["day"] = 1
        st.journal.append({"day": 5, "slot": "午后", "city": "奈良",
                           "loc": "东大寺", "type": "风景", "title": "奈良一",
                           "text": "鹿在参道上让开半步，又跟上来。"})
        st.weather_by_day["5"] = "晴"
        rep = report.make_report(st, t.pack)
        cities = {p["city"] for p in rep["meta"]["photos"]}
        self.assertEqual(len(rep["meta"]["photos"]), 6)
        self.assertIn("奈良", cities, "每座城至少要留一张")

    def test_all_kept_when_six_or_fewer(self):
        t = self.build()
        t.state.journal = t.state.journal[:5]
        rep = report.make_report(t.state, t.pack)
        block = section(rep["text"], report.RULE_PHOTOS, report.RULE_DEVELOP)
        self.assertEqual(len(rep["meta"]["photos"]), 5)
        self.assertNotIn("其余", block)

    def test_portrait_is_always_kept(self):
        t = self.build()
        t.state.journal.append({
            "day": 2, "slot": "黄昏", "city": "京都", "loc": "鸭川河岸",
            "type": "纪念", "title": "有你的照片·鸭川河岸",
            "text": "她忽然叫你的名字，你一回头，快门响了。"})
        rep = report.make_report(t.state, t.pack)
        titles = [p["title"] for p in rep["meta"]["photos"]]
        self.assertIn("有你的照片·鸭川河岸", titles)
        self.assertEqual(len(titles), 6)

    def test_real_photos_fall_back_to_text_without_manifest(self):
        t = self.build()
        with mock.patch.object(report, "_manifest", return_value={}):
            rep = report.make_report(t.state, t.pack, photos="real")
        block = section(rep["text"], report.RULE_PHOTOS, report.RULE_DEVELOP)
        self.assertIn("（你选了：照片）", block)
        self.assertNotIn("[照片]", block)
        self.assertIn("╭", block)
        self.assertTrue(all(p["form"] == "text" for p in rep["meta"]["photos"]))

    def test_real_photos_use_manifest_when_it_has_the_place(self):
        t = self.build()
        fake = {"kamogawa": [{"file": "kamo-dawn.jpg", "caption": "鸭川的清晨",
                              "credit": "Eva"}]}
        with mock.patch.object(report, "_manifest", return_value=fake):
            rep = report.make_report(t.state, t.pack, photos="real")
            block = section(rep["text"], report.RULE_PHOTOS, report.RULE_DEVELOP)
            self.assertIn("[照片] content/kyoto/photos/kamo-dawn.jpg"
                          " ｜ 鸭川的清晨 ｜ 摄影：Eva", block)
            forms = {p["loc"]: p["form"] for p in rep["meta"]["photos"]}
            self.assertEqual(forms["鸭川河岸"], "real")
            self.assertEqual(forms["锦市场"], "text")     # 清单里没有就回落
            rep2 = report.make_report(t.state, t.pack, photos="both")
        block2 = section(rep2["text"], report.RULE_PHOTOS, report.RULE_DEVELOP)
        self.assertIn("（你选了：都要）", block2)
        self.assertIn("[照片] content/kyoto/photos/kamo-dawn.jpg", block2)
        self.assertIn("╰─ 鸭川河岸 · 上午", block2)     # 照片行 + 文字框都给


def photo_row(**kw) -> dict:
    """一条「时辰档案」——默认全放行，测哪一道门就改哪一个字段。"""
    row = {"file": "x.jpg", "title": "某处", "author": "拍照的人",
           "license": "CC BY 4.0", "source": "https://example.invalid/File:x",
           "scene_note": "亲眼看到的样子", "compatible_slots": None,
           "weather_hint": None, "season_months": None}
    row.update(kw)
    return row


class TestPhotoGate(unittest.TestCase):
    """时辰档案：一张照片只在它能诚实代表的时段／天气／季节里出场。

    不过门就静默回落文字，不解释、不道歉、也不许炸。
    全部用假 manifest，跟 content/kyoto/photos/ 里下没下到图无关。
    """

    def trip(self, rows, month=8, weather=None, trip_id="test-gate"):
        t = make_trip(trip_id=trip_id, month=month)
        t.opening()
        st = t.state
        st.month = month
        st.weather_by_day = weather or {str(d): "晴" for d in range(1, 6)}
        st.journal = []
        for day, slot, loc in rows:
            st.journal.append({
                "day": day, "slot": slot, "city": "京都", "loc": loc,
                "type": "风景", "title": f"{loc}的{slot}",
                "text": f"{loc}在{slot}的样子，只有文字记得。"})
        return t

    def block(self, t, manifest, photos="real"):
        with mock.patch.object(report, "_manifest", return_value=manifest):
            rep = report.make_report(t.state, t.pack, photos=photos)
        return rep, section(rep["text"], report.RULE_PHOTOS,
                            report.RULE_DEVELOP)

    # ---- ① 时段对上就出照片行 ----

    def test_slot_match_prints_the_photo_line(self):
        t = self.trip([(1, "上午", "鸭川河岸")])
        manifest = {"kamogawa": [photo_row(
            file="kamogawa.jpg", title="鸭川河岸", scene_note="白鹭各自站着",
            compatible_slots=["清晨", "上午", "午后"])]}
        rep, block = self.block(t, manifest)
        self.assertIn("[照片] content/kyoto/photos/kamogawa.jpg"
                      " ｜ 白鹭各自站着 ｜ 摄影：拍照的人", block)
        self.assertEqual([p["form"] for p in rep["meta"]["photos"]], ["real"])
        self.assertNotIn("╭", block)          # real 模式过了门就不再给文字框

    def test_both_mode_gives_line_and_box(self):
        t = self.trip([(1, "上午", "鸭川河岸")])
        manifest = {"kamogawa": [photo_row(compatible_slots=["上午"])]}
        rep, block = self.block(t, manifest, photos="both")
        self.assertIn("[照片]", block)
        self.assertIn("╰─ 鸭川河岸 · 上午", block)
        self.assertEqual([p["form"] for p in rep["meta"]["photos"]], ["both"])

    # ---- ② 夜景照片配午后条目 → 回落文字 ----

    def test_night_photo_refuses_an_afternoon_entry(self):
        manifest = {"ichijoji": [photo_row(
            file="ichijoji.jpg", title="一乘寺·惠文社",
            compatible_slots=["夜晚"])]}
        t = self.trip([(1, "午后", "一乘寺·惠文社")])
        rep, block = self.block(t, manifest)
        self.assertNotIn("[照片]", block)
        self.assertIn("╰─ 一乘寺·惠文社 · 午后", block)
        self.assertEqual([p["form"] for p in rep["meta"]["photos"]], ["text"])
        self.assertNotIn("时辰", block)        # 静默：不解释为什么没照片

        t2 = self.trip([(1, "夜晚", "一乘寺·惠文社")], trip_id="test-gate-night")
        rep2, block2 = self.block(t2, manifest)
        self.assertIn("[照片] content/kyoto/photos/ichijoji.jpg", block2)
        self.assertEqual([p["form"] for p in rep2["meta"]["photos"]], ["real"])

    def test_mismatch_row_never_passes(self):
        t = self.trip([(1, "上午", "锦市场")])
        manifest = {"nishiki": [photo_row(
            compatible_slots=["清晨", "上午", "午后"],
            mismatch="拍的根本不是这地方")]}
        rep, block = self.block(t, manifest)
        self.assertNotIn("[照片]", block)
        self.assertNotIn("拍的根本不是这地方", rep["text"])

    # ---- ③ weather_hint ----

    def test_sunny_photo_refuses_a_rainy_entry(self):
        manifest = {"kiyomizu": [photo_row(
            file="kiyomizu.jpg", title="清水寺",
            compatible_slots=["上午", "午后"], weather_hint="晴")]}
        rainy = self.trip([(1, "午后", "清水寺")], weather={"1": "小雨"})
        rep, block = self.block(rainy, manifest)
        self.assertNotIn("[照片]", block)
        self.assertEqual([p["form"] for p in rep["meta"]["photos"]], ["text"])

        snowy = self.trip([(1, "午后", "清水寺")], weather={"1": "小雪"},
                          trip_id="test-gate-snow")
        _, block_snow = self.block(snowy, manifest)
        self.assertNotIn("[照片]", block_snow)      # 雪也算湿

        sunny = self.trip([(1, "午后", "清水寺")], weather={"1": "晴"},
                          trip_id="test-gate-sun")
        rep3, block3 = self.block(sunny, manifest)
        self.assertIn("[照片] content/kyoto/photos/kiyomizu.jpg", block3)
        self.assertEqual([p["form"] for p in rep3["meta"]["photos"]], ["real"])

    def test_rain_photo_only_shows_on_a_wet_day(self):
        manifest = {"nishiki": [photo_row(
            file="nishiki-rain.jpg", compatible_slots=["午后"],
            weather_hint="雨")]}
        dry = self.trip([(1, "午后", "锦市场")], weather={"1": "晴"})
        _, dry_block = self.block(dry, manifest)
        self.assertNotIn("[照片]", dry_block)

        wet = self.trip([(1, "午后", "锦市场")], weather={"1": "阵雨"},
                        trip_id="test-gate-wet")
        _, wet_block = self.block(wet, manifest)
        self.assertIn("[照片] content/kyoto/photos/nishiki-rain.jpg", wet_block)

    # ---- ④ season_months（含跨年区间）----

    def test_season_gate_keeps_autumn_out_of_august(self):
        manifest = {"kiyomizu": [photo_row(
            file="kiyomizu.jpg", title="清水寺",
            compatible_slots=["上午", "午后"], season_months=[10, 11])]}
        august = self.trip([(1, "午后", "清水寺")], month=8)
        rep, block = self.block(august, manifest)
        self.assertNotIn("[照片]", block)
        self.assertEqual([p["form"] for p in rep["meta"]["photos"]], ["text"])

        november = self.trip([(1, "午后", "清水寺")], month=11,
                             trip_id="test-gate-nov")
        rep2, block2 = self.block(november, manifest)
        self.assertIn("[照片] content/kyoto/photos/kiyomizu.jpg", block2)
        self.assertEqual([p["form"] for p in rep2["meta"]["photos"]], ["real"])

    def test_season_range_may_wrap_around_the_year(self):
        manifest = {"kinkakuji": [photo_row(
            file="kinkakuji-snow.jpg", compatible_slots=["上午"],
            season_months=[12, 2])]}
        for month, wanted in ((12, True), (1, True), (2, True),
                              (3, False), (6, False), (11, False)):
            t = self.trip([(1, "上午", "金阁寺")], month=month,
                          trip_id=f"test-gate-m{month}")
            _, block = self.block(t, manifest)
            self.assertEqual("[照片]" in block, wanted,
                             f"{month} 月的门开错了")

    def test_gate_is_and_not_or(self):
        """三道门是与的关系：错一样就整张回落。"""
        manifest = {"kamogawa": [photo_row(
            compatible_slots=["上午"], weather_hint="晴",
            season_months=[10, 11])]}
        cases = [((1, "上午", "鸭川河岸"), 11, "晴", True),
                 ((1, "黄昏", "鸭川河岸"), 11, "晴", False),
                 ((1, "上午", "鸭川河岸"), 11, "小雨", False),
                 ((1, "上午", "鸭川河岸"), 8, "晴", False)]
        for i, (row, month, weather, wanted) in enumerate(cases):
            t = self.trip([row], month=month, weather={"1": weather},
                          trip_id=f"test-gate-and{i}")
            _, block = self.block(t, manifest)
            self.assertEqual("[照片]" in block, wanted,
                             f"{row[1]}／{weather}／{month}月 判错了")

    # ---- ⑤ 用了真实照片就得署名 ----

    def test_appendix_credits_every_photo_actually_used(self):
        t = self.trip([(1, "上午", "鸭川河岸"), (2, "午后", "清水寺")])
        manifest = {
            "kamogawa": [photo_row(file="kamogawa.jpg", title="鸭川河岸",
                                   author="Hyppolyte", license="CC BY 4.0",
                                   source="https://example.invalid/File:kamo",
                                   compatible_slots=["上午"])],
            "kiyomizu": [photo_row(file="kiyomizu.jpg", title="清水寺",
                                   author="Falbisoner", license="CC BY-SA 4.0",
                                   source="https://example.invalid/File:kiyo",
                                   compatible_slots=["上午"])],   # 午后过不了门
        }
        rep, block = self.block(t, manifest)
        appendix = rep["text"].split(report.RULE_APPENDIX)[1]
        self.assertIn("【图片来源】", appendix)
        self.assertIn("鸭川河岸 ｜ Hyppolyte ｜ CC BY 4.0"
                      " ｜ https://example.invalid/File:kamo", appendix)
        self.assertNotIn("Falbisoner", appendix)      # 没洗出来的不署名
        self.assertNotIn("【图片来源】", body_of(rep["text"]))

    def test_no_credits_section_when_nothing_passed_the_gate(self):
        t = self.trip([(1, "午后", "一乘寺·惠文社")])
        manifest = {"ichijoji": [photo_row(compatible_slots=["夜晚"],
                                           author="Yuco")]}
        rep, _ = self.block(t, manifest)
        self.assertNotIn("【图片来源】", rep["text"])
        self.assertNotIn("Yuco", rep["text"])

    def test_text_mode_never_credits_anyone(self):
        t = self.trip([(1, "上午", "鸭川河岸")])
        manifest = {"kamogawa": [photo_row(compatible_slots=["上午"])]}
        rep, _ = self.block(t, manifest, photos="text")
        self.assertNotIn("【图片来源】", rep["text"])
        self.assertNotIn("[照片]", rep["text"])

    def test_same_photo_twice_is_credited_once(self):
        t = self.trip([(1, "上午", "鸭川河岸"), (2, "上午", "鸭川河岸")])
        manifest = {"kamogawa": [photo_row(
            file="kamogawa.jpg", title="鸭川河岸",
            source="https://example.invalid/File:kamo",
            compatible_slots=["上午"])]}
        rep, block = self.block(t, manifest)
        self.assertEqual(block.count("[照片]"), 2)
        appendix = rep["text"].split(report.RULE_APPENDIX)[1]
        self.assertEqual(appendix.count("https://example.invalid/File:kamo"), 1)

    # ---- ⑥ manifest 缺失／损坏／没这地点：现行为不变 ----

    def test_missing_manifest_falls_back_quietly(self):
        t = self.trip([(1, "上午", "鸭川河岸")])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(report, "CONTENT_DIR", Path(tmp)):
                self.assertEqual(report._manifest("kyoto"), {})
                rep = report.make_report(t.state, t.pack, photos="real")
        block = section(rep["text"], report.RULE_PHOTOS, report.RULE_DEVELOP)
        self.assertNotIn("[照片]", block)
        self.assertIn("╭", block)
        self.assertNotIn("【图片来源】", rep["text"])

    def test_broken_manifest_falls_back_quietly(self):
        t = self.trip([(1, "上午", "鸭川河岸")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kyoto" / "photos"
            path.mkdir(parents=True)
            (path / "manifest.json").write_text("{ 这不是 JSON",
                                                encoding="utf-8")
            with mock.patch.object(report, "CONTENT_DIR", Path(tmp)):
                self.assertEqual(report._manifest("kyoto"), {})
                rep = report.make_report(t.state, t.pack, photos="real")
        block = section(rep["text"], report.RULE_PHOTOS, report.RULE_DEVELOP)
        self.assertNotIn("[照片]", block)
        self.assertIn("╭", block)

    def test_manifest_without_this_place_falls_back(self):
        t = self.trip([(1, "上午", "鸭川河岸")])
        manifest = {"kinkakuji": [photo_row(compatible_slots=["上午"])]}
        rep, block = self.block(t, manifest)
        self.assertNotIn("[照片]", block)
        self.assertEqual([p["form"] for p in rep["meta"]["photos"]], ["text"])

    def test_manifest_file_on_disk_round_trips(self):
        """磁盘上的 {地点id: {档案}} 写法，_manifest 读得出来。"""
        t = self.trip([(1, "上午", "鸭川河岸")], month=11)
        data = {"kamogawa": {
            "file": "kamogawa.jpg", "title": "鸭川河岸", "author": "河边的人",
            "license": "CC BY 4.0", "source": "https://example.invalid/File:k",
            "scene_note": "浅滩上的白鹭各自站着",
            "compatible_slots": ["清晨", "上午", "午后"],
            "weather_hint": None, "season_months": [11, 2]}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kyoto" / "photos"
            path.mkdir(parents=True)
            (path / "manifest.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(report, "CONTENT_DIR", Path(tmp)):
                rep = report.make_report(t.state, t.pack, photos="real")
        block = section(rep["text"], report.RULE_PHOTOS, report.RULE_DEVELOP)
        self.assertIn("[照片] content/kyoto/photos/kamogawa.jpg"
                      " ｜ 浅滩上的白鹭各自站着 ｜ 摄影：河边的人", block)
        self.assertIn("鸭川河岸 ｜ 河边的人 ｜ CC BY 4.0",
                      rep["text"].split(report.RULE_APPENDIX)[1])

    def test_old_style_manifest_still_works(self):
        """没写时辰档案的老条目一律放行——档案是收紧，不是新门槛。"""
        t = self.trip([(1, "夜晚", "鸭川河岸")], month=8)
        manifest = {"kamogawa": [{"file": "kamo.jpg", "caption": "鸭川的清晨",
                                  "credit": "Eva"}]}
        rep, block = self.block(t, manifest)
        self.assertIn("[照片] content/kyoto/photos/kamo.jpg"
                      " ｜ 鸭川的清晨 ｜ 摄影：Eva", block)
        appendix = rep["text"].split(report.RULE_APPENDIX)[1]
        self.assertIn("kamo.jpg ｜ Eva", appendix)   # 有多少写多少，不编许可


class TestObserve(unittest.TestCase):
    """观察段：唯一的 LLM 环节，可选、可失败、失败就当没有。"""

    def setUp(self):
        FakeClient.calls = []

    def test_observed_section_and_notes_move_out_of_timeline(self):
        t = played(trip_id="test-observe")
        with mock.patch.object(report.llm, "has_key", return_value=True), \
                mock.patch.object(report.llm, "DeepSeek", FakeClient):
            rep = report.make_report(t.state, t.pack, observe=True)
        text = rep["text"]
        self.assertTrue(rep["meta"]["observed"])
        self.assertIn(report.RULE_OBSERVE, text)
        self.assertIn("你说，这条街的光线像老照片", text)
        self.assertNotIn("🗨", text)        # 自语已经进了观察段，时间线不再重复
        call = FakeClient.calls[0]
        self.assertEqual(call["kw"]["temperature"], 0.5)
        self.assertEqual(call["kw"]["max_tokens"], 1200)
        for hard in ("分行诗体", "禁心理断言", "逐字引用"):
            self.assertIn(hard, call["system"])
        self.assertIn("这条街的光线像老照片", call["user"])   # 事实清单带上了自语
        self.assertIn("足迹：", call["user"])

    def test_failure_falls_back_to_timeline(self):
        t = played(trip_id="test-observe-fail")

        class Boom(FakeClient):
            def chat(self, *a, **kw):
                raise RuntimeError("network down")

        with mock.patch.object(report.llm, "has_key", return_value=True), \
                mock.patch.object(report.llm, "DeepSeek", Boom):
            rep = report.make_report(t.state, t.pack, observe=True)
        self.assertFalse(rep["meta"]["observed"])
        self.assertNotIn(report.RULE_OBSERVE, rep["text"])
        self.assertIn("🗨「这条街的光线像老照片」", rep["text"])

    def test_no_key_means_no_observation(self):
        t = played(trip_id="test-observe-nokey")
        with mock.patch.object(report.llm, "has_key", return_value=False), \
                mock.patch.object(report.llm, "DeepSeek", FakeClient):
            rep = report.make_report(t.state, t.pack, observe=True)
        self.assertFalse(rep["meta"]["observed"])
        self.assertEqual(FakeClient.calls, [])
        self.assertIn("🗨", rep["text"])


class TestSave(unittest.TestCase):
    def test_writes_md_and_meta_json(self):
        t = played(trip_id="test-save")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            md = report.save_report(t.state, t.pack, out_dir=out)
            self.assertTrue(md.exists())
            self.assertEqual(md.name, "test-save_旅行报告.md")
            meta_path = out / "test-save_旅行报告.meta.json"
            self.assertTrue(meta_path.exists())
            text = md.read_text(encoding="utf-8")
            self.assertIn("卧游 · 旅行报告", text)
            self.assertIn(report.RULE_APPENDIX, text)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(set(meta), {"color", "photos", "observed"})
            self.assertFalse(meta["observed"])
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             sorted([md.name, meta_path.name]))

    def test_does_not_touch_active_txt(self):
        active = ROOT / "saves" / "active.txt"
        before = active.read_text(encoding="utf-8") if active.exists() else None
        t = played(trip_id="test-save-active")
        with tempfile.TemporaryDirectory() as tmp:
            report.save_report(t.state, t.pack, out_dir=Path(tmp))
        after = active.read_text(encoding="utf-8") if active.exists() else None
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
