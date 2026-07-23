"""
番茄新书榜爬虫 · 男频为主，女频为辅

URL 规律（番茄 rank 页）：
  /rank/{channel}_{board}_{categoryId}
  channel: 0=女频, 1=男频
  board:   1=新书榜, 2=其他榜
"""
import os
import json
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

START_CODE = 58344  # 0xE3E8
CHAR_SEQUENCE = [
    "D", "在", "主", "特", "家", "军", "然", "表", "场", "4", "要", "只", "v", "和", "?", "6", "别", "还", "g", "现", "儿", "岁", "?", "?", "此", "象", "月", "3", "出", "战", "工", "相", "o", "男", "直", "失", "世", "F", "都", "平", "文", "什", "V", "O", "将", "真", "T", "那", "当", "?", "会", "立", "些", "u", "是", "十", "张", "学", "气", "大", "爱", "两", "命", "全", "后", "东", "性", "通", "被", "1", "它", "乐", "接", "而", "感", "车", "山", "公", "了", "常", "以", "何", "可", "话", "先", "p", "i", "叫", "轻", "M", "士", "w", "着", "变", "尔", "快", "l", "个", "说", "少", "色", "里", "安", "花", "远", "7", "难", "师", "放", "t", "报", "认", "面", "道", "S", "?", "克", "地", "度", "I", "好", "机", "U", "民", "写", "把", "万", "同", "水", "新", "没", "书", "电", "吃", "像", "斯", "5", "为", "y", "白", "几", "日", "教", "看", "但", "第", "加", "候", "作", "上", "拉", "住", "有", "法", "r", "事", "应", "位", "利", "你", "声", "身", "国", "问", "马", "女", "他", "Y", "比", "父", "x", "A", "H", "N", "s", "X", "边", "美", "对", "所", "金", "活", "回", "意", "到", "z", "从", "j", "知", "又", "内", "因", "点", "Q", "三", "定", "8", "R", "b", "正", "或", "夫", "向", "德", "听", "更", "?", "得", "告", "并", "本", "q", "过", "记", "L", "让", "打", "f", "人", "就", "者", "去", "原", "满", "体", "做", "经", "K", "走", "如", "孩", "c", "G", "给", "使", "物", "?", "最", "笑", "部", "?", "员", "等", "受", "k", "行", "一", "条", "果", "动", "光", "门", "头", "见", "往", "自", "解", "成", "处", "天", "能", "于", "名", "其", "发", "总", "母", "的", "死", "手", "入", "路", "进", "心", "来", "h", "时", "力", "多", "开", "已", "许", "d", "至", "由", "很", "界", "n", "小", "与", "Z", "想", "代", "么", "分", "生", "口", "再", "妈", "望", "次", "西", "风", "种", "带", "J", "?", "实", "情", "才", "这", "?", "E", "我", "神", "格", "长", "觉", "间", "年", "眼", "无", "不", "亲", "关", "结", "0", "友", "信", "下", "却", "重", "己", "老", "2", "音", "字", "m", "呢", "明", "之", "前", "高", "P", "B", "目", "太", "e", "9", "起", "稜", "她", "也", "W", "用", "方", "子", "英", "每", "理", "便", "四", "数", "期", "中", "C", "外", "样", "a", "海", "们", "任"
]

# 频道配置：男频全量，女频辅助（较少本数）
CHANNELS = [
    {
        "id": "male",
        "label": "男频",
        "init_url": "https://fanqienovel.com/rank/1_1_1014",
        "href_prefix": "/rank/1_1_",
        "limit": 30,
        "primary": True,
    },
    {
        "id": "female",
        "label": "女频",
        "init_url": "https://fanqienovel.com/rank/0_1_1139",
        "href_prefix": "/rank/0_1_",
        "limit": 15,  # 辅助：每类少抓一些
        "primary": False,
    },
]

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAPSHOT_PREFIX = "fanqie_ranks_"


def decode_text(text: str) -> str:
    if not text:
        return ""
    result = []
    for char in text:
        code = ord(char)
        idx = code - START_CODE
        if 0 <= idx < len(CHAR_SEQUENCE):
            result.append(CHAR_SEQUENCE[idx])
        else:
            result.append(char)
    return "".join(result)


