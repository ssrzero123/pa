import argparse
import csv
import html
import io
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import pdfplumber
import urllib3
from urllib3.util import Retry

urllib3.disable_warnings()

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")


@dataclass
class Paper:
    title: str
    authors: List[str]
    subject: str
    arxiv_id: str
    pdf_url: str


class PaperCrawler:
    def __init__(self):
        self.header = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_4) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/11.1 Safari/605.1.15"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "zh-cn",
            "Connection": "keep-alive",
        }
        retries = Retry(total=3, backoff_factor=0.4, status_forcelist=(429, 500, 502, 503, 504))
        self.http = urllib3.PoolManager(retries=retries, cert_reqs="CERT_NONE")

    @staticmethod
    def create_url(count: int) -> str:
        valid_show_values = (25, 50, 100, 250, 500, 1000, 2000)
        request_count = max(1, min(count, 2000))
        show = next((v for v in valid_show_values if request_count <= v), 2000)
        return f"https://arxiv.org/list/cs/pastweek?skip=0&show={show}"

    def fetch_listing(self, count: int) -> str:
        url = self.create_url(count)
        response = self.http.request("GET", url, headers=self.header, timeout=urllib3.Timeout(connect=6.0, read=20.0))
        if response.status != 200:
            raise RuntimeError(f"拉取列表失败，HTTP {response.status}")
        return response.data.decode("utf-8", errors="ignore")

    @staticmethod
    def _clean_text(raw: str) -> str:
        cleaned = re.sub(r"<.*?>", "", raw, flags=re.S)
        cleaned = html.unescape(cleaned)
        return " ".join(cleaned.split())

    def parse_papers(self, page: str, count: int) -> List[Paper]:
        items = re.findall(r"<dt>\s*(.*?)\s*</dt>\s*<dd>\s*(.*?)\s*</dd>", page, flags=re.S)
        papers: List[Paper] = []
        for dt_html, dd_html in items:
            id_match = re.search(r'href\s*=\s*["\']/abs/([^"\']+)["\']', dt_html, flags=re.S)
            title_match = re.search(r'<div class=[\'"]list-title mathjax[\'"]>(.*?)</div>', dd_html, flags=re.S)
            subject_match = re.search(r'<span class="primary-subject">(.*?)</span>', dd_html, flags=re.S)
            author_matches = re.findall(
                r'<a href=[\'"][^\'"]*search[^\'"]*searchtype=author[^\'"]*[\'"][^>]*>(.*?)</a>',
                dd_html,
                flags=re.S,
            )

            if not id_match or not title_match:
                continue

            arxiv_id = id_match.group(1).strip()
            title = self._clean_text(title_match.group(1).replace("Title:", ""))
            subject = self._clean_text(subject_match.group(1)) if subject_match else ""
            authors = [self._clean_text(a) for a in author_matches if self._clean_text(a)]
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

            papers.append(
                Paper(
                    title=title,
                    authors=authors,
                    subject=subject,
                    arxiv_id=arxiv_id,
                    pdf_url=pdf_url,
                )
            )
            if len(papers) >= count:
                break
        return papers

    def extract_emails_from_pdf(self, pdf_url: str) -> List[str]:
        try:
            response = self.http.request(
                "GET",
                pdf_url,
                headers=self.header,
                timeout=urllib3.Timeout(connect=8.0, read=30.0),
            )
            if response.status != 200:
                return []

            found: List[str] = []
            seen: Set[str] = set()
            with pdfplumber.open(io.BytesIO(response.data)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    for email in EMAIL_RE.findall(text):
                        email_norm = email.lower()
                        if email_norm not in seen:
                            seen.add(email_norm)
                            found.append(email_norm)
            return found
        except Exception:
            return []

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
        }
        if any(c and c in local for c in candidates):
            return True
        if len(first) >= 3 and first in local and len(last) >= 3 and last in local:
            return True
        return False

    def attribute_emails(self, paper: Paper, emails: List[str]) -> List[Tuple[str, str]]:
        author_email_pairs: List[Tuple[str, str]] = []
        for email in emails:
            matched_author = None
            for author in paper.authors:
                if self._email_belongs_to_author(email, author):
                    matched_author = author
                    break
            author_email_pairs.append((matched_author or "UNKNOWN", email))
        return author_email_pairs

    def crawl(self, count: int) -> List[Dict[str, str]]:
        page = self.fetch_listing(count)
        papers = self.parse_papers(page, count)

        rows: List[Dict[str, str]] = []
        seen_author_email: Set[Tuple[str, str]] = set()
        unique_emails: Set[str] = set()

        for idx, paper in enumerate(papers, start=1):
            print(f"[{idx}/{len(papers)}] 处理: {paper.arxiv_id} {paper.title}")
            emails = self.extract_emails_from_pdf(paper.pdf_url)
            for author, email in self.attribute_emails(paper, emails):
                key = (author.lower(), email.lower())
                if key in seen_author_email:
                    continue
                seen_author_email.add(key)
                unique_emails.add(email.lower())
                rows.append(
                    {
                        "author": author,
                        "email": email.lower(),
                        "arxiv_id": paper.arxiv_id,
                        "title": paper.title,
                        "subject": paper.subject,
                    }
                )

        self.write_csv(rows, "author_email.csv", ["author", "email", "arxiv_id", "title", "subject"])
        self.write_csv([{"email": e} for e in sorted(unique_emails)], "email.csv", ["email"])
        return rows

    @staticmethod
    def write_csv(rows: List[Dict[str, str]], path: str, columns: List[str]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)


def main(count: int) -> None:
    t_start = time.time()
    crawler = PaperCrawler()
    rows = crawler.crawl(count)
    print(f"完成，作者邮箱去重后共 {len(rows)} 条。")
    print(f"耗时: {time.time() - t_start:.2f} 秒")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="抓取 arXiv 论文并提取去重后的作者邮箱")
    parser.add_argument("--count", type=int, default=5, help="抓取论文数量（默认 5）")
    args = parser.parse_args()
    main(args.count)
