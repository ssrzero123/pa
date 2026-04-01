# ArXiv MailHunter

[English](README.md) | [中文](README_CN.md)

> 一款 Python 工具包，用于批量爬取 arXiv 论文，从 PDF 中提取作者邮箱地址，构建「作者-邮箱」映射数据库。支持关键词搜索、全量批量爬取、智能去重、多源邮箱补全等功能。

## 功能特性

- **关键词搜索爬取** — 通过 arXiv API 按关键词和年份范围搜索论文，多线程下载 PDF，提取邮箱并归属到对应作者
- **全量批量爬取** — 按月系统性地爬取 `cs.*` 全部分类论文，增量去重，支持断点续跑
- **多源邮箱补全** — 从 arXiv 摘要页、HTML 版、外链、GitHub README 以及学术 API（OpenAlex、Semantic Scholar、DBLP、Crossref）多渠道补全缺失邮箱
- **启发式作者-邮箱匹配** — 基于姓名模式匹配，自动将提取的邮箱归属到具体作者
- **断点续跑** — 所有爬虫通过状态文件支持崩溃恢复，中断不丢失进度

## 项目结构

```
pa/
├── README.md                                  # 英文文档
├── README_CN.md                               # 中文文档
├── axiv-spyder/                              # 主工作目录
│   ├── axiv_email.py                          # 基础爬虫（近一周 CS 论文）
│   ├── agent_email_crawler.py                 # 关键词搜索爬虫（多线程）
│   ├── cs_email_batcher.py                    # 大规模批量爬虫（cs.* 全量）
│   ├── enrich_author_email_multisource.py     # 多源邮箱补全工具
│   ├── run_continuous.sh                      # 持续批量爬取 Shell 脚本
│   ├── wait_for_csv.sh                        # CSV 产出监控脚本
│   ├── cs_crawl_state.json                    # 批量爬虫断点状态
│   └── *.csv                                  # 爬虫输出文件
├── csv/                                       # 批量爬虫输出目录
│   └── cs_email_batch_*.csv                   # 增量去重邮箱批次
└── axiv-spyder_export/                        # 核心脚本导出备份
```

## 安装

```bash
git clone https://github.com/ssrzero123/pa.git
cd pa/axiv-spyder
pip install pdfplumber urllib3
```

**环境要求：** Python >= 3.7

## 使用方法

### 1. 基础爬虫 — 近一周 CS 论文

爬取 arXiv 近一周最新 CS 论文，下载 PDF 并提取邮箱。

```bash
python3 axiv_email.py --count 20
```

输出文件：
- `author_email.csv` — 作者-邮箱映射及论文元数据
- `email.csv` — 全局去重的邮箱列表

### 2. 关键词搜索爬虫

按关键词和年份范围搜索 arXiv 论文，多线程下载 PDF 提取邮箱。

```bash
# 搜索 2024-2026 年包含 "agent" 关键词的论文
python3 agent_email_crawler.py --keyword agent --start-year 2024 --end-year 2026

# 自定义输出前缀
python3 agent_email_crawler.py --keyword llm --start-year 2024 --end-year 2026 --output-prefix llm

# 断点续跑
python3 agent_email_crawler.py --keyword agent --start-year 2024 --end-year 2026 --resume

# 调试：限制论文数量
python3 agent_email_crawler.py --keyword agent --start-year 2024 --end-year 2026 --max-papers 50
```

主要参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--keyword` | `agent` | 搜索关键词 |
| `--start-year` | 2024 | 起始年份 |
| `--end-year` | 2026 | 结束年份 |
| `--workers` | 6 | 并发下载线程数 |
| `--api-interval` | 3.2s | API 请求间隔 |
| `--pdf-interval` | 0.7s | PDF 下载间隔 |
| `--output-prefix` | `agent` | 输出文件名前缀 |
| `--resume` | false | 断点续跑模式 |
| `--max-papers` | -1 | 最大处理论文数（调试用） |

输出文件：
- `{prefix}_author_email_{start}_{end}.csv` — 全部作者邮箱记录
- `{prefix}_missing_author_email_{start}_{end}.csv` — 未匹配到邮箱的作者
- `{prefix}_unique_email_{start}_{end}.csv` — 全局唯一邮箱列表

### 3. 批量爬虫 — cs.* 全量分类

按月系统性爬取全部 CS 论文，增量去重。每个批次包含 50 个新邮箱（之前所有 CSV 中未出现过的）。

```bash
# 产出 1 个批次（50 个新邮箱）
python3 cs_email_batcher.py --max-batches 1

