# Daily AI Intelligence Radar

自动采集并分析以下信息源：
- Product Hunt
- Hacker News
- Indie Hackers
- Y Combinator Blog
- Reddit r/LocalLLaMA
- Reddit r/ArtificialIntelligence
- X (via configurable RSS/Nitter sources)

每天定时执行：
1. 抓取最新内容
2. 自动分类（新产品/技术创业讨论/增长与变现/行业与研究/开源模型与部署/实时热点）
3. 生成专业级点评（重点投资与行业发展）
4. 发送邮件到目标邮箱

## 1) 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2) 配置环境变量

复制 `.env.example` 到 `.env` 并填写：

- `SMTP_USER`：你的 QQ 邮箱（例如 `xxx@qq.com`）
- `SMTP_PASS`：QQ 邮箱 SMTP 授权码（不是登录密码）
- `MAIL_TO`：收件邮箱（默认已填 `56200498@qq.com`）
- `OPENAI_API_KEY`：可选，用于更高质量点评（不填则使用内置专业模板点评）

## 3) 先试跑一次

```bash
python main.py --run-once
```

运行成功后，你会在邮箱收到日报。

## 4) 每天定时运行

### 方式 A：程序内置定时（简单）

```bash
python main.py --daemon
```

默认每天 `09:00`（可通过 `.env` 的 `SCHEDULE_HOUR` / `SCHEDULE_MINUTE` 修改）。

### 方式 B：Windows 任务计划（推荐长期稳定）

创建任务每日运行：

```bash
python main.py --run-once
```

### 方式 C：GitHub Actions（电脑可关机，推荐）

仓库里已包含工作流文件：

- `.github/workflows/daily-ai-radar.yml`

它会在每天北京时间 09:00（UTC 01:00）自动运行，也支持手动触发。

你需要在 GitHub 仓库中设置 `Settings -> Secrets and variables -> Actions -> New repository secret`：

- `SMTP_HOST`：`smtp.qq.com`
- `SMTP_PORT`：`465`
- `SMTP_USER`：你的 QQ 邮箱
- `SMTP_PASS`：QQ 邮箱 SMTP 授权码
- `MAIL_TO`：`56200498@qq.com`
- `OPENAI_API_KEY`：可选
- `OPENAI_MODEL`：可选，示例 `gpt-4o-mini`
- `LOOKBACK_HOURS`：可选，示例 `36`
- `MAX_ITEMS_PER_SOURCE`：可选，示例 `20`
- `X_FEEDS`：可选，多个 RSS 用英文逗号分隔

然后把代码 push 到 GitHub，进入 `Actions -> Daily AI Radar`，点 `Run workflow` 先手动跑一次验证。

## 5) 注意事项

- X 平台官方 API 限制较多，示例中采用可配置 RSS 源（`X_FEEDS`）。
- 部分站点结构可能变化，程序已做降级容错（抓不到会在报告里标记）。
- 若使用 OpenAI 点评，请遵守你自己的 API 成本预算。
