# -*- coding: utf-8 -*-
"""卧游 · 基础工具：路径、环境变量、稳定随机数、文本匹配。"""
import hashlib
import json
import os
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT / "content"
SAVE_DIR = ROOT / "saves"

# 一天 10 刻 (t = 0..9)，每 2 刻为一个时段
SLOTS = ["清晨", "上午", "午后", "黄昏", "夜晚"]

# 跨入新时段时的通用过场句（与城市无关）
SLOT_TURN = {
    "上午": "日头渐渐升高，街上的人多了起来。",
    "午后": "过了正午，光线变得厚实而慵懒。",
    "黄昏": "暮色漫上来，天边烧起一层暖色。",
    "夜晚": "夜色落定，灯火次第亮起。",
}


def slot_of(t: int) -> str:
    return SLOTS[max(0, min(int(t), 9)) // 2]


def stable_rng(*parts) -> random.Random:
    """由若干片段派生的确定性随机源——同种子同参数永远同结果。"""
    key = ":".join(str(p) for p in parts)
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def load_env() -> None:
    """读取项目根目录 .env（若存在），只设置尚未存在的环境变量。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v:
                os.environ.setdefault(k, v)
    except OSError:
        pass


def slugify(text: str) -> str:
    """尽量生成 ascii slug；纯中文等无法转写时用短哈希兜底。"""
    s = text.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "city-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
    return s


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def fmt_money(symbol: str, n: int) -> str:
    return f"{symbol}{n:,}"


def norm(s: str) -> str:
    """匹配用的归一化：去空白、casefold、去全角空格与常见标点。"""
    s = s.strip().casefold()
    return re.sub(r"[\s·・'\"“”‘’、，,。.!?！？~～-]+", "", s)


def fuzzy_pick(query: str, candidates):
    """candidates: [(key, [名字们])]。全等优先，其次相互包含。返回 key 或 None。"""
    q = norm(query)
    if not q:
        return None
    partial = []
    for key, names in candidates:
        for name in names:
            n = norm(str(name))
            if not n:
                continue
            if n == q:
                return key
            if q in n or n in q:
                partial.append((abs(len(n) - len(q)), key))
    if partial:
        partial.sort()
        return partial[0][1]
    return None


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
