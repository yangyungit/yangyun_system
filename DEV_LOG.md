

# Moltbot 开发日志 (DEV_LOG)
> **文档说明**：本日志用于记录系统架构变更、关键技术决策及当前开发进度。任何 AI 助手在协助开发前，必须优先阅读本文件以对齐上下文。

🛠️ System Dev Log: The "Hardcore" UpdateDate: 2026-02-10 | Status: Operational ✅ | Version: v1.2-Beta
🎯 核心里程碑 (Core Milestone)全链路（Pipeline）正式打通：数据流 Telegram / Manual $\rightarrow$ AI Cleaning $\rightarrow$ Google Sheets $\rightarrow$ GitHub Storage $\rightarrow$ Streamlit UI 现已无缝连接。系统已具备双重归档（00原文/01分析）能力，且彻底解决了云端写入权限与链接跳转问题。
🚀 功能迭代 (Features Update)
1. 前端交互与视觉 (UI/UX)极简主义重构 (Minimalist Overhaul)：移除所有表格表头的 Emoji 与冗余说明，回归硬核数据展示。链接样式去“土味蓝”，改为朴素灰虚线，悬停高亮，符合专业情报终端审美。侧边栏汉化 (Localization)：文件结构重命名为 01_宏观监控, 02_情报雷达, 03_深度侦查 等，移除了文件名中的 Emoji，保持侧边栏清爽。智能标题展示策略 (Smart Display)：实施 “三级替补策略”：AI 结论 (20字) $\rightarrow$ 摘要 $\rightarrow$ 逻辑链 $\rightarrow$ "暂无结论"。彻底消灭 "无标题" 和 "Untitled"。双重跳转架构：点击 标题 $\rightarrow$ 跳转 00_Inbox (查看原文)。点击 逻辑链 $\rightarrow$ 跳转 01/02_Research (查看深度分析卡片)。
2. 后端逻辑与 AI 核心 (Backend & AI)四维情报切分 (4-Dim Slicing)：utils.py 升级 Prompt，强制 AI 将每份情报切割为 [事实]、[观点]、[逻辑]、[假设] 四个物理模块，为侦查室提供结构化弹药。20字极简结论 (Executive Summary)：强制 AI 输出 20 字以内的“核心结论”作为标题，而非使用原本的长标题或摘要。来源标记增强：支持识别非 HTTP 来源，前端准确显示 Telegram, Manual (手动录入), Web Console 等来源标签。
3. 基础设施 (Infrastructure - GitHub)PyGithub 集成：引入 PyGithub 库，替代简单的 HTTP 请求，实现更稳定的文件创建与更新。实现 自动文件夹创建：推送时若目标文件夹不存在（如 00_Inbox_AI），系统会自动建立，无需人工干预。鉴权修复：通过 fly secrets set GITHUB_TOKEN 解决了云端服务器无法写入 GitHub 的权限问题。
🐛 漏洞修复 (Bug Fixes)
逻辑链乱码修复 (Logic Chain Clean-up)：
症状：网页显示的逻辑链带有双重中括号 [[A->B]]，导致 Markdown 链接失效并显示乱码。修复：前端渲染前增加 .replace('[', '').replace(']', '') 清洗步骤。
空格链接断裂修复 (Space in URL)：症状：文件名如 "S&P 500" 包含空格，导致跳转链接在 "S&P" 处截断。修复：源头：生成文件名时强制将空格替换为下划线 _。补救：前端读取旧数据时增加 .replace(' ', '%20') 兼容老文件。手动录入数据结构补全：修复：雷达页面的手动录入功能现在会正确填充 title, summary, url="Manual" 等所有字段，防止入库报错。
🔮 Next Step (下一步建议)深度侦查室实战：现在数据结构已经包含了完美的“事实/假设”切分，下一步可以重点打磨 03_深度侦查 的 Prompt，让 AI 真正学会“攻击假设”和“寻找逻辑漏洞”。认知法庭 (Court)：利用已有的多空偏向数据，搭建“红蓝军对抗”辩论场。

End of Log.

## 📅 最新更新: 2026-02-09 (系统重构与稳定化)

### 🏗 当前系统状态 (Current Status)
1.  **核心中枢 (`utils.py`)**:
    * 已集成了 Google Sheets 的读写逻辑 (`load_data`, `save_data`)。
    * 已集成 Gemini AI 分析模块 (`auto_dispatch`)。
    * **状态**: 稳定。**严禁**修改其函数签名，它是连接中台与机器人的心脏。
2.  **密钥管理 (Secrets)**:
    * 放弃了直接依赖 `st.secrets` 的方式（因只读限制）。
    * **现行方案**: 使用环境变量 `STREAMLIT_SECRETS_B64` 存储 Base64 编码的 TOML 内容，程序启动时自动解码并写入 `.streamlit/secrets.toml` 物理文件。
