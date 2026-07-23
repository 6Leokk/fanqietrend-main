# 🍅 番茄风向标 · Fanqie Trend

> **男频为主，女频辅助** — 每日自动追踪番茄小说新书榜，结合 AI 生成趋势分析，部署为在线看板。

本仓库基于社区项目改造，**已独立运营、不再同步上游**。数据格式、爬取频道与看板结构均为本 fork 定制版。

---

## ✨ 功能概览

| 功能 | 说明 |
|------|------|
| 🕷️ 双频道爬取 | **男频**全量 Top 30；**女频**辅助 Top 15 |
| 📊 趋势对比 | 新上榜 / 掉榜 / 排名变化 / 阅读量增长 |
| 🤖 AI 风向分析 | 默认 OpenCode 免费 `deepseek-v4-flash-free`（key=`public`），限流自动换免费备用模；失败则规则兜底 |
| 🧭 类型风向标 | 多日聚合，男频赛道优先 |
| 🖥️ 看板 | 侧边栏可切换男频 / 女频 |
| ⚡ 自动化 | GitHub Actions + GitHub Pages |

看板地址：https://6leokk.github.io/fanqietrend-main/

---

## 🚀 使用说明

### 1. Fork / 使用本仓库

本仓库已配置好 Actions 与 Pages。若重新部署到自己的账号：

1. Fork 本仓库
2. 开启 **Settings → Pages → Source: GitHub Actions**
3. **无需配置 Secrets**：默认使用 OpenCode 免费接口
   - `API_BASE_URL=https://opencode.ai/zen/v1`
   - `API_KEY=public`
   - `API_MODEL=deepseek-v4-flash-free`
4. **Actions → Daily Fanqie Rank Scraper → Run workflow**

### 2. 自动更新

每天 **UTC 00:17（北京时间 08:17）** 自动爬取并部署。

### 3. 数据格式

每日快照：`data/fanqie_ranks_YYYYMMDD.json`

```json
{
  "date": "2026-07-23",
  "primary_channel": "male",
  "categories": [
    {
      "name": "都市日常",
      "channel": "male",
      "key": "male:都市日常",
      "books": [ ... ]
    },
    {
      "name": "古风世情",
      "channel": "female",
      "key": "female:古风世情",
      "books": [ ... ]
    }
  ]
}
```

- 男频入口：`https://fanqienovel.com/rank/1_1_*`
- 女频入口：`https://fanqienovel.com/rank/0_1_*`

---

## 🔌 数据接口

| 路径 | 说明 |
|------|------|
| `api/lastest.json` | 类型索引（含 channel） |
| `api/lastest/all.json` | 全量 |
| `api/lastest/<channel>_<类型>.json` | 单类型 |

---

## 🔧 本地开发

```bash
git clone https://github.com/6Leokk/fanqietrend-main.git
cd fanqietrend-main
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

python scrape_fanqie_ranks.py          # 爬取（男频优先）
python scripts/build_latest.py         # 构建看板数据
python -m http.server 8000             # 预览
```

---

## 📁 项目结构

```
fanqietrend-main/
├── .github/workflows/
│   ├── scrape.yml              # 每日爬取 + 部署
│   ├── force_update.yml        # 强制重跑
│   └── pages.yml               # Pages 部署
├── css/  js/                   # 前端
├── scripts/build_latest.py     # 趋势 + AI + API
├── data/
│   ├── fanqie_ranks_YYYYMMDD.json
│   ├── latest_ranks.json
│   ├── market_summary.json
│   └── trends/
├── api/lastest/                # 静态 JSON 接口
├── scrape_fanqie_ranks.py
├── index.html / trend.html / book.html
└── README.md
```

---

## 📝 说明

- **默认启用 AI**（OpenCode 公开免费 key=`public`），不把密钥放进 GitHub Secrets。
- 免费接口有全局限流；主模型不可用时自动回退 `laguna-s-2.1-free` 等免费模型。
- 默认只对**男频**分类做 AI 总结（`AI_CHANNELS=male`），女频用规则摘要省额度。
- 若要关闭 AI：环境变量 `API_KEY=off`。
- 男女频存在同名分类（如「科幻末世」），内部用 `channel:name` 作唯一键。

---

## License / 致谢

改造自 [zhangsalute1/fanqietrend-main](https://github.com/zhangsalute1/fanqietrend-main) 社区项目。本仓库独立维护，功能与数据方向已分叉。
