"""
构建 latest_ranks.json：
1. 加载最近两天的 JSON 快照（男频为主 + 女频辅助）
2. 按 channel:name 对比趋势（新上榜/掉榜/排名变化/阅读量变化）
3. 可选调用 OpenAI 兼容 API 生成 AI 总结
4. 输出 latest_ranks.json + trends/YYYY-MM-DD.json
"""
import os
import re
import json
import glob
import sys
import argparse
from urllib.parse import quote

SNAPSHOT_GLOB = "fanqie_ranks_*.json"
SNAPSHOT_PREFIX = "fanqie_ranks_"
# 兼容旧女频快照（只读，迁移期）
LEGACY_SNAPSHOT_GLOB = "fanqie_female_new_ranks_*.json"

# OpenCode Zen 免费接口（key=public，无需 GitHub Secrets）
# 文档: https://opencode.ai/docs/zen
DEFAULT_API_BASE_URL = "https://opencode.ai/zen/v1"
DEFAULT_API_KEY = "public"
DEFAULT_API_MODEL = "deepseek-v4-flash-free"
# 免费模型限流时自动回退（仍全部免费）
FREE_MODEL_FALLBACKS = [
    "deepseek-v4-flash-free",
    "laguna-s-2.1-free",
    "big-pickle",
    "mimo-v2.5-free",
]
# 免费额度有限：默认只给男频做 AI，女频走规则摘要
DEFAULT_AI_CHANNELS = "male"


def is_rate_limit_error(err: Exception) -> bool:
    text = str(err).lower()
    return any(
        x in text
        for x in ("rate limit", "freeusagelimit", "429", "too many", "usage limit")
    )


