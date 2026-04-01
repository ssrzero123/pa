# ArXiv MailHunter

[English](README.md) | [中文](README_CN.md)

> A Python toolkit for crawling arXiv papers, extracting author email addresses from PDFs, and building author-email mapping databases. Supports keyword search, full-category batch crawling, deduplication, and multi-source email recovery.

## Features

- **Keyword Search Crawling** — Search arXiv papers by keyword and year range via API, download PDFs with multi-threading, extract and assign emails to authors
- **Full-Category Batch Crawling** — Systematically crawl all `cs.*` papers month by month with incremental deduplication and crash recovery
- **Multi-Source Email Enrichment** — Recover missing emails from arXiv abstract pages, HTML versions, external links, GitHub READMEs, and academic APIs (OpenAlex, Semantic Scholar, DBLP, Crossref)
- **Heuristic Author-Email Matching** — Automatically assign extracted emails to specific authors based on name pattern matching
- **Checkpoint & Resume** — All crawlers support crash recovery via state files; no progress is lost on interruption

## Project Structure

```
pa/
├── README.md
├── axiv-spyder/                              # Main workspace
│   ├── axiv_email.py                          # Basic crawler (recent week's CS papers)
│   ├── agent_email_crawler.py                 # Keyword search crawler (multi-threaded)
│   ├── cs_email_batcher.py                    # Large-scale batch crawler (cs.* all papers)
│   ├── enrich_author_email_multisource.py     # Multi-source email recovery tool
│   ├── run_continuous.sh                      # Continuous batch crawling shell script
│   ├── wait_for_csv.sh                        # CSV output monitor script
│   ├── cs_crawl_state.json                    # Batch crawler checkpoint state
│   └── *.csv                                  # Crawler output files
├── csv/                                       # Batch crawler output directory
│   └── cs_email_batch_*.csv                   # Incremental deduplicated email batches
└── axiv-spyder_export/                        # Exported backup of core scripts
```

## Installation

```bash
git clone https://github.com/ssrzero123/pa.git
cd pa/axiv-spyder
pip install pdfplumber urllib3
```

**Requirements:** Python >= 3.7

## Usage

### 1. Basic Crawler — Recent Week's CS Papers

Crawl the latest CS papers from the past week, download PDFs and extract emails.

```bash
python3 axiv_email.py --count 20
```

Output files:
- `author_email.csv` — Author-email mapping with paper metadata
- `email.csv` — Globally deduplicated email list

### 2. Keyword Search Crawler

Search arXiv papers by keyword within a year range, with multi-threaded PDF downloading.

```bash
# Search "agent" keyword in 2024-2026 papers
python3 agent_email_crawler.py --keyword agent --start-year 2024 --end-year 2026

# Custom output prefix
python3 agent_email_crawler.py --keyword llm --start-year 2024 --end-year 2026 --output-prefix llm

# Resume from interruption
python3 agent_email_crawler.py --keyword agent --start-year 2024 --end-year 2026 --resume

# Debug: limit paper count
python3 agent_email_crawler.py --keyword agent --start-year 2024 --end-year 2026 --max-papers 50
```

Key parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--keyword` | `agent` | Search keyword |
| `--start-year` | 2024 | Start year |
| `--end-year` | 2026 | End year |
| `--workers` | 6 | Concurrent download threads |
| `--api-interval` | 3.2s | Delay between API requests |
| `--pdf-interval` | 0.7s | Delay between PDF downloads |
| `--output-prefix` | `agent` | Output filename prefix |
| `--resume` | false | Resume from checkpoint |
| `--max-papers` | -1 | Max papers to process (debug) |

Output files:
- `{prefix}_author_email_{start}_{end}.csv` — All author-email records
- `{prefix}_missing_author_email_{start}_{end}.csv` — Authors with unmatched emails
- `{prefix}_unique_email_{start}_{end}.csv` — Globally unique email list

### 3. Batch Crawler — Full cs.* Category

Systematically crawl all CS papers month by month with incremental deduplication. Each batch contains 50 new emails (not seen in any previous CSV).

```bash
# Produce 1 batch (50 new emails)
python3 cs_email_batcher.py --max-batches 1

