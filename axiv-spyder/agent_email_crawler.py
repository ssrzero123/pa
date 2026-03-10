import argparse
import csv
import io
import re
import time
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pdfplumber
import urllib3
from urllib3.util import Retry

urllib3.disable_warnings()

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")
VALID_MAX_RESULTS = 100


@dataclass
class Paper:
    arxiv_id: str
    title: str
    summary: str
    published: str
    year: int
    authors: List[str]
    pdf_url: str


class ArxivAgentEmailCrawler:
    def __init__(
        self,
        keyword: str,
        start_year: int,
        end_year: int,
        workers: int = 6,
        output_prefix: str = "agent",
    ):
        self.keyword = keyword.strip().lower()
        self.start_year = start_year
        self.end_year = end_year
        self.workers = max(1, workers)
        self.output_prefix = output_prefix.strip() or "agent"
        retries = Retry(total=0, connect=0, read=0, redirect=0)
        self.http = urllib3.PoolManager(retries=retries, cert_reqs="CERT_NONE")
        self.headers = {
            "User-Agent": "axiv-spyder-agent-email-crawler/1.0",
            "Accept-Encoding": "gzip, deflate, br",
        }
        self._last_request_at = 0.0
        self._request_lock = threading.Lock()
        self._api_min_interval_sec = 3.2
        self._pdf_min_interval_sec = 0.7
        self.author_csv = f"{self.output_prefix}_author_email_{self.start_year}_{self.end_year}.csv"
        self.missing_csv = f"{self.output_prefix}_missing_author_email_{self.start_year}_{self.end_year}.csv"
        self.unique_csv = f"{self.output_prefix}_unique_email_{self.start_year}_{self.end_year}.csv"

    def set_rate_limits(self, api_interval: float, pdf_interval: float) -> None:
        self._api_min_interval_sec = max(0.5, float(api_interval))
        self._pdf_min_interval_sec = max(0.2, float(pdf_interval))

    def _request(self, url: str) -> bytes:
        min_interval = self._api_min_interval_sec if "/api/query?" in url else self._pdf_min_interval_sec
        for attempt in range(1, 8):
            with self._request_lock:
                now = time.time()
                wait = min_interval - (now - self._last_request_at)
                if wait > 0:
                    time.sleep(wait)
                self._last_request_at = time.time()
            response = self.http.request(
                "GET",
                url,
                headers=self.headers,
                retries=False,
                timeout=urllib3.Timeout(connect=8.0, read=40.0),
            )
            if response.status == 200:
                return response.data
            if response.status in (429, 500, 502, 503, 504):
                sleep_sec = min(120, 5.0 * attempt)
                print(f"请求重试: HTTP {response.status}, attempt={attempt}, sleep={sleep_sec:.1f}s")
                time.sleep(sleep_sec)
                continue
            raise RuntimeError(f"请求失败: {url} -> HTTP {response.status}")
        raise RuntimeError(f"请求多次失败: {url}")

    def _in_year_range(self, published: str) -> bool:
        year = int(published[:4])
        return self.start_year <= year <= self.end_year

    def _entry_matches_keyword(self, title: str, summary: str) -> bool:
        text = f"{title}\n{summary}".lower()
        return self.keyword in text

    def fetch_papers(self, max_papers: Optional[int] = None) -> List[Paper]:
        start = 0
        papers: List[Paper] = []
        seen_ids: Set[str] = set()
        date_filter = f"submittedDate:[{self.start_year}01010000+TO+{self.end_year}12312359]"
        query = f"search_query=(ti:{self.keyword}+OR+abs:{self.keyword})+AND+{date_filter}"

        while True:
            url = (
                "https://export.arxiv.org/api/query?"
                f"{query}&start={start}&max_results={VALID_MAX_RESULTS}"
                "&sortBy=submittedDate&sortOrder=descending"
            )
            payload = self._request(url)
            root = ET.fromstring(payload)
            entries = root.findall("atom:entry", ATOM_NS)
            if not entries:
                break

            stop_for_old_year = False
            for entry in entries:
                raw_id = (entry.findtext("atom:id", default="", namespaces=ATOM_NS) or "").strip()
                title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
                summary = (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip()
                published = (entry.findtext("atom:published", default="", namespaces=ATOM_NS) or "").strip()
                if not raw_id or not published:
                    continue

                year = int(published[:4])
                if year < self.start_year:
                    stop_for_old_year = True
                    continue
                if year > self.end_year:
                    continue
                if not self._entry_matches_keyword(title, summary):
                    continue

                arxiv_id = raw_id.rsplit("/", 1)[-1]
                if arxiv_id in seen_ids:
                    continue
                seen_ids.add(arxiv_id)

                authors = []
                for a in entry.findall("atom:author", ATOM_NS):
                    name = (a.findtext("atom:name", default="", namespaces=ATOM_NS) or "").strip()
                    if name:
                        authors.append(name)

                papers.append(
                    Paper(
                        arxiv_id=arxiv_id,
                        title=" ".join(title.split()),
                        summary=" ".join(summary.split()),
                        published=published,
                        year=year,
                        authors=authors,
                        pdf_url=f"https://export.arxiv.org/pdf/{arxiv_id}.pdf",
                    )
                )
                if max_papers and len(papers) >= max_papers:
                    return papers

            if stop_for_old_year:
                break
            start += VALID_MAX_RESULTS
            time.sleep(3.5)
        return papers

    @staticmethod
    def _normalize_name(author: str) -> List[str]:
        tokens = re.sub(r"[^a-zA-Z ]", " ", author).lower().split()
        return [t for t in tokens if t]

    def _email_belongs_to_author(self, email: str, author: str) -> bool:
        local = re.sub(r"[^a-z]", "", email.split("@", 1)[0].lower())
        tokens = self._normalize_name(author)
        if not local or not tokens:
            return False
        first = tokens[0]
        last = tokens[-1]
        initials = "".join(t[0] for t in tokens if t)
        candidates = {
            f"{first}{last}",
            f"{last}{first}",
            f"{first[:1]}{last}",
            f"{last}{first[:1]}",
            f"{initials}{last}",
            f"{last}{initials}",
            first,
            last,
        }
        return any(c and c in local for c in candidates)

    def extract_emails_from_pdf(self, paper: Paper) -> List[str]:
        try:
            payload = self._request(paper.pdf_url)
            found: List[str] = []
            seen: Set[str] = set()
            with pdfplumber.open(io.BytesIO(payload)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    for email in EMAIL_RE.findall(text):
                        norm = email.lower()
                        if norm not in seen:
                            seen.add(norm)
                            found.append(norm)
            return found
        except Exception:
            return []

    def assign_emails(self, paper: Paper, emails: List[str]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        rows: List[Dict[str, str]] = []
        missing_rows: List[Dict[str, str]] = []
        used_emails: Set[str] = set()

        for author in paper.authors:
            matched = ""
            for email in emails:
                if email in used_emails:
                    continue
                if self._email_belongs_to_author(email, author):
                    matched = email
                    used_emails.add(email)
                    break

            row = {
                "author": author,
                "email": matched,
                "arxiv_id": paper.arxiv_id,
                "published": paper.published,
                "title": paper.title,
            }
            rows.append(row)
            if not matched:
                missing_rows.append(row)

        for email in emails:
            if email in used_emails:
                continue
            rows.append(
                {
                    "author": "UNKNOWN",
                    "email": email,
                    "arxiv_id": paper.arxiv_id,
                    "published": paper.published,
                    "title": paper.title,
                }
            )
        return rows, missing_rows

    @staticmethod
    def _dedupe_rows(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
        deduped: List[Dict[str, str]] = []
        seen: Set[Tuple[str, str, str]] = set()
        for row in rows:
            author = row["author"].strip().lower()
            email = row["email"].strip().lower()
            arxiv_id = row["arxiv_id"].strip().lower()
            if email:
                key = (author, email, "")
            else:
                key = (author, email, arxiv_id)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped

    @staticmethod
    def write_csv(path: str, rows: List[Dict[str, str]], columns: List[str]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def run(self, max_papers: Optional[int] = None, resume: bool = False) -> None:
        papers = self.fetch_papers(max_papers=max_papers)
        print(f"命中论文数: {len(papers)}")
        if not papers:
            self.write_csv(self.author_csv, [], ["author", "email", "arxiv_id", "published", "title"])
            self.write_csv(self.missing_csv, [], ["author", "email", "arxiv_id", "published", "title"])
            return

        all_rows: List[Dict[str, str]] = []
        missing_rows: List[Dict[str, str]] = []
        processed_ids: Set[str] = set()

        if resume:
            all_rows = self._read_csv_if_exists(self.author_csv)
            missing_rows = self._read_csv_if_exists(self.missing_csv)
            processed_ids = {r["arxiv_id"] for r in all_rows if r.get("arxiv_id")}
            print(f"断点续跑: 已存在 {len(processed_ids)} 篇论文记录，将跳过已处理论文。")

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            pending = [p for p in papers if p.arxiv_id not in processed_ids]
            futures = {pool.submit(self.extract_emails_from_pdf, paper): paper for paper in pending}
            for idx, fut in enumerate(as_completed(futures), start=1):
                paper = futures[fut]
                emails = fut.result()
                paper_rows, paper_missing = self.assign_emails(paper, emails)
                all_rows.extend(paper_rows)
                missing_rows.extend(paper_missing)
                print(f"[{idx}/{len(pending)}] {paper.arxiv_id} authors={len(paper.authors)} emails={len(emails)}")
                if idx % 25 == 0:
                    self.write_csv(
                        self.author_csv,
                        self._dedupe_rows(all_rows),
                        ["author", "email", "arxiv_id", "published", "title"],
                    )
                    self.write_csv(
                        self.missing_csv,
                        self._dedupe_rows(missing_rows),
                        ["author", "email", "arxiv_id", "published", "title"],
                    )

        all_rows = self._dedupe_rows(all_rows)
        missing_rows = self._dedupe_rows(missing_rows)
        unique_emails = sorted({r["email"].lower() for r in all_rows if r["email"].strip()})

        self.write_csv(
            self.author_csv,
            all_rows,
            ["author", "email", "arxiv_id", "published", "title"],
        )
        self.write_csv(
            self.missing_csv,
            missing_rows,
            ["author", "email", "arxiv_id", "published", "title"],
        )
        self.write_csv(self.unique_csv, [{"email": e} for e in unique_emails], ["email"])
        print(f"作者记录(去重后): {len(all_rows)}")
        print(f"唯一邮箱数: {len(unique_emails)}")
        print(f"未匹配作者记录: {len(missing_rows)}")
        print(f"输出文件: {self.author_csv}, {self.unique_csv}, {self.missing_csv}")

    @staticmethod
    def _read_csv_if_exists(path: str) -> List[Dict[str, str]]:
        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        except FileNotFoundError:
            return []


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取2024-2026年题目/摘要包含agent的论文作者邮箱")
    parser.add_argument("--keyword", default="agent", help="检索关键词，默认 agent")
    parser.add_argument("--start-year", type=int, default=2024, help="开始年份，默认 2024")
    parser.add_argument("--end-year", type=int, default=2026, help="结束年份，默认 2026")
    parser.add_argument("--workers", type=int, default=6, help="并发下载PDF数量，默认 6")
    parser.add_argument("--max-papers", type=int, default=0, help="调试用，0 表示不限制")
    parser.add_argument("--resume", action="store_true", help="从现有 CSV 断点续跑")
    parser.add_argument("--api-interval", type=float, default=3.2, help="API请求最小间隔秒数，默认 3.2")
    parser.add_argument("--pdf-interval", type=float, default=0.7, help="PDF请求最小间隔秒数，默认 0.7")
    parser.add_argument("--output-prefix", default="", help="输出文件前缀，默认使用 keyword")
    args = parser.parse_args()

    max_papers: Optional[int] = None if args.max_papers <= 0 else args.max_papers
    crawler = ArxivAgentEmailCrawler(
        keyword=args.keyword,
        start_year=args.start_year,
        end_year=args.end_year,
        workers=args.workers,
        output_prefix=(args.output_prefix or args.keyword),
    )
    crawler.set_rate_limits(args.api_interval, args.pdf_interval)
    started = time.time()
    crawler.run(max_papers=max_papers, resume=args.resume)
    print(f"耗时: {time.time() - started:.2f} 秒")


if __name__ == "__main__":
    main()