def chat_completion(client, model: str, messages: list, max_tokens: int = 500,
                    temperature: float = 0.7):
    """调用 chat.completions，主模型限流时自动切换免费备用模型。"""
    import time

    models = []
    for m in [model] + FREE_MODEL_FALLBACKS:
        if m and m not in models:
            models.append(m)

    last_err = None
    for mid in models:
        for attempt in range(1, 4):
            try:
                response = client.chat.completions.create(
                    model=mid,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if mid != model:
                    print(f"    🔁 已切换免费模型: {mid}")
                return response, mid
            except Exception as e:
                last_err = e
                if is_rate_limit_error(e):
                    wait = min(45, 8 * attempt)
                    print(f"    ⏳ {mid} 限流 (第{attempt}次)，{wait}s 后重试/换模...")
                    time.sleep(wait)
                    # 同一模型重试 2 次后换下一个免费模型
                    if attempt >= 2:
                        break
                else:
                    # 非限流错误：换模型再试
                    print(f"    ⚠️  {mid} 失败: {e}")
                    break
    raise last_err or RuntimeError("AI 调用失败")


def cat_key(channel: str, name: str) -> str:
    """跨频道唯一分类键。"""
    channel = channel or "male"
    return f"{channel}:{name}"


def ensure_cat_key(cat: dict) -> str:
    if cat.get("key"):
        return cat["key"]
    return cat_key(cat.get("channel", "male"), cat.get("name", ""))


def parse_reads(reads_str: str) -> float:
    """将 '15.2万' 这样的字符串转为数值，用于比较。"""
    if not reads_str or reads_str == "未知":
        return 0
    s = reads_str.strip().replace(",", "")
    try:
        if "万" in s:
            return float(s.replace("万", "")) * 10000
        return float(s)
    except ValueError:
        return 0


def format_reads_change(diff: float) -> str:
    """格式化阅读量变化。"""
    if abs(diff) >= 10000:
        return f"{'+' if diff > 0 else ''}{diff / 10000:.1f}万"
    return f"{'+' if diff > 0 else ''}{int(diff)}"


def load_snapshot(path: str) -> dict:
    """加载一个 JSON 快照文件。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compare_categories(today_cats: list, prev_cats: list) -> dict:
    """
    对比两天的分类数据，返回每个分类的趋势信息。
    key = channel:name（兼容旧数据仅用 name）
    """
    # 构建 prev 的索引: cat_key -> {url: ...}
    prev_index = {}
    for cat in prev_cats:
        key = ensure_cat_key(cat)
        # 旧快照无 channel 时，同时用 name 兜底
        url_map = {}
        for i, book in enumerate(cat.get("books", [])):
            url_map[book["url"]] = {
                "rank": i + 1,
                "reads": book.get("reads", "未知"),
                "title": book.get("title", "未知"),
                "intro": book.get("intro", "暂无简介"),
            }
        prev_index[key] = url_map
        if key != cat.get("name"):
            prev_index.setdefault(cat.get("name", ""), url_map)

    trends = {}
    for cat in today_cats:
        cat_name = cat["name"]
        key = ensure_cat_key(cat)
        prev_urls = prev_index.get(key) or prev_index.get(cat_name, {})
        today_books = cat.get("books", [])

        new_books = []
        dropped_books = []
        risers = []
        fallers = []
        reads_growth = []

        today_urls = set()
        for i, book in enumerate(today_books):
            url = book["url"]
            today_urls.add(url)
            today_rank = i + 1
            title = book.get("title", "未知")

            if url in prev_urls:
                prev_info = prev_urls[url]
                prev_rank = prev_info["rank"]
                rank_change = prev_rank - today_rank  # 正数=上升

                if rank_change > 0:
                    risers.append({"title": title, "change": f"+{rank_change}"})
                elif rank_change < 0:
                    fallers.append({"title": title, "change": str(rank_change)})

                today_reads = parse_reads(book.get("reads", ""))
                prev_reads = parse_reads(prev_info["reads"])
                if today_reads > 0 and prev_reads > 0:
                    diff = today_reads - prev_reads
                    if diff != 0:
                        reads_growth.append(
                            {"title": title, "growth": format_reads_change(diff)}
                        )
            else:
                new_books.append(title)

        for url, info in prev_urls.items():
            if url not in today_urls:
                dropped_books.append({
                    "title": info["title"],
                    "intro": info.get("intro", "暂无简介")[:100],
                })

        risers.sort(key=lambda x: int(x["change"].replace("+", "")), reverse=True)
        fallers.sort(key=lambda x: int(x["change"]))
        reads_growth.sort(
            key=lambda x: parse_reads(x["growth"].replace("+", "")), reverse=True
        )

        trends[key] = {
            "name": cat_name,
            "channel": cat.get("channel", "male"),
            "new_count": len(new_books),
            "dropped_count": len(dropped_books),
            "new_books": new_books[:5],
            "dropped_books": dropped_books[:5],
            "top_risers": risers[:3],
            "top_fallers": fallers[:3],
            "reads_growth": reads_growth[:3],
            "summary": "",
        }

    return trends


def generate_trend_summary_text(cat_name: str, trend: dict) -> str:
    """生成基于规则的简短趋势文本（作为 AI 总结不可用时的 fallback）。"""
    parts = []
    if trend["new_count"] > 0:
        parts.append(f"新增{trend['new_count']}本上榜")
    if trend["dropped_count"] > 0:
        dropped_titles = [d["title"] if isinstance(d, dict) else d
                          for d in trend.get("dropped_books", [])]
        if dropped_titles:
            parts.append(f"{trend['dropped_count']}本掉出（{'、'.join('《' + t + '》' for t in dropped_titles)}）")
        else:
            parts.append(f"{trend['dropped_count']}本掉出")
    if trend["top_risers"]:
        r = trend["top_risers"][0]
        parts.append(f"《{r['title']}》排名上升{r['change']}位")
    if trend["reads_growth"]:
        g = trend["reads_growth"][0]
        parts.append(f"《{g['title']}》阅读量{g['growth']}")
    if not parts:
        parts.append("榜单无明显变动")
    return "；".join(parts) + "。"


def build_ai_prompt(cat_name: str, cat: dict, trend: dict) -> str:
    """构建 AI 总结的 prompt（统一模板）。"""
    # 当前榜单书籍
    intros = []
    for i, book in enumerate(cat.get("books", [])[:20]):
        intros.append(
            f"{i+1}. 《{book['title']}》- {book.get('author', '未知')}\n"
            f"   在读：{book.get('reads', '未知')}\n"
            f"   简介：{book.get('intro', '无')[:200]}"
        )
    intros_text = "\n".join(intros)

    # 新上榜书籍
    new_books = trend.get("new_books", [])
    new_text = "、".join(f"《{t}》" for t in new_books) if new_books else "无"

    # 掉出榜单书籍（含简介）
    dropped = trend.get("dropped_books", [])
    if dropped:
        dropped_lines = []
        for d in dropped:
            if isinstance(d, dict):
                dropped_lines.append(f"《{d['title']}》（{d.get('intro', '暂无简介')[:50]}）")
            else:
                dropped_lines.append(f"《{d}》")
        dropped_text = "、".join(dropped_lines)
    else:
        dropped_text = "无"

    # 排名变动
    risers = trend.get("top_risers", [])
    risers_text = "、".join(f"《{r['title']}》{r['change']}" for r in risers) if risers else "无"
    fallers = trend.get("top_fallers", [])
    fallers_text = "、".join(f"《{f['title']}》{f['change']}" for f in fallers) if fallers else "无"

    return f"""你是一位网文行业分析师。请根据以下数据，为番茄小说「{cat_name}」分类新书榜生成结构化分析。

## 当前榜单 Top 20
{intros_text}

## 榜单变动
- 新上榜：{new_text}
- 掉出榜单：{dropped_text}
- 排名上升：{risers_text}
- 排名下降：{fallers_text}

## 输出要求（请严格按以下格式输出，使用 Markdown）

**🔥 题材趋势**
用1-2句话总结当前分类的主流题材和高频元素（如穿书/重生/系统/种田等），点明哪些设定扎堆出现。

**📖 读者偏好**
用1句话概括读者口味方向（甜宠/虐/爽/日常/暗黑等），以及金手指类型偏好。

**🆕 新上榜作品**
列出新上榜书名，每本用一句话点评其题材亮点或差异化卖点。

**📉 掉出榜单**
列出掉出书名及其题材方向，简要分析可能掉出的原因（如题材饱和、同质化等）。

**💡 值得关注**
挑1-2本有差异化潜力的作品，说明理由。

要求：每个板块2-3句话，总字数250字以内。语言简洁专业，像行业快报。"""


BATCH_SIZE = 2  # 免费接口限流较严，每批少合并几个

MARKET_PERIODS = [("7", 7), ("14", 14), ("30", 30), ("all", None)]

# 男频综合赛道（主）+ 女频赛道（辅）
GENRE_GROUPS = [
    # —— 男频 ——
    {"name": "东方玄幻", "channel": "male",
     "categories": ["东方仙侠", "传统玄幻", "玄幻脑洞", "都市修真"]},
    {"name": "西方奇幻", "channel": "male",
     "categories": ["西方奇幻"]},
    {"name": "都市高武", "channel": "male",
     "categories": ["都市高武", "都市日常", "都市脑洞", "都市种田", "战神赘婿"]},
    {"name": "历史抗战", "channel": "male",
     "categories": ["历史古代", "历史脑洞", "抗战谍战"]},
    {"name": "科幻末世", "channel": "male",
     "categories": ["科幻末世"]},
    {"name": "悬疑灵异", "channel": "male",
     "categories": ["悬疑脑洞", "悬疑灵异"]},
    {"name": "游戏衍生", "channel": "male",
     "categories": ["游戏体育", "动漫衍生", "男频衍生"]},
    # —— 女频（辅助）——
    {"name": "古风言情", "channel": "female",
     "categories": ["古风世情", "古言脑洞", "宫斗宅斗", "种田"]},
    {"name": "现代言情", "channel": "female",
     "categories": ["现言脑洞", "豪门总裁", "职场婚恋", "青春甜宠"]},
    {"name": "幻想言情", "channel": "female",
     "categories": ["玄幻言情", "科幻末世", "悬疑脑洞", "女频悬疑"]},
    {"name": "快穿衍生", "channel": "female",
     "categories": ["快穿", "女频衍生"]},
    {"name": "年代民国", "channel": "female",
     "categories": ["年代", "民国言情"]},
    {"name": "娱乐星光", "channel": "female",
     "categories": ["星光璀璨"]},
]

MARKET_KEYWORDS = [
    # 男频高频
    "系统", "重生", "穿越", "无敌", "签到", "暴兵", "基建", "种田", "赘婿",
    "战神", "兵王", "神医", "鉴宝", "修仙", "仙侠", "玄幻", "高武", "灵气复苏",
    "末日", "末世", "丧尸", "囤货", "异能", "星际", "机甲", "科幻", "赛博",
    "历史", "三国", "明朝", "抗日", "谍战", "无限流", "诸天", "综武", "同人",
    "游戏", "电竞", "直播", "悬疑", "灵异", "克苏鲁", "脑洞", "无CP",
    # 女频辅助
    "穿书", "快穿", "空间", "团宠", "萌宝", "女配", "炮灰", "宅斗", "宫斗",
    "豪门", "总裁", "甜宠", "先婚后爱", "年代", "民国", "娱乐圈",
]


def build_batch_ai_prompt(batch: list) -> str:
    """构建批量 AI 总结的 prompt。

    batch: list of (cat_name, cat_data, trend_data) tuples
    """
    sections = []
    for cat_name, cat, trend in batch:
        intros = []
        for i, book in enumerate(cat.get("books", [])[:20]):
            intros.append(
                f"{i+1}. 《{book['title']}》- {book.get('author', '未知')}\n"
                f"   在读：{book.get('reads', '未知')}\n"
                f"   简介：{book.get('intro', '无')[:200]}"
            )
        intros_text = "\n".join(intros)

        new_books = trend.get("new_books", [])
        new_text = "、".join(f"《{t}》" for t in new_books) if new_books else "无"

        dropped = trend.get("dropped_books", [])
        if dropped:
            dropped_lines = []
            for d in dropped:
                if isinstance(d, dict):
                    dropped_lines.append(
                        f"《{d['title']}》（{d.get('intro', '暂无简介')[:50]}）"
                    )
                else:
                    dropped_lines.append(f"《{d}》")
            dropped_text = "、".join(dropped_lines)
        else:
            dropped_text = "无"

        risers = trend.get("top_risers", [])
        risers_text = (
            "、".join(f"《{r['title']}》{r['change']}" for r in risers)
            if risers else "无"
        )
        fallers = trend.get("top_fallers", [])
        fallers_text = (
            "、".join(f"《{f['title']}》{f['change']}" for f in fallers)
            if fallers else "无"
        )

        sections.append(
            f"### 分类：{cat_name}\n\n"
            f"**当前榜单 Top 20：**\n{intros_text}\n\n"
            f"**榜单变动：**\n"
            f"- 新上榜：{new_text}\n"
            f"- 掉出榜单：{dropped_text}\n"
            f"- 排名上升：{risers_text}\n"
            f"- 排名下降：{fallers_text}"
        )

    all_sections = "\n\n---\n\n".join(sections)
    cat_names = [b[0] for b in batch]

    output_examples = "\n\n".join(
        f"===BEGIN: {name}===\n"
        f"**🔥 题材趋势** ...\n"
        f"**📖 读者偏好** ...\n"
        f"**🆕 新上榜作品** ...\n"
        f"**📉 掉出榜单** ...\n"
        f"**💡 值得关注** ...\n"
        f"===END: {name}==="
        for name in cat_names
    )

    return (
        f"你是一位网文行业分析师。请根据以下数据，"
        f"为番茄小说的多个分类新书榜分别生成结构化分析。\n\n"
        f"{all_sections}\n\n"
        f"## 输出要求\n\n"
        f"请严格按照以下格式，为每个分类分别输出分析。"
        f"每个分类的分析必须包裹在对应的标记中：\n\n"
        f"{output_examples}\n\n"
        f"每个板块2-3句话，每个分类总字数250字以内。"
        f"语言简洁专业，像行业快报。\n"
        f"注意：必须为每个分类都输出完整分析，不可省略任何分类。"
    )


def parse_batch_response(response_text: str, cat_names: list) -> dict:
    """解析批量 AI 响应，返回 {cat_name: summary} 字典。"""
    results = {}
    for name in cat_names:
        pattern = rf"===BEGIN:\s*{re.escape(name)}\s*===(.*?)===END:\s*{re.escape(name)}\s*==="
        match = re.search(pattern, response_text, re.DOTALL)
        if match:
            summary = match.group(1).strip()
            if summary:
                results[name] = summary
    return results


def _save_trends_incremental(trend_path: str, date: str,
                             prev_date: str, trends: dict):
    """增量保存趋势数据到文件（每批成功后立即写入）。"""
    if not trend_path:
        return
    trend_output = {
        "date": date,
        "prev_date": prev_date,
        "trends": trends,
    }
    with open(trend_path, "w", encoding="utf-8") as f:
        json.dump(trend_output, f, ensure_ascii=False, indent=2)


def api_type_filename(type_name: str) -> str:
    """将类型名转成适合作为静态 JSON 文件名的名称。"""
    name = (type_name or "").strip()
    name = re.sub(r"[\\/]+", "_", name)
    name = re.sub(r"[^\w\u4e00-\u9fff\s-]", "_", name)
    name = re.sub(r"\s+", "_", name).strip("._")
    return name or "unknown"


def write_json(path: str, payload: dict):
    """统一写 JSON，确保中文可读。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_lastest_api(output: dict, base_dir: str):
    """生成静态 lastest 数据接口。

    GitHub Pages 不支持动态 query API，因此这里将 type 参数映射为静态文件：
    - api/lastest/all.json：全量数据
    - api/lastest/<type>.json：单个类型数据
    - api/lastest.json / api/lastest/index.json：类型索引
    """
    api_root = os.path.join(base_dir, "api")
    lastest_dir = os.path.join(api_root, "lastest")
    os.makedirs(lastest_dir, exist_ok=True)
    for old_path in glob.glob(os.path.join(lastest_dir, "*.json")):
        os.remove(old_path)

    date = output.get("date", "")
    prev_date = output.get("prev_date", "")
    categories = output.get("categories", [])

    all_payload = {
        "type": "all",
        "date": date,
        "prev_date": prev_date,
        "categories": categories,
    }
    write_json(os.path.join(lastest_dir, "all.json"), all_payload)

    types = [{
        "type": "all",
        "url": "api/lastest/all.json",
        "category_count": len(categories),
        "book_count": sum(len(cat.get("books", [])) for cat in categories),
    }]

    used_filenames = {"all"}
    for cat in categories:
        type_name = cat.get("name", "")
        channel = cat.get("channel", "male")
        # 文件名带频道前缀，避免男女同名分类冲突
        filename = api_type_filename(f"{channel}_{type_name}")
        base_filename = filename
        suffix = 2
        while filename in used_filenames:
            filename = f"{base_filename}_{suffix}"
            suffix += 1
        used_filenames.add(filename)

        payload = {
            "type": type_name,
            "channel": channel,
            "key": ensure_cat_key(cat),
            "date": date,
            "prev_date": prev_date,
            "category": cat,
            "categories": [cat],
        }
        write_json(os.path.join(lastest_dir, f"{filename}.json"), payload)

        url = f"api/lastest/{quote(filename)}.json"
        types.append({
            "type": type_name,
            "channel": channel,
            "key": ensure_cat_key(cat),
            "url": url,
            "book_count": len(cat.get("books", [])),
        })

    index_payload = {
        "date": date,
        "prev_date": prev_date,
        "primary_channel": output.get("primary_channel", "male"),
        "types": types,
    }
    write_json(os.path.join(lastest_dir, "index.json"), index_payload)
    write_json(os.path.join(api_root, "lastest.json"), index_payload)

    return lastest_dir


def parse_change(change: str) -> int:
    """解析 '+3' / '-2' 这类排名变化。"""
    try:
        return int(str(change or "0").replace("+", ""))
    except ValueError:
        return 0


def load_trend_rows(trends_dir: str) -> list:
    """加载全部趋势归档，按日期升序排列。"""
    rows = []
    for path in sorted(glob.glob(os.path.join(trends_dir, "*.json"))):
        try:
            data = load_snapshot(path)
            rows.append({
                "date": data.get("date", ""),
                "prev_date": data.get("prev_date", ""),
                "trends": data.get("trends", {}),
            })
        except Exception as e:
            print(f"  ⚠️  跳过趋势文件 {path}: {e}")
    return sorted([r for r in rows if r["date"]], key=lambda x: x["date"])


def summarize_market_rows(rows: list) -> dict:
    """汇总某个分类在一组趋势行中的动能指标。"""
    totals = {
        "new_count": 0,
        "dropped_count": 0,
        "riser_count": 0,
        "faller_count": 0,
        "read_count": 0,
        "read_growth_total": 0,
        "active_days": 0,
    }
    for row in rows:
        trend = row.get("trend") or {}
        riser_count = len(trend.get("top_risers", []))
        faller_count = len(trend.get("top_fallers", []))
        read_count = len(trend.get("reads_growth", []))
        read_growth_total = sum(
            parse_reads(item.get("growth", ""))
            for item in trend.get("reads_growth", [])
        )
        totals["new_count"] += int(trend.get("new_count", 0) or 0)
        totals["dropped_count"] += int(trend.get("dropped_count", 0) or 0)
        totals["riser_count"] += riser_count
        totals["faller_count"] += faller_count
        totals["read_count"] += read_count
        totals["read_growth_total"] += read_growth_total
        if (
            trend.get("new_count", 0) or trend.get("dropped_count", 0)
            or riser_count or faller_count or read_count
        ):
            totals["active_days"] += 1
    return totals


def market_score(totals: dict) -> int:
    """计算全站热点分：综合赛道和具体分类只看新增在读量。"""
    return round(totals["read_growth_total"])


def format_market_reads(value: float) -> str:
    """格式化市场层面的新增在读量。"""
    if abs(value) >= 10000:
        return f"{value / 10000:.1f}万"
    return str(round(value))


def collect_market_hot_types(cat_entries: list, rows_window: list) -> list:
    """统计具体分类热度。cat_entries: [{key, name, channel}, ...]。"""
    result = []
    for entry in cat_entries:
        key = entry["key"]
        rows = [
            {"trend": row.get("trends", {}).get(key)}
            for row in rows_window
            if row.get("trends", {}).get(key)
        ]
        # 兼容旧 trends 用纯 name 做 key
        if not rows:
            name = entry["name"]
            rows = [
                {"trend": row.get("trends", {}).get(name)}
                for row in rows_window
                if row.get("trends", {}).get(name)
            ]
        totals = summarize_market_rows(rows)
        score = market_score(totals)
        if score <= 0:
            continue
        result.append({
            "key": key,
            "name": entry["name"],
            "channel": entry.get("channel", "male"),
            "score": score,
            "new_count": totals["new_count"],
            "dropped_count": totals["dropped_count"],
            "read_count": totals["read_count"],
            "read_growth_total": totals["read_growth_total"],
            "active_days": totals["active_days"],
        })
    return sorted(
        result,
        key=lambda x: (x["read_growth_total"], x["read_count"]),
        reverse=True
    )


def collect_market_hot_genres(cat_entries: list, hot_types: list) -> list:
    """按综合赛道聚合具体分类热度（优先男频赛道）。"""
    type_by_key = {item["key"]: item for item in hot_types if item.get("key")}
    type_by_name_channel = {
        (item.get("channel", "male"), item["name"]): item for item in hot_types
    }
    name_set_by_channel = {}
    for e in cat_entries:
        name_set_by_channel.setdefault(e.get("channel", "male"), set()).add(e["name"])

    genres = []
    for group in GENRE_GROUPS:
        channel = group.get("channel", "male")
        available = name_set_by_channel.get(channel, set())
        matched = []
        for name in group["categories"]:
            if name not in available:
                continue
            item = type_by_name_channel.get((channel, name))
            if not item:
                key = cat_key(channel, name)
                item = type_by_key.get(key, {
                    "key": key,
                    "name": name,
                    "channel": channel,
                    "score": 0,
                    "new_count": 0,
                    "dropped_count": 0,
                    "read_count": 0,
                    "read_growth_total": 0,
                    "active_days": 0,
                })
            matched.append(item)
        if not matched:
            continue
        read_growth_total = sum(item["read_growth_total"] for item in matched)
        if read_growth_total <= 0:
            continue
        lead = sorted(
            matched,
            key=lambda x: (x["read_growth_total"], x["read_count"]),
            reverse=True
        )[0]
        genres.append({
            "name": group["name"],
            "channel": channel,
            "score": round(read_growth_total),
            "new_count": sum(item["new_count"] for item in matched),
            "dropped_count": sum(item["dropped_count"] for item in matched),
            "read_count": sum(item["read_count"] for item in matched),
            "read_growth_total": read_growth_total,
            "active_days": sum(item["active_days"] for item in matched),
            "lead_category": lead["name"],
            "categories": [item["name"] for item in matched],
        })
    return sorted(
        genres,
        key=lambda x: (x["read_growth_total"], x["read_count"]),
        reverse=True
    )


def add_theme_hits(score_map: dict, text: str, category_name: str, weight: int):
    """给命中的题材关键词加权。"""
    source = str(text or "")
    if not source:
        return
    for keyword in MARKET_KEYWORDS:
        if keyword not in source:
            continue
        item = score_map[keyword]
        item["count"] += weight
        item["categories"].add(category_name)


def collect_market_hot_themes(output: dict, rows_window: list,
                              cat_entries: list) -> list:
    """只统计近期新上榜作品中的高频题材词。优先男频，女频权重减半。"""
    score_map = {
        name: {"name": name, "count": 0, "categories": set()}
        for name in MARKET_KEYWORDS
    }
    latest_book_map = {}
    for cat in output.get("categories", []):
        for book in cat.get("books", []):
            title = book.get("title", "")
            if title:
                latest_book_map[title] = book

    for row in rows_window:
        for entry in cat_entries:
            key = entry["key"]
            trend = row.get("trends", {}).get(key) or row.get("trends", {}).get(entry["name"])
            if not trend:
                continue
            weight = 1 if entry.get("channel", "male") == "male" else 0  # 女频不计入全站题材
            if weight <= 0:
                # 女频辅助：降低权重但仍可见
                weight = 0
                # 跳过女频题材，保持全站热点以男频为主
                continue
            for title in trend.get("new_books", []):
                book = latest_book_map.get(title, {})
                add_theme_hits(
                    score_map,
                    f"{title} {book.get('intro', '')}",
                    entry["name"],
                    weight
                )

    themes = []
    for item in score_map.values():
        if item["count"] <= 0:
            continue
        themes.append({
            "name": item["name"],
            "count": item["count"],
            "category_count": len(item["categories"]),
        })
    return sorted(
        themes,
        key=lambda x: (x["count"], x["category_count"]),
        reverse=True
    )


def build_rule_market_summary(period_label: str, hot_genres: list,
                              hot_types: list, hot_themes: list) -> str:
    """基于统计结果生成全站热点兜底文案。"""
    top_genres = "、".join(item["name"] for item in hot_genres[:2])
    top_types = "、".join(item["name"] for item in hot_types[:3])
    top_themes = "、".join(item["name"] for item in hot_themes[:6])
    if not top_genres and not top_types:
        return f"{period_label}暂无足够数据判断全站热点。"
    return (
        f"{period_label}里，{top_genres or top_types} 的阅读增长更强，"
        f"具体分类以 {top_types} 的新增在读更集中；新书题材上 {top_themes} "
        f"更高频，说明读者仍偏好强设定、强情绪钩子和明确爽点。"
    )


def build_market_summary_payload(output: dict, trends_dir: str) -> dict:
    """生成全站热点统计和规则兜底总结（以男频为主）。"""
    # 全站热点默认只看男频；女频数据仍在 categories 中可单独查看
    male_cats = [
        {
            "key": ensure_cat_key(cat),
            "name": cat.get("name", ""),
            "channel": cat.get("channel", "male"),
        }
        for cat in output.get("categories", [])
        if cat.get("channel", "male") == "male"
    ]
    # 若还没有 channel 字段（旧数据），全部当作可用
    if not male_cats:
        male_cats = [
            {
                "key": ensure_cat_key(cat),
                "name": cat.get("name", ""),
                "channel": cat.get("channel", "male"),
            }
            for cat in output.get("categories", [])
        ]

    trend_rows = load_trend_rows(trends_dir)
    periods = {}

    for key, days in MARKET_PERIODS:
        rows_window = trend_rows if days is None else trend_rows[-days:]
        period_label = "全部样本" if days is None else f"近 {days} 日"
        hot_types = collect_market_hot_types(male_cats, rows_window)
        hot_genres = collect_market_hot_genres(male_cats, hot_types)
        hot_themes = collect_market_hot_themes(output, rows_window, male_cats)
        fallback_summary = build_rule_market_summary(
            period_label, hot_genres, hot_types, hot_themes
        )
        periods[key] = {
            "period": period_label,
            "source": "rule",
            "summary": fallback_summary,
            "fallback_summary": fallback_summary,
            "hot_genres": hot_genres[:5],
            "hot_types": hot_types[:6],
            "hot_themes": hot_themes[:14],
        }

    return {
        "date": output.get("date", ""),
        "prev_date": output.get("prev_date", ""),
        "primary_channel": "male",
        "periods": periods,
    }


def build_market_ai_prompt(payload: dict) -> str:
    """构建全站热点 AI 总结 prompt。"""
    sections = []
    for key, data in payload.get("periods", {}).items():
        genres = "、".join(
            f"{item['name']}(新增在读{format_market_reads(item.get('read_growth_total', 0))}, "
            f"增长作品{item.get('read_count', 0)})"
            for item in data.get("hot_genres", [])[:5]
        )
        types = "、".join(
            f"{item['name']}(新增在读{format_market_reads(item.get('read_growth_total', 0))}, "
            f"增长作品{item.get('read_count', 0)})"
            for item in data.get("hot_types", [])[:6]
        )
        themes = "、".join(
            f"{item['name']}(新书{item['count']}本)"
            for item in data.get("hot_themes", [])[:10]
        )
        sections.append(
            f"周期 {key} / {data['period']}:\n"
            f"- 综合赛道（按阅读增长量排序）: {genres or '无'}\n"
            f"- 具体分类（按阅读增长量排序）: {types or '无'}\n"
            f"- 高频题材（只统计新上榜作品，按新书数量排序）: {themes or '无'}\n"
            f"- 规则兜底: {data['fallback_summary']}"
        )

    return f"""你是一位网文市场编辑，请根据番茄男频新书榜的统计结果，为每个周期生成一段全站热点判断。

{chr(10).join(sections)}

要求：
1. 只基于给定统计，不要编造未出现的类型或题材。
2. 每个周期输出 1 段中文，80-140 字。
3. 综合赛道和具体分类必须按“新增在读/阅读增长”解读，不要写“榜单动能”或“排名动能”。
4. 高频题材必须按“新上榜作品数量”解读，不要混入存量榜单或摘要题材。
5. 点明综合赛道、具体分类、新书题材关键词，以及一句编辑判断。
6. 输出严格 JSON，不要 Markdown，不要解释，格式如下：
{{
  "7": "总结文本",
  "14": "总结文本",
  "30": "总结文本",
  "all": "总结文本"
}}"""


def parse_json_object(text: str) -> dict:
    """尽量从模型响应中提取 JSON 对象。"""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def enrich_market_summary_with_ai(payload: dict, api_key: str,
                                  base_url: str, model: str) -> dict:
    """使用 AI 改写全站热点总结；失败时保留规则兜底。"""
    try:
        from openai import OpenAI
    except ImportError:
        print("⚠️  openai 库未安装，跳过全站热点 AI 总结。")
        return payload

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)
        response, used_model = chat_completion(
            client, model,
            messages=[{"role": "user", "content": build_market_ai_prompt(payload)}],
            max_tokens=900,
            temperature=0.5,
        )
        parsed = parse_json_object(response.choices[0].message.content)
        for key, summary in parsed.items():
            if key in payload["periods"] and isinstance(summary, str) and summary.strip():
                payload["periods"][key]["summary"] = summary.strip()
                payload["periods"][key]["source"] = "ai"
        print(f"✅ 全站热点 AI 总结已生成 ({used_model})")
    except Exception as e:
        print(f"⚠️  全站热点 AI 总结失败，使用规则兜底: {e}")

    return payload