# Continuous crawling
python3 cs_email_batcher.py --max-batches 99999

# Specify start year/month
python3 cs_email_batcher.py --start-year 2026 --start-month 2

# Debug: limit paper count
python3 cs_email_batcher.py --max-papers 100
```

Or run continuously via shell script:

```bash
bash run_continuous.sh
```

Checkpoint state is saved to `cs_crawl_state.json` after each paper. The crawler automatically resumes from the last checkpoint on restart.

Output files in `csv/` directory:
- `cs_email_batch_{timestamp}_{seq:03d}_50.csv` — Each batch with 50 new emails

### 4. Multi-Source Email Enrichment

Recover missing emails using multiple data sources:

```bash
# Basic enrichment (arXiv pages + external links)
python3 enrich_author_email_multisource.py \
  --author-csv agent_author_email_2024_2026.csv \
  --missing-csv agent_missing_author_email_2024_2026.csv \
  --out-csv agent_author_email_2024_2026_enriched.csv

# Enable author-level metadata APIs (slower but more thorough)
python3 enrich_author_email_multisource.py --enable-author-meta

# ArXiv pages only (no external link scraping)
python3 enrich_author_email_multisource.py --no-external-links

# Debug: process only first N records
python3 enrich_author_email_multisource.py --limit 10
```

Data sources used (in order):
1. arXiv abstract page (`/abs/{id}`)
2. arXiv HTML version (`/html/{id}`)
3. External links from abstract page
4. GitHub project READMEs
5. OpenAlex API (author ORCID & homepage)
6. Semantic Scholar API (author profiles)
7. DBLP API (author pages)
8. Crossref API (DOI link lookup)

## How It Works

### Data Pipeline

```
arXiv API / Web Pages  -->  PDF Download  -->  Email Extraction  -->  Author Assignment
                                                                      |
                                                             Missing Emails  -->  Multi-Source Enrichment
                                                                      |
                                                             Output: Deduplicated CSV
```

### Author-Email Matching Algorithm

All crawlers share the same heuristic matching logic:

1. Extract the local part of the email (before `@`), strip non-alphabetic characters
2. Tokenize author name into (first, last, initials)
3. Generate candidate patterns: `firstlast`, `lastfirst`, `flast`, `lfirst`, `initialslast`, `lastinitials`, `first`, `last`
4. If any candidate is a substring of the email local part, the email is assigned to that author

### Rate Limiting

| Request Type | Default Interval | Max Retries | Max Backoff |
|-------------|-----------------|-------------|-------------|
| arXiv API | 3.2s | 7 | 120s (exponential) |
| PDF Download | 0.7s | 7 | 120s |
| arXiv Pagination | 3.5s | - | - |
| External Web Pages | 1.5s | 5 | 10s |

### Deduplication

- **Keyword Crawler:** Deduplicates by `(author, email, arxiv_id)` tuple
- **Batch Crawler:** Loads all existing CSVs across `pa/`, `pa/axiv-spyder/`, `pa/csv/` directories and only emits emails not seen before
- **Enrichment Tool:** Global deduplication with `source` column tracking email origin

## CSV Format

### Keyword & Batch Crawler Output

| Column | Description |
|--------|-------------|
| `author` | Author full name |
| `email` | Extracted email address |
| `arxiv_id` | arXiv paper ID |
| `published` | Publication date |
| `title` | Paper title |
| `source` | Email source (enriched files only) |

### Basic Crawler Output

| Column | Description |
|--------|-------------|
| `author` | Author full name |
| `email` | Extracted email address |
| `arxiv_id` | arXiv paper ID |
| `title` | Paper title |
| `subject` | arXiv subject category |

## Disclaimer

> **Note:** This repository may include data artifacts (e.g., `*.csv` files) that contain personal contact information (email addresses) extracted from publicly available arXiv papers. Please use responsibly and in compliance with applicable privacy regulations.

## License

MIT
