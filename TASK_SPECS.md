# 采集特工开发指南 (Task Specifications)

## 1. 开发目标
为每一位优质博主量身定制抓取方案，确保能稳定绕过反爬虫（如 403 错误），并拿到干净的正文文本。

## 2. 开发环境与限制
- **严禁**：在你的脚本中直接写 Google Sheets 的写入逻辑。
- **严禁**：在你的脚本中直接写 GitHub 的上传逻辑。
- **必须**：通过 `from .base_scraper import dispatch_to_system` 来提交结果。

## 3. 新增抓取器流程
1. 在 `scrapers/` 文件夹下新建 `fetch_[博主名].py`。
2. 编写逻辑获取文章标题 (`title`)、正文 (`content`)、来源链接 (`url`)。
3. 在脚本末尾调用：
   ```python
   from .base_scraper import dispatch_to_system
   dispatch_to_system(title=title, content=content, author="博主名", source_url=url)