import argparse
import datetime as dt
import html
import os
import re
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

import feedparser
import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


@dataclass
class Item:
    source: str
    title: str
    link: str
    summary: str
    published: Optional[dt.datetime]


def env_int(name: str, default: int) -> int:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def parse_entry_date(entry) -> Optional[dt.datetime]:
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return None
    return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)


def fetch_rss(source: str, url: str) -> List[Item]:
    parsed = feedparser.parse(url)
    items: List[Item] = []
    for e in parsed.entries:
        items.append(
            Item(
                source=source,
                title=(getattr(e, "title", "") or "").strip(),
                link=(getattr(e, "link", "") or "").strip(),
                summary=BeautifulSoup((getattr(e, "summary", "") or ""), "html.parser").get_text(" ", strip=True),
                published=parse_entry_date(e),
            )
        )
    return items


def clean_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


def fetch_article_snippet(url: str, max_chars: int = 260) -> str:
    if not url:
        return ""
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = clean_text(soup.get_text(" ", strip=True))
        if not text:
            return ""
        return text[:max_chars]
    except Exception:
        return ""


def enrich_items_with_snippet(items: List[Item]) -> List[Item]:
    enriched: List[Item] = []
    for i in items:
        snippet = fetch_article_snippet(i.link)
        summary = snippet if snippet else i.summary
        enriched.append(Item(i.source, i.title, i.link, summary, i.published))
    return enriched


def fetch_indie_hackers(max_items: int) -> List[Item]:
    url = "https://www.indiehackers.com/"
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items: List[Item] = []
        seen = set()
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            text = a.get_text(" ", strip=True)
            if not text or len(text) < 12:
                continue
            if not href.startswith("/post/"):
                continue
            full = f"https://www.indiehackers.com{href}"
            if full in seen:
                continue
            seen.add(full)
            items.append(Item("Indie Hackers", text, full, "", None))
            if len(items) >= max_items:
                break
        return items
    except Exception:
        return []


def collect_all_sources(max_items: int) -> Dict[str, List[Item]]:
    sources = {
        "Product Hunt": "https://www.producthunt.com/feed",
        "Hacker News": "https://news.ycombinator.com/rss",
        "Hacker News New": "https://hnrss.org/newest",
        "YC Blog": "https://www.ycombinator.com/blog/feed",
        "Reddit LocalLLaMA": "https://www.reddit.com/r/LocalLLaMA/.rss",
        "Reddit ArtificialIntelligence": "https://www.reddit.com/r/ArtificialIntelligence/.rss",
    }

    result: Dict[str, List[Item]] = {}
    for name, url in sources.items():
        base_items = fetch_rss(name, url)[:max_items]
        result[name] = enrich_items_with_snippet(base_items)

    result["Indie Hackers"] = enrich_items_with_snippet(fetch_indie_hackers(max_items))

    x_feeds = [x.strip() for x in (os.getenv("X_FEEDS", "") or "").split(",") if x.strip()]
    x_items: List[Item] = []
    for feed_url in x_feeds:
        x_items.extend(enrich_items_with_snippet(fetch_rss("X AI Builder", feed_url)[:max_items]))
    result["X AI Builder"] = x_items[:max_items]

    return result


def within_lookback(item: Item, lookback_hours: int) -> bool:
    if item.published is None:
        return True
    now = dt.datetime.now(dt.timezone.utc)
    return (now - item.published) <= dt.timedelta(hours=lookback_hours)


def categorize(item: Item) -> str:
    t = f"{item.title} {item.summary}".lower()
    if any(k in t for k in ["launch", "app", "tool", "product", "agent", "plugin"]):
        return "新产品与工具"
    if any(k in t for k in ["open source", "llama", "quant", "inference", "vllm", "gpu", "deployment"]):
        return "开源模型与部署"
    if any(k in t for k in ["revenue", "mrr", "growth", "seo", "acquisition", "pricing"]):
        return "增长与变现"
    if any(k in t for k in ["funding", "vc", "startup", "acquire", "market", "valuation"]):
        return "投资与创业动态"
    if any(k in t for k in ["paper", "research", "benchmark", "safety", "policy"]):
        return "研究与行业趋势"
    return "技术与创业讨论"