# 持续爬取
python3 cs_email_batcher.py --max-batches 99999

# 指定起始年月
python3 cs_email_batcher.py --start-year 2026 --start-month 2

# 调试：限制论文数量
python3 cs_email_batcher.py --max-papers 100
```

或通过 Shell 脚本持续运行：

```bash
bash run_continuous.sh
```

断点状态保存在 `cs_crawl_state.json` 中，每处理一篇论文实时保存，重启后自动从断点继续。

输出文件位于 `csv/` 目录：
- `cs_email_batch_{时间戳}_{序号:03d}_50.csv` — 每批 50 个新邮箱

### 4. 多源邮箱补全

利用多个数据源补全缺失邮箱：

```bash
# 基础补全（arXiv 页面 + 外链）
python3 enrich_author_email_multisource.py \
  --author-csv agent_author_email_2024_2026.csv \
  --missing-csv agent_missing_author_email_2024_2026.csv \
  --out-csv agent_author_email_2024_2026_enriched.csv

# 启用作者级元数据 API（更慢但更全面）
python3 enrich_author_email_multisource.py --enable-author-meta

# 仅使用 arXiv 页面（不抓取外链）
python3 enrich_author_email_multisource.py --no-external-links

# 调试：只处理前 N 条
python3 enrich_author_email_multisource.py --limit 10
```

使用的数据源（按顺序）：
1. arXiv 摘要页 (`/abs/{id}`)
2. arXiv HTML 版 (`/html/{id}`)
3. 摘要页中的外部链接
4. GitHub 项目 README
5. OpenAlex API（作者 ORCID 和主页）
6. Semantic Scholar API（作者主页）
7. DBLP API（作者信息页）
8. Crossref API（DOI 链接查找）

## 工作原理

### 数据管道

```
arXiv API / 网页列表  -->  PDF 下载  -->  邮箱提取  -->  作者归属匹配
                                                              |
                                                     缺失邮箱  -->  多源补全
                                                              |
                                                     输出：去重 CSV
```

### 作者-邮箱匹配算法

所有爬虫共享同一套启发式匹配逻辑：

1. 提取邮箱 `@` 前的本地部分，去除非字母字符
2. 将作者姓名拆分为 (first, last, initials) 三组 token
3. 生成候选组合：`firstlast`、`lastfirst`、`flast`、`lfirst`、`initialslast`、`lastinitials`、`first`、`last`
4. 如果任一候选字符串是邮箱本地部分的子串，则判定邮箱归属于该作者

### 速率限制

| 请求类型 | 默认间隔 | 最大重试 | 最大退避时间 |
|---------|---------|---------|------------|
| arXiv API | 3.2s | 7 次 | 120s（指数退避） |
| PDF 下载 | 0.7s | 7 次 | 120s |
| arXiv 分页 | 3.5s | - | - |
| 外部网页 | 1.5s | 5 次 | 10s |

### 去重机制

- **关键词爬虫：** 基于 `(author, email, arxiv_id)` 三元组去重
- **批量爬虫：** 加载 `pa/`、`pa/axiv-spyder/`、`pa/csv/` 下所有已有 CSV 的邮箱集合，仅输出新邮箱
- **补全工具：** 全局去重，通过 `source` 列标记邮箱来源

## CSV 格式

### 关键词爬虫 & 批量爬虫输出

| 列名 | 说明 |
|------|------|
| `author` | 作者全名 |
| `email` | 提取的邮箱地址 |
| `arxiv_id` | arXiv 论文 ID |
| `published` | 发表日期 |
| `title` | 论文标题 |
| `source` | 邮箱来源（仅补全文件） |

### 基础爬虫输出

| 列名 | 说明 |
|------|------|
| `author` | 作者全名 |
| `email` | 提取的邮箱地址 |
| `arxiv_id` | arXiv 论文 ID |
| `title` | 论文标题 |
| `subject` | arXiv 学科分类 |

## 免责声明

> **注意：** 本仓库可能包含数据产物（如 `*.csv` 文件），其中含有从公开可访问的 arXiv 论文中提取的个人联系信息（邮箱地址）。请负责任地使用，并遵守适用的隐私法规。

## 许可证

MIT