def cat_key(channel: str, name: str) -> str:
    """跨频道唯一分类键（男女频存在同名分类）。"""
    return f"{channel}:{name}"


EXTRACT_JS = """
() => {
    const bookMap = new Map();
    const links = document.querySelectorAll('a[href^="/page/"]');
    links.forEach(link => {
        let container = link.parentElement;
        let depth = 0;
        while (container && depth < 6) {
            if (container.querySelector('img') && container.innerText.includes('在读')) {
                const href = link.getAttribute('href');
                if (!bookMap.has(href)) {
                    bookMap.set(href, container);
                }
                break;
            }
            container = container.parentElement;
            depth++;
        }
    });

    const cards = Array.from(bookMap.values());
    const results = [];
    for (const item of cards) {
        let imgNode = item.querySelector('img');
        let cover = imgNode ? imgNode.getAttribute('src') : "";

        let title = "";
        if (imgNode && imgNode.getAttribute('alt')) {
            title = imgNode.getAttribute('alt').trim();
        }
        if (!title) {
            let textTitleNode = item.querySelector('h4, .title, h1') || item.querySelector('a[href^="/page/"]');
            if (textTitleNode) {
                let text = textTitleNode.innerText.trim();
                if (text && !/^\\d+$/.test(text)) {
                    title = text;
                }
            }
        }
        if (!title) title = "未知";
        if (title.includes("榜单说明")) continue;

        let authorNode = item.querySelector('.author, .author-name') || item.querySelector('a[href^="/author-page/"]');
        let author = authorNode ? authorNode.innerText.trim() : "未知";

        let reads = "未知";
        const lines = item.innerText.split('\\n');
        for (let line of lines) {
            if (line.includes('在读')) {
                reads = line;
                break;
            }
        }

        let introNode = item.querySelector('.intro, .abstract, .desc');
        let intro = introNode ? introNode.innerText.trim() : "暂无简介";

        results.push({
            title: title,
            author: author,
            reads: reads,
            intro: intro,
            cover: cover,
            url: item.querySelector('a[href^="/page/"]').getAttribute('href')
        });
    }
    return results;
}
"""


def _normalize_book(raw: dict) -> dict:
    t = decode_text(raw.get("title", ""))
    a = decode_text(raw.get("author", ""))
    r_raw = decode_text(raw.get("reads", ""))
    i = decode_text(raw.get("intro", "")).replace("\\n", " ")
    c = raw.get("cover", "")

    if "在读" in r_raw:
        parts = r_raw.split("在读")
        cleaned_r = (
            parts[1].replace(":", "").replace("：", "").strip()
            if len(parts) > 1
            else r_raw
        )
    else:
        cleaned_r = r_raw

    return {
        "title": t,
        "author": a,
        "reads": cleaned_r,
        "intro": i,
        "cover": c,
        "url": "https://fanqienovel.com" + raw.get("url", ""),
    }


