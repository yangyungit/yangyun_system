# Moltbot 投资研究中台 - 总体设计架构

## 1. 系统核心理念
本项目采用**“主板与插件”**式设计。
- **主板 (Core)**: 负责数据存储 (Google Sheets)、知识归档 (GitHub) 和 AI 分析。
- **插件 (Scrapers)**: 每个博主或数据源对应一个独立的抓取脚本。

## 2. 模块分工
- `/utils.py`: **严禁随意修改**。包含 Google API 认证、数据库读写函数（load_data/save_data）以及 AI 客户端初始化。
- `/scrapers/base_scraper.py`: **核心接口**。所有抓取脚本必须调用 `dispatch_to_system` 函数，禁止跳过接口直接写库。
- `/scrapers/`: 存放具体抓取逻辑。
- `/pages/`: 存放 Streamlit UI 展示代码。

## 3. 数据流向
1. **获取**: `scrapers/*.py` 抓取原始内容。
2. **清洗**: 调用 `base_scraper.py` 进行标准化转换。
3. **分析**: 调用 `utils.auto_dispatch` (Gemini) 提取信号。
4. **持久化**:
   - 结构化摘要 -> Google Sheets (yangyun_system_db)。
   - 完整原文 -> GitHub (obsidian_notes)。
5. **展示**: `/pages/*.py` 从 Google Sheets 读取数据并渲染中台大表。

## 4. 协作守则
- 凡是涉及 API Key、数据库连接、公共函数的修改，必须由 **架构师 (你)** 完成。
- **采集特工 (同事)** 只能在 `scrapers/` 下新增脚本，禁止修改 `utils.py`。