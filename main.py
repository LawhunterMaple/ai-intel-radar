import argparse
import datetime as dt
import html
import os
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


def parse_isoish_date(entry) -> Optional[dt.datetime]:
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return None
    return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)


def fetch_rss(source: str, url: str, timeout: int = 15) -> List[Item]:
    parsed = feedparser.parse(url)
    items: List[Item] = []
    for e in parsed.entries:
        items.append(
            Item(
                source=source,
                title=(getattr(e, "title", "") or "").strip(),
                link=(getattr(e, "link", "") or "").strip(),
                summary=BeautifulSoup((getattr(e, "summary", "") or ""), "html.parser").get_text(" ", strip=True),
                published=parse_isoish_date(e),
            )
        )
    return items


def fetch_indie_hackers(max_items: int) -> List[Item]:
    url = "https://www.indiehackers.com/"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items: List[Item] = []
        seen = set()
        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
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
        items = fetch_rss(name, url)[:max_items]
        result[name] = items

    result["Indie Hackers"] = fetch_indie_hackers(max_items)

    x_feeds = [x.strip() for x in os.getenv("X_FEEDS", "").split(",") if x.strip()]
    x_items: List[Item] = []
    for f in x_feeds:
        x_items.extend(fetch_rss("X AI Builder", f)[:max_items])
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


def generate_rule_based_commentary(grouped: Dict[str, List[Item]]) -> str:
    total = sum(len(v) for v in grouped.values())
    hot = sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True)
    top_cats = ", ".join([f"{k}({len(v)})" for k, v in hot[:3]]) if hot else "无"
    lines = [
        "【专业点评（规则版）】",
        f"今日样本量 {total} 条，讨论热区集中在：{top_cats}。",
        "投资视角：若“新产品与工具”密集但“增长与变现”跟进不足，通常意味着供给侧创新快于需求验证，建议关注有明确分发渠道与付费闭环的团队。",
        "行业视角：开源模型与部署讨论上升，往往对应企业侧降本诉求增强，重点跟踪推理效率、私有化合规和垂直场景落地。",
        "策略建议：将项目分为“短期可变现（3-6个月）”和“中期平台型（12个月+）”两篮子，分别用收入增速与生态势能做跟踪。",
    ]
    return "\n".join(lines)


def generate_llm_commentary(grouped: Dict[str, List[Item]]) -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or OpenAI is None:
        return None
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)

    payload = []
    for cat, items in grouped.items():
        payload.append({"category": cat, "items": [{"title": i.title, "source": i.source, "summary": i.summary, "link": i.link} for i in items[:10]]})

    prompt = (
        "你是一名 AI 产业研究总监。请基于给定情报，生成中文专业点评，重点包含：\n"
        "1) 资本/投资启示；2) 行业阶段判断；3) 未来30-90天观察指标；4) 潜在风险。\n"
        "要求：结构化、克制、避免空话，800字以内。"
    )
    try:
        r = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": str(payload)},
            ],
            temperature=0.2,
        )
        text = getattr(r, "output_text", "").strip()
        return text or None
    except Exception:
        return None


def build_html_report(all_items: Dict[str, List[Item]], lookback_hours: int) -> str:
    filtered: List[Item] = []
    for _, items in all_items.items():
        for i in items:
            if within_lookback(i, lookback_hours):
                filtered.append(i)

    grouped: Dict[str, List[Item]] = {}
    for i in filtered:
        grouped.setdefault(categorize(i), []).append(i)

    llm_commentary = generate_llm_commentary(grouped)
    commentary = llm_commentary or generate_rule_based_commentary(grouped)

    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f"<h2>AI 情报日报 - {now_str}</h2>",
        f"<p>统计窗口：最近 {lookback_hours} 小时</p>",
        "<h3>一、专业点评</h3>",
        f"<pre style='white-space:pre-wrap;font-family:Segoe UI,Arial,sans-serif'>{html.escape(commentary)}</pre>",
        "<h3>二、分类情报</h3>",
    ]

    cat_order = [
        "新产品与工具",
        "投资与创业动态",
        "增长与变现",
        "开源模型与部署",
        "研究与行业趋势",
        "技术与创业讨论",
    ]
    for cat in cat_order:
        items = grouped.get(cat, [])
        if not items:
            continue
        parts.append(f"<h4>{cat}（{len(items)}）</h4><ul>")
        for i in items[:30]:
            src = html.escape(i.source)
            title = html.escape(i.title or "(无标题)")
            link = html.escape(i.link or "#")
            summary = html.escape((i.summary or "")[:180])
            parts.append(f"<li><a href='{link}'>{title}</a> <em>[{src}]</em><br/>{summary}</li>")
        parts.append("</ul>")

    parts.append("<h3>三、源站抓取状态</h3><ul>")
    for source, items in all_items.items():
        parts.append(f"<li>{html.escape(source)}: {len(items)} 条</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def send_email(subject: str, html_body: str):
    host = os.getenv("SMTP_HOST", "smtp.qq.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER", "").strip()
    pwd = os.getenv("SMTP_PASS", "").strip()
    to_addr = os.getenv("MAIL_TO", "56200498@qq.com").strip()

    if not user or not pwd:
        raise RuntimeError("缺少 SMTP_USER / SMTP_PASS，请先配置 .env")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, pwd)
        server.sendmail(user, [to_addr], msg.as_string())


def run_pipeline():
    max_items = int(os.getenv("MAX_ITEMS_PER_SOURCE", "20"))
    lookback_hours = int(os.getenv("LOOKBACK_HOURS", "36"))
    all_items = collect_all_sources(max_items=max_items)
    html_body = build_html_report(all_items, lookback_hours=lookback_hours)
    date_tag = dt.datetime.now().strftime("%Y-%m-%d")
    subject = f"AI 情报日报 {date_tag}"
    send_email(subject, html_body)
    print(f"[OK] Email sent: {subject}")


def run_daemon():
    hour = int(os.getenv("SCHEDULE_HOUR", "9"))
    minute = int(os.getenv("SCHEDULE_MINUTE", "0"))
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(run_pipeline, "cron", hour=hour, minute=minute, id="daily_ai_radar")
    print(f"[SCHEDULE] Daily at {hour:02d}:{minute:02d} Asia/Shanghai")
    scheduler.start()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Daily AI Intelligence Radar")
    parser.add_argument("--run-once", action="store_true", help="Run once immediately")
    parser.add_argument("--daemon", action="store_true", help="Run scheduler daemon")
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
        return
    run_pipeline()


if __name__ == "__main__":
    main()
