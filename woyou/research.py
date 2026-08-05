# -*- coding: utf-8 -*-
"""卧游 · 调研阶段：为一座城市产出「调研报告」(research.md)。

调研报告是后续内容生成的事实底座——先让模型以研究者身份把真实的地名、
历史、掌故、物价、节令整理成文，再由 generate.py 把它转译成游戏内容包。
可选：WOYOU_WEB=1 时会尝试抓取维基导游/维基百科的条目摘要作为参考资料
（在部分网络环境下不可用，失败会静默跳过，不影响生成）。
"""
import json
import os
import urllib.parse
import urllib.request

RESEARCH_SYSTEM = (
    "你是一位严谨的旅行研究者，正在为一部真实向的旅行模拟做地方志功课。"
    "只写你有把握的真实信息：真实存在的地名（附当地语言写法）、真实历史、"
    "真实菜肴与物价量级。不确定的内容要么不写，要么明确标注「民间说法」。"
    "禁止发明不存在的店铺名、寺庙名、传说。用中文输出 Markdown。"
)

RESEARCH_PROMPT = """请为「{city}」写一份旅行调研报告，供后续制作文字旅行模拟使用。
按以下结构，内容务求真实、具体、有画面感（总计 2500-4000 字）：

## 城市概览
三五句话：这是一座什么样的城，它的气质与历史脉络。

## 城区结构
真实的城区/片区划分（3-5 片），每片一两句气质描述。

## 必访地点（12-16 处）
每处：名称（含当地语言写法）｜所在片区｜类型｜门票（当地货币，免费写0）｜
大致开放时间｜两三句：它真实的样子、为什么值得去、有什么容易被游客错过的细节。
类型尽量多样：寺庙/教堂、市场、老街、河岸/海边、园林、博物馆、眺望点、夜生活街区、书店咖啡等。

## 深巷（3-4 处）
游客地图上不显眼、但本地人珍视的真实去处（老咖啡馆、旧书店、小神祠、无名坂道等），
每处注明：通常是怎样的人会把它介绍给旅行者。

## 吃什么（8-10 样）
真实的地方吃食：名称｜大致价格（当地货币）｜在哪一带能吃到｜一两句它的来历或吃法。

## 会遇到的人（5-6 位）
这座城常见的本地人原型（市场里的老摊主、寺里的讲解志愿者、街头乐手、老店店主等），
每位配一段**真实的**文化背景或历史掌故素材（150-250字），要求属实、可讲成故事：
历史事件、民俗规矩、行业传统、地名由来等。注明与哪个地点相关。

## 风物与节令
每月天气特征（简表）、当地的节庆、这座城独有的时间感（早市/晚祷/夜市等）。

## 实用信息
当地货币与对人民币汇率量级、经济型住宿一晚价格、市内公共交通单程票价、
城际交通（到邻近旅行城市的火车/巴士价格）、一顿普通饭的价格区间。

## 掌故与传说（3-5 则）
流传较广的真实民间传说或历史轶事，逐则注明「史实」或「民间说法」。
{extra}"""


def _fetch_wiki_extract(host: str, title: str, timeout: int = 10) -> str:
    """MediaWiki API 拉纯文本摘要，尽力而为。"""
    params = urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": 1,
        "redirects": 1, "format": "json", "titles": title,
    })
    url = f"https://{host}/w/api.php?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "woyou/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pages = data.get("query", {}).get("pages", {})
    for p in pages.values():
        text = p.get("extract", "")
        if text:
            return text[:6000]
    return ""


def gather_web_refs(city: str, city_en: str) -> str:
    """可选的网络参考资料（WOYOU_WEB=1 才启用）。"""
    if os.environ.get("WOYOU_WEB") != "1":
        return ""
    chunks = []
    for host, title in [
        ("en.wikivoyage.org", city_en or city),
        ("zh.wikipedia.org", city),
    ]:
        try:
            text = _fetch_wiki_extract(host, title)
            if text:
                chunks.append(f"【{host} · {title}】\n{text}")
        except Exception:
            continue
    if not chunks:
        return ""
    return ("\n\n---\n以下是抓取到的参考资料，可用于核对事实"
            "（以你的可靠知识为准，资料仅作补充）：\n\n" + "\n\n".join(chunks))


def build_research(client, city: str, city_en: str = "") -> str:
    """产出调研报告 markdown 文本。"""
    extra = gather_web_refs(city, city_en)
    prompt = RESEARCH_PROMPT.format(city=city, extra=extra)
    return client.chat(RESEARCH_SYSTEM, prompt,
                       temperature=0.4, max_tokens=7000)