3.  **抓取模块 (`scrapers/`)**:
    * **Patreon 自动抓取**: 暂时挂起。原因：Fly.io 数据中心 IP 无法通过 Cloudflare 403 验证 (即使有 Cookie)。
    * **手动投喂模式**: 已上线。通过 Telegram 直接发送长文本，触发 `run_text_task` 进行分析入库。
4.  **机器人 (`telegram_bot.py`)**:
    * 已修复 `telebot` 依赖缺失问题。
    * 目前支持识别 URL（尝试抓取）和 长文本（直接分析）两种模式。

### 📓 关键技术决策 (Decision Log)
* **[架构] 接口层分离**: 引入 `scrapers/base_scraper.py` 作为所有抓取脚本的统一出口。任何新开发的抓取器（Scraper）必须通过此接口提交数据，禁止直接操作数据库。
* **[部署] 依赖锁定**: `requirements.txt` 已固化，明确包含 `plotly`, `cloudscraper`, `pyTelegramBotAPI` 等关键库。
* **[运维] 哨兵兼容**: 在 `fetch_patreon.py` 中保留了 `get_cookie_path` 空函数，防止 `monitor.py` 因导入错误导致服务器无限重启。

### ⚠️ 已知问题与坑 (Known Issues)
1.  **反爬虫**: Cloudflare 对数据中心 IP (Fly.io) 极其敏感。
    * *规避策略*: 优先开发本地运行的抓取脚本，或者使用“手动投喂”作为兜底。
2.  **密钥格式**: Mac 的 `base64` 命令会自动添加换行符，导致环境变量截断。
    * *解决方案*: 必须使用 `base64 -i ... | tr -d '\n' | pbcopy` 确保生成的字符串无换行。
3.  **Streamlit 缓存**: 修改 `utils.py` 后，有时需要重启 App 才能清除 `st.cache` 的旧数据。

### 🚀 待办事项 (Roadmap)
* [x] **P0**: 修复 Google Sheets 写入时的 KeyError (已完成)
* [x] **P0**: 建立多模块协作架构 (Architecture & Task Specs) (已完成)
* [ ] **P1**: **反向同步脚本**: 编写脚本读取 GitHub 历史 .md 文件，将其结构化后补充写入 Google Sheets，填充中台历史数据。
* [ ] **P2**: **中台可视化升级**: 在 Radar 页面增加按“惊奇度”排序的功能；在 Macro 页面增加资产波动率图表。
* [ ] **P3**: **新博主接入**: 开发第二个博主的抓取脚本，测试 `base_scraper.py` 的兼容性。

---
*End of Log*

## 📅 2026-02-07 (中台搭建)
DEV_LOG: Cloud Persistence & UI Hardening 
Update版本: v2.0 (Cloud Native)状态: 🟢 Online (已上线)
核心变动: 本地存储 (json) $\rightarrow$ 云端数据库 (Google Sheets)🛠️ 
核心架构升级 (Infrastructure)云端持久化 (Persistence): 
彻底弃用易丢失的本地 JSON 文件，全线接入 Google Sheets (radar_data & macro_stream)。
系统重启后数据不再丢失，实现了真正的“永久记忆”。
智能路由 (AI Dispatch v2): 重构 utils.py 中的分发逻辑，增强了对“个股/公司”实体的识别权重（如 Tesla, NVDA 强制归类为 RADAR），并修复了 AI 返回 JSON 字段缺失导致的 KeyError。
📂 页面级更新 (Pages Change Log)
1. pages/0_Macro.py (宏观作战室)
[Fix] 数据同步: 修复了“清空记录”按钮只清空本地缓存的问题，现在操作会实时同步删除云端数据库内容。
[Feat] AI 校准: 自动校准功能现在基于云端存储的最近 15 条宏观情报流进行五维状态推演。
2. pages/1_Radar.py (情报雷达)[UI] 
硬核模式回归: 废弃了“卡片式”布局，回归 彭博终端风格 (Bloomberg-style) 的高密度列表。左侧高亮显示 惊奇度 (Surprise)。紧凑排列 时间戳 与 标签 (Tags)。
[Fix] 冷启动防御: 增加了对空数据库 (Empty DataFrame) 的防御逻辑，修复了因缺少列名 (KeyError: surprise) 导致的崩溃问题。
[Feat] 快速录入: 在顶部增加了折叠式的手动录入表单，支持手动添加高惊奇度情报。
3. pages/2_Detective.py & 3_Court.py (侦查与法庭)
[Refactor] 保存逻辑: 将所有的 save_data 调用升级为带参数版本，确保侦查笔记和判决结果能准确写入 radar_data 表格，不与宏观数据混淆。
📊 当前系统状态 (System Status)
数据流: ✅ 双向打通 (读/写) Google Sheets。
稳定性: ✅ 已解决空数据报错、API 格式错误。
输入源:主输入: Web 端 Home 页 / Radar 手动录入。
Beta: Telegram 机器人 (Moltbot) 正在测试中，作为移动端前哨。

---
*End of Log*