def is_rule_summary(summary: str) -> bool:
    """判断一个总结是否为规则模板生成的（非 AI）。
    规则摘要特征：短小、分号分隔、以句号结尾、无换行。
    """
    if not summary:
        return True
    if summary == "首日数据，暂无趋势对比。":
        return True
    # 规则摘要一般 < 150 字，用分号分隔，无换行
    if len(summary) < 150 and "；" in summary and "\n" not in summary:
        return True
    return False


def generate_ai_summaries(categories: list, trends: dict,
                          api_key: str, base_url: str,
                          model: str, force: bool = False,
                          existing_trends: dict = None,
                          trend_path: str = None,
                          trend_date: str = "",
                          prev_date: str = "") -> dict:
    """通过 OpenAI 兼容 API 为每个分类生成 AI 总结。

    采用批量合并策略（每 BATCH_SIZE 个分类一次调用）减少 API 调用次数，
    并在每批成功后增量保存，避免中途失败丢失已完成的结果。
    批量失败的分类会自动降级为逐个重试。
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("⚠️  openai 库未安装，跳过 AI 总结。pip install openai")
        return trends

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)
    existing_trends = existing_trends or {}
    ai_channels = {
        c.strip()
        for c in os.environ.get("AI_CHANNELS", DEFAULT_AI_CHANNELS).split(",")
        if c.strip()
    }

    # 1. 筛选需要生成总结的分类
    pending = []  # (cat_name, cat_data, trend_data)
    skipped = 0
    channel_skipped = 0

    for cat in categories:
        cat_name = cat["name"]
        channel = cat.get("channel", "male")
        key = ensure_cat_key(cat)
        if key not in trends and cat_name not in trends:
            continue
        trend_ref = trends.get(key) or trends.get(cat_name)
        if not trend_ref:
            continue
        # 统一写回 key
        if key not in trends:
            trends[key] = trend_ref

        # 默认只 AI 男频，女频用规则摘要（省免费额度）
        if ai_channels and channel not in ai_channels:
            if not trend_ref.get("summary") or is_rule_summary(trend_ref.get("summary", "")):
                trends[key]["summary"] = generate_trend_summary_text(cat_name, trend_ref)
            channel_skipped += 1
            continue

        if not force:
            existing_summary = (
                existing_trends.get(key, {}).get("summary", "")
                or existing_trends.get(cat_name, {}).get("summary", "")
            )
            if existing_summary and not is_rule_summary(existing_summary):
                trends[key]["summary"] = existing_summary
                skipped += 1
                continue

        pending.append((cat_name, cat, trends[key]))

    if channel_skipped > 0:
        print(f"  🎯 AI 频道限制 {sorted(ai_channels)}：{channel_skipped} 个分类用规则摘要")

    if skipped > 0:
        print(f"  ⏭️  跳过 {skipped} 个已有 AI 总结的分类")

    if not pending:
        print("  ✅ 所有分类已有 AI 总结，无需生成")
        return trends

    # 2. 分批处理
    batches = [
        pending[i:i + BATCH_SIZE]
        for i in range(0, len(pending), BATCH_SIZE)
    ]
    failed_cats = []  # 批量失败后需单独重试的分类

    print(f"  📦 共 {len(pending)} 个分类，分 {len(batches)} 批处理"
          f"（每批最多 {BATCH_SIZE} 个）")

    for batch_idx, batch in enumerate(batches):
        batch_names = [b[0] for b in batch]
        print(f"\n  📦 第 {batch_idx + 1}/{len(batches)} 批: "
              f"{', '.join(batch_names)}")

        prompt = build_batch_ai_prompt(batch)

        batch_success = False
        try:
            response, used_model = chat_completion(
                client, model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500 * len(batch),
                temperature=0.7,
            )
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise ValueError("API 返回空内容")

            parsed = parse_batch_response(content, batch_names)
            if parsed:
                for name, summary in parsed.items():
                    matched = [b for b in batch if b[0] == name]
                    for m_name, m_cat, m_trend in matched:
                        m_key = ensure_cat_key(m_cat)
                        trends[m_key]["summary"] = summary
                        print(f"    ✅ {m_cat.get('channel', '')}/{name} ({used_model})")

                for name in batch_names:
                    if name not in parsed:
                        print(f"    ⚠️  未解析到: {name}（将单独重试）")
                        failed_cats.append(
                            next(b for b in batch if b[0] == name)
                        )

                _save_trends_incremental(
                    trend_path, trend_date, prev_date, trends
                )
                batch_success = True
                import time
                time.sleep(2)
            else:
                raise ValueError("批量响应解析失败，未匹配到任何分类")
        except Exception as e:
            print(f"    ❌ 批量失败: {e}")

        if not batch_success:
            print("    ↪️  将逐个重试本批分类")
            failed_cats.extend(batch)

    # 3. 对失败的分类逐个重试（降级为单分类 prompt）
    if failed_cats:
        print(f"\n  🔄 逐个重试 {len(failed_cats)} 个失败分类...")
        for cat_name, cat, trend in failed_cats:
            key = ensure_cat_key(cat)
            prompt = build_ai_prompt(cat_name, cat, trend)
            success = False
            try:
                response, used_model = chat_completion(
                    client, model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=500,
                    temperature=0.7,
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    raise ValueError("API 返回空内容")
                trends[key]["summary"] = content.strip()
                print(f"    ✅ {cat.get('channel', '')}/{cat_name} ({used_model})")
                _save_trends_incremental(
                    trend_path, trend_date, prev_date, trends
                )
                success = True
                import time
                time.sleep(2)
            except Exception as e:
                print(f"    ❌ {cat_name} 最终失败: {e}")

            if not success:
                print(f"    ❌ {cat_name} 最终失败")
                old = (
                    existing_trends.get(key, {}).get("summary", "")
                    or existing_trends.get(cat_name, {}).get("summary", "")
                )
                if old and not is_rule_summary(old):
                    trends[key]["summary"] = old
                    print(f"    ↩️  保留旧 AI 总结: {cat_name}")
                else:
                    trends[key]["summary"] = generate_trend_summary_text(
                        cat_name, trend
                    )

    return trends


def main():
    parser = argparse.ArgumentParser(description="构建 latest_ranks.json")
    parser.add_argument("--force", action="store_true",
                        help="强制重新生成所有 AI 总结，忽略已有总结")
    parser.add_argument("--date", type=str, default="",
                        help="指定目标日期 (YYYY-MM-DD)，默认使用最新快照")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    trends_dir = os.path.join(data_dir, "trends")
    os.makedirs(trends_dir, exist_ok=True)

    # 查找 JSON 快照（新格式优先，兼容旧女频文件名）
    snapshots = sorted(glob.glob(os.path.join(data_dir, SNAPSHOT_GLOB)))
    if not snapshots:
        snapshots = sorted(glob.glob(os.path.join(data_dir, LEGACY_SNAPSHOT_GLOB)))

    if not snapshots:
        print("未找到任何 JSON 快照文件。请先运行爬虫。")
        sys.exit(1)

    # 根据 --date 参数选择目标快照
    if args.date:
        target_date_compact = args.date.replace("-", "")
        candidates = [
            os.path.join(data_dir, f"{SNAPSHOT_PREFIX}{target_date_compact}.json"),
            os.path.join(data_dir, f"fanqie_female_new_ranks_{target_date_compact}.json"),
        ]
        target_path = next((p for p in candidates if os.path.exists(p)), None)
        if not target_path:
            print(f"❌ 未找到 {args.date} 的快照文件")
            sys.exit(1)
        latest_path = target_path
        target_idx = snapshots.index(target_path) if target_path in snapshots else -1
    else:
        latest_path = snapshots[-1]
        target_idx = len(snapshots) - 1

    latest_data = load_snapshot(latest_path)
    print(f"目标快照: {os.path.basename(latest_path)} ({latest_data['date']})")

    # 加载前一天的快照（如果有）
    prev_data = None
    prev_date = ""
    if target_idx > 0:
        prev_path = snapshots[target_idx - 1]
        prev_data = load_snapshot(prev_path)
        prev_date = prev_data.get("date", "")
        print(f"对比快照: {os.path.basename(prev_path)} ({prev_date})")

    # 加载已有的趋势数据（用于保留已有 AI 总结）
    existing_trends = {}
    trend_path = os.path.join(trends_dir, f"{latest_data['date']}.json")
    if os.path.exists(trend_path) and not args.force:
        try:
            with open(trend_path, "r", encoding="utf-8") as f:
                existing_trend_data = json.load(f)
                existing_trends = existing_trend_data.get("trends", {})
            ai_count = sum(1 for t in existing_trends.values()
                          if not is_rule_summary(t.get("summary", "")))
            rule_count = len(existing_trends) - ai_count
            print(f"已有趋势数据: {ai_count} 个 AI 总结, {rule_count} 个待补充")
        except Exception:
            pass

    if args.force:
        print("\n🔄 强制模式：将重新生成所有 AI 总结")

    # 对比趋势
    if prev_data:
        trends = compare_categories(
            latest_data["categories"], prev_data["categories"]
        )
    else:
        print("仅有一天数据，无法生成趋势对比。")
        trends = {
            ensure_cat_key(cat): {
                "name": cat.get("name", ""),
                "channel": cat.get("channel", "male"),
                "new_count": 0,
                "dropped_count": 0,
                "new_books": [],
                "dropped_books": [],
                "top_risers": [],
                "top_fallers": [],
                "reads_growth": [],
                "summary": "首日数据，暂无趋势对比。",
            }
            for cat in latest_data["categories"]
        }

    # ========== AI 总结：默认 OpenCode 免费 deepseek-v4-flash-free ==========
    # 空字符串（如未配置的 GitHub Secrets）会回落到公开免费配置，无需 Secrets
    api_base_url = (os.environ.get("API_BASE_URL") or DEFAULT_API_BASE_URL).strip()
    api_key = (os.environ.get("API_KEY") or DEFAULT_API_KEY).strip()
    api_model = (os.environ.get("API_MODEL") or DEFAULT_API_MODEL).strip()
    # 显式关闭：API_KEY=off / none / disable
    ai_disabled = api_key.lower() in {"off", "none", "disable", "disabled", "0", "false"}

    if not ai_disabled and api_base_url and api_key and api_model:
        print(f"\n正在使用 {api_model} 生成 AI 总结...")
        print(f"  API: {api_base_url}")
        print(f"  Key: {'public(free)' if api_key == 'public' else '***'}")
        trends = generate_ai_summaries(
            latest_data["categories"], trends,
            api_key, api_base_url, api_model,
            force=args.force,
            existing_trends=existing_trends,
            trend_path=trend_path,
            trend_date=latest_data["date"],
            prev_date=prev_date
        )
    else:
        print("\nAI 已关闭或未配置，使用规则摘要替代。")
        for key, trend in trends.items():
            display_name = trend.get("name") or key.split(":", 1)[-1]
            old = (
                existing_trends.get(key, {}).get("summary", "")
                or existing_trends.get(display_name, {}).get("summary", "")
            )
            if old and not is_rule_summary(old):
                trend["summary"] = old
            elif not trend.get("summary"):
                trend["summary"] = generate_trend_summary_text(display_name, trend)

    # 组装输出
    output = {
        "date": latest_data["date"],
        "prev_date": prev_date,
        "primary_channel": latest_data.get("primary_channel", "male"),
        "categories": [],
    }

    for cat in latest_data["categories"]:
        cat_name = cat["name"]
        key = ensure_cat_key(cat)
        channel = cat.get("channel", "male")
        # 保证 key/channel 写回
        if "channel" not in cat:
            cat = {**cat, "channel": channel, "key": key}
        cat_output = {
            "name": cat_name,
            "channel": channel,
            "key": key,
            "trend": trends.get(key) or trends.get(cat_name, {}),
            "books": cat.get("books", []),
        }
        output["categories"].append(cat_output)

    # 写入 latest_ranks.json
    out_path = os.path.join(data_dir, "latest_ranks.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已生成: {out_path}")

    # 生成静态 API 文件：api/lastest/all.json + api/lastest/<type>.json
    api_dir = build_lastest_api(output, base_dir)
    print(f"✅ Lastest API: {api_dir}")

    # 写入 trends/YYYY-MM-DD.json
    trend_output = {
        "date": latest_data["date"],
        "prev_date": prev_date,
        "trends": trends,
    }
    with open(trend_path, "w", encoding="utf-8") as f:
        json.dump(trend_output, f, ensure_ascii=False, indent=2)
    print(f"✅ 趋势存档: {trend_path}")

    # 生成全站热点总结：AI 优先，规则文案兜底
    market_payload = build_market_summary_payload(output, trends_dir)
    if api_base_url and api_key and api_model:
        market_payload = enrich_market_summary_with_ai(
            market_payload, api_key, api_base_url, api_model
        )
    market_path = os.path.join(data_dir, "market_summary.json")
    write_json(market_path, market_payload)
    print(f"✅ 全站热点总结: {market_path}")

    # 生成 dates.json 索引（供前端历史日期选择器使用）
    date_list = []
    for s in snapshots:
        fname = os.path.basename(s)
        # fanqie_ranks_YYYYMMDD.json / fanqie_female_new_ranks_YYYYMMDD.json
        m = re.search(r"(\d{4})(\d{2})(\d{2})", fname)
        if m:
            date_list.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    dates_path = os.path.join(data_dir, "dates.json")
    with open(dates_path, "w", encoding="utf-8") as f:
        json.dump({"dates": sorted(set(date_list))}, f, ensure_ascii=False, indent=2)
    print(f"✅ 日期索引: {dates_path} ({len(set(date_list))} 个日期)")


if __name__ == "__main__":
    main()