def investment_signal_score(item: Item) -> int:
    t = f"{item.title} {item.summary}".lower()
    score = 0
    high_intent = ["revenue", "mrr", "pricing", "paid", "enterprise", "contract", "customers"]
    market_signal = ["funding", "valuation", "acquire", "launch", "growth", "retention"]
    risk_words = ["shutdown", "layoff", "ban", "lawsuit", "security incident"]
    for k in high_intent:
        if k in t:
            score += 2
    for k in market_signal:
        if k in t:
            score += 1
    for k in risk_words:
        if k in t:
            score -= 1
    return max(0, min(score, 10))


def signal_level(score: int) -> str:
    if score >= 6:
        return "高"
    if score >= 3:
        return "中"
    return "低"


def trend_snapshot(items: List[Item]) -> Dict[str, int]:
    now = dt.datetime.now(dt.timezone.utc)
    last_24h = 0
    prev_6d = 0
    for i in items:
        if not i.published:
            continue
        delta = now - i.published
        if delta <= dt.timedelta(hours=24):
            last_24h += 1
        elif delta <= dt.timedelta(days=7):
            prev_6d += 1
    return {"last_24h": last_24h, "prev_6d": prev_6d}


def generate_rule_based_commentary(grouped: Dict[str, List[Item]]) -> str:
    total = sum(len(v) for v in grouped.values())
    hot = sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True)
    top_cats = ", ".join([f"{k}({len(v)})" for k, v in hot[:3]]) if hot else "无"
    lines = [
        "【专业点评（规则版）】",
        f"今日样本量 {total} 条，热度最高分类：{top_cats}。",
        "投资视角：若“新产品与工具”占比高于“增长与变现”，通常意味着供给创新快于商业验证，优先关注已形成稳定获客渠道与付费闭环的团队。",
        "行业视角：开源模型与部署讨论升温时，往往对应企业降本增效阶段，重点跟踪推理成本、私有化交付能力和垂直场景复用性。",
        "建议：将标的分为“3-6个月可验证收入”和“12个月平台势能”两类，分别跟踪收入增速、留存和生态合作信号。",
    ]
    return "\n".join(lines)