def _scrape_channel(page, channel_cfg: dict, completed: set, all_categories: list,
                    output_file: str, state_file: str, sleep_sec: float) -> list:
    """抓取单个频道的所有新书榜分类，返回该频道新增的 categories。"""
    channel_id = channel_cfg["id"]
    label = channel_cfg["label"]
    init_url = channel_cfg["init_url"]
    href_prefix = channel_cfg["href_prefix"]
    limit = channel_cfg["limit"]

    print(f"\n{'='*50}")
    print(f"📡 开始抓取【{label}】频道（每类 Top {limit}）")
    print(f"{'='*50}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 入口：{init_url}")

    page.goto(init_url, wait_until="load", timeout=20000)
    page.wait_for_selector('a[href^="/page/"]', timeout=8000)

    categories_js = f"""
    () => {{
        return Array.from(document.querySelectorAll('a'))
            .filter(a => a.href.includes('{href_prefix}'))
            .map(a => ({{
                name: a.innerText.trim(),
                href: a.getAttribute('href')
            }}))
            .filter(c => c.name && c.href);
    }}
    """
    categories = page.evaluate(categories_js)
    # 去重（侧边栏可能重复）
    seen = set()
    unique_cats = []
    for cat in categories:
        if cat["name"] not in seen:
            seen.add(cat["name"])
            unique_cats.append(cat)
    categories = unique_cats

    print(f"✅ 【{label}】提取到 {len(categories)} 个分类")

    channel_results = []
    for cat in categories:
        cat_name = cat["name"]
        cat_href = cat["href"]
        key = cat_key(channel_id, cat_name)

        if key in completed:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏭️ 跳过已完成：{label}/{cat_name}")
            continue

        print(f"[{datetime.now().strftime('%H:%M:%S')}] → {label}/{cat_name}")
        try:
            page.locator(f"a[href='{cat_href}']").click()
            time.sleep(2)
            page.wait_for_selector('a[href^="/page/"]', timeout=5000)
        except Exception as e:
            print(f"  切换分类出错 {cat_name}: {e}")

        for _ in range(3):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            time.sleep(1.2)

        try:
            books_data = page.evaluate(EXTRACT_JS)
        except Exception as e:
            print(f"  JS 抽取失败 {cat_name}: {e}")
            books_data = []

        category_books = [_normalize_book(b) for b in books_data[:limit]]
        entry = {
            "name": cat_name,
            "channel": channel_id,
            "key": key,
            "books": category_books,
        }
        channel_results.append(entry)
        all_categories.append(entry)

        # 增量写盘
        snapshot = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "primary_channel": "male",
            "categories": all_categories,
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

        completed.add(key)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"completed": sorted(completed)}, f, ensure_ascii=False)

        print(f"  ✅ {cat_name}: {len(category_books)} 本（进度已存档）")
        time.sleep(sleep_sec)

    return channel_results


def run_scraper(sleep_sec=5):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    output_file = os.path.join(OUTPUT_DIR, f"{SNAPSHOT_PREFIX}{date_str}.json")
    state_file = os.path.join(OUTPUT_DIR, f"task_state_{date_str}.json")

    completed = set()
    all_categories = []

    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                completed = set(json.load(f).get("completed", []))
        except Exception:
            pass

    if os.path.exists(output_file) and completed:
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
                all_categories = existing.get("categories", [])
        except Exception:
            pass

    with sync_playwright() as p:
        if os.environ.get("GITHUB_ACTIONS"):
            browser = p.chromium.launch(headless=True)
        else:
            try:
                browser = p.chromium.launch(headless=True, channel="chrome")
            except Exception:
                browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        # 男频优先，女频辅助
        for channel_cfg in CHANNELS:
            _scrape_channel(
                page, channel_cfg, completed, all_categories,
                output_file, state_file, sleep_sec
            )

        browser.close()

    # 最终写一次，保证字段齐全
    snapshot = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "primary_channel": "male",
        "categories": all_categories,
        "meta": {
            "male_categories": sum(1 for c in all_categories if c.get("channel") == "male"),
            "female_categories": sum(1 for c in all_categories if c.get("channel") == "female"),
            "male_books": sum(
                len(c.get("books", [])) for c in all_categories if c.get("channel") == "male"
            ),
            "female_books": sum(
                len(c.get("books", [])) for c in all_categories if c.get("channel") == "female"
            ),
        },
    }
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # 兼容旧版 Actions 跳过检查（仍查找 fanqie_female_new_ranks_*）
    # 写入同内容别名，避免同日重复全量爬取；前端/构建优先读 fanqie_ranks_*
    legacy_file = os.path.join(OUTPUT_DIR, f"fanqie_female_new_ranks_{date_str}.json")
    if os.path.abspath(legacy_file) != os.path.abspath(output_file):
        with open(legacy_file, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 抓取完成：{output_file}")
    print(
        f"   男频 {snapshot['meta']['male_categories']} 类 / "
        f"{snapshot['meta']['male_books']} 本；"
        f"女频 {snapshot['meta']['female_categories']} 类 / "
        f"{snapshot['meta']['female_books']} 本"
    )


if __name__ == "__main__":
    print("开始执行番茄新书榜抓取（男频为主 · 女频辅助）...")
    run_scraper(sleep_sec=5)
