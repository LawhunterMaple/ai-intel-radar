# Daily AI Intelligence Radar

每天自动抓取并整理以下信息源：
- Product Hunt
- Hacker News
- Indie Hackers
- Y Combinator Blog
- Reddit r/LocalLLaMA
- Reddit r/ArtificialIntelligence
- X AI Builder（通过可配置 RSS）

输出内容：
- 分类汇总
- 投资与行业发展点评
- 邮件发送到指定收件箱

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 配置

复制 `.env.example` 为 `.env`，填写：
- `SMTP_USER`：你的 QQ 邮箱
- `SMTP_PASS`：QQ SMTP 授权码（不是 QQ 登录密码）
- `MAIL_TO`：收件邮箱（默认 `56200498@qq.com`）
- `OPENAI_API_KEY`：可选，用于更高质量点评

## 运行

试跑（发送邮件）：
```bash
python main.py --run-once
```

试跑（不发邮件，只生成预览）：
```bash
python main.py --dry-run
```

定时守护（本地）：
```bash
python main.py --daemon
```

## GitHub Actions（推荐）

工作流文件：
- `.github/workflows/daily-ai-radar.yml`

默认每日北京时间 09:00（UTC 01:00）自动运行，也支持手动触发。

需要在仓库 Secrets 添加：
- `SMTP_HOST` = `smtp.qq.com`
- `SMTP_PORT` = `465`
- `SMTP_USER` = 你的 QQ 邮箱
- `SMTP_PASS` = QQ SMTP 授权码
- `MAIL_TO` = `56200498@qq.com`
- `OPENAI_API_KEY`（可选）
- `OPENAI_MODEL`（可选，默认 `gpt-4o-mini`）
- `LOOKBACK_HOURS`（可选，默认 `36`）
- `MAX_ITEMS_PER_SOURCE`（可选，默认 `20`）
- `X_FEEDS`（可选，多个 RSS 用英文逗号分隔）

## 注意

- Secrets 可留空，程序会自动回退默认值，不会因为空字符串崩溃。
- X 平台官方 API 限制较多，建议使用可信 RSS 源。