def generate_llm_commentary(grouped: Dict[str, List[Item]]) -> Optional[str]:
    api_key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
    if not api_key or OpenAI is None:
        return None

    model = (os.getenv("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini").strip()
    client = OpenAI(api_key=api_key)
    payload = []
    for cat, items in grouped.items():
        payload.append(
            {
                "category": cat,
                "items": [{"title": i.title, "source": i.source, "summary": i.summary, "link": i.link} for i in items[:10]],
            }
        )

    prompt = (
        "你是一名AI产业研究总监。请基于给定情报输出中文专业点评，必须包含："
        "1) 资本/投资启示；2) 行业阶段判断；3) 未来30-90天观察指标；4) 主要风险。"
        "要求结构化、克制、避免空话，800字以内。"
    )
    try:
        r = client.responses.create(
            model=model,
            input=[{"role": "system", "content": prompt}, {"role": "user", "content": str(payload)}],
            temperature=0.2,
        )
        text = (getattr(r, "output_text", "") or "").strip()
        return text or None
    except Exception:
        return None


def build_html_report(all_items: Dict[str, List[Item]], lookback_hours: int) -> str:
    filtered = [i for items in all_items.values() for i in items if within_lookback(i, lookback_hours)]
    grouped: Dict[str, List[Item]] = {}
    for i in filtered:
        grouped.setdefault(categorize(i), []).append(i)

    commentary = generate_llm_commentary(grouped) or generate_rule_based_commentary(grouped)
    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f"<h2>AI 情报日报 - {now_str}</h2>",
        f"<p>统计窗口：最近 {lookback_hours} 小时</p>",
        "<h3>一、专业点评</h3>",
        f"<pre style='white-space:pre-wrap;font-family:Segoe UI,Arial,sans-serif'>{html.escape(commentary)}</pre>",
        "<h3>二、分类情报</h3>",
    ]

    parts.append("<h3>二点五、赛道热度趋势（近24h 对比 前6天）</h3><ul>")
    for cat, items in grouped.items():
        snap = trend_snapshot(items)
        diff = snap["last_24h"] - snap["prev_6d"]
        trend = "升温" if diff > 0 else ("降温" if diff < 0 else "持平")
        parts.append(
            f"<li>{html.escape(cat)}: 近24h={snap['last_24h']}，前6天={snap['prev_6d']}，趋势={trend}</li>"
        )
    parts.append("</ul>")

    cat_order = [
        "新产品与工具",
        "投资与创业动态",
        "增长与变现",
        "开源模型与部署",
        "研究与行业趋势",
        "技术与创业讨论",
    ]

    category_limit = 5
    for cat in cat_order:
        items = grouped.get(cat, [])
        if not items:
            continue
        ranked = sorted(items, key=investment_signal_score, reverse=True)[:category_limit]
        parts.append(f"<h4>{cat}（显示前{len(ranked)}条 / 共{len(items)}条）</h4><ul>")
        for i in ranked:
            src = html.escape(i.source)
            title = html.escape(i.title or "(无标题)")
            link = html.escape(i.link or "#")
            summary = html.escape((i.summary or "")[:260])
            score = investment_signal_score(i)
            level = signal_level(score)
            parts.append(
                f"<li><a href='{link}'>{title}</a> <em>[{src}]</em> "
                f"<strong>投资信号: {level}({score}/10)</strong><br/>{summary}</li>"
            )
        parts.append("</ul>")

    parts.append("<h3>三、源站抓取状态</h3><ul>")
    for source, items in all_items.items():
        parts.append(f"<li>{html.escape(source)}: {len(items)} 条</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def send_email(subject: str, html_body: str) -> None:
    host = (os.getenv("SMTP_HOST", "smtp.qq.com") or "smtp.qq.com").strip()
    port = env_int("SMTP_PORT", 465)
    user = (os.getenv("SMTP_USER", "") or "").strip()
    pwd = (os.getenv("SMTP_PASS", "") or "").strip()
    to_addr = (os.getenv("MAIL_TO", "56200498@qq.com") or "56200498@qq.com").strip()

    if not user or not pwd:
        raise RuntimeError("缺少 SMTP_USER / SMTP_PASS，请先配置环境变量或 GitHub Secrets。")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, pwd)
        server.sendmail(user, [to_addr], msg.as_string())


def run_pipeline(dry_run: bool = False) -> None:
    max_items = env_int("MAX_ITEMS_PER_SOURCE", 20)
    lookback_hours = env_int("LOOKBACK_HOURS", 36)
    all_items = collect_all_sources(max_items=max_items)
    html_body = build_html_report(all_items, lookback_hours=lookback_hours)

    if dry_run:
        preview_path = "report_preview.html"
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"[OK] Dry run finished. Preview saved to {preview_path}")
        return

    date_tag = dt.datetime.now().strftime("%Y-%m-%d")
    subject = f"AI 情报日报 {date_tag}"
    send_email(subject, html_body)
    print(f"[OK] Email sent: {subject}")


def run_daemon() -> None:
    hour = env_int("SCHEDULE_HOUR", 9)
    minute = env_int("SCHEDULE_MINUTE", 0)
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(run_pipeline, "cron", hour=hour, minute=minute, id="daily_ai_radar")
    print(f"[SCHEDULE] Daily at {hour:02d}:{minute:02d} Asia/Shanghai")
    scheduler.start()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Daily AI Intelligence Radar")
    parser.add_argument("--run-once", action="store_true", help="Run once immediately")
    parser.add_argument("--daemon", action="store_true", help="Run scheduler daemon")
    parser.add_argument("--dry-run", action="store_true", help="Build report only, do not send email")
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
        return
    run_pipeline(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
