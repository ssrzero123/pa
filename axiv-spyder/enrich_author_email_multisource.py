import argparse
import csv
import json
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote_plus

import urllib3
from urllib3.util import Retry

urllib3.disable_warnings()

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")
URL_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)


@dataclass
class MissingRow:
    author: str
    email: str
    arxiv_id: str
    published: str
    title: str


class MultiSourceEmailEnricher:
    def __init__(self, interval: float = 1.5):
        retries = Retry(total=0, connect=0, read=0, redirect=0)
        self.http = urllib3.PoolManager(retries=retries, cert_reqs="CERT_NONE")
        self.headers = {"User-Agent": "axiv-spyder-email-enricher/1.0"}
        self.interval = max(0.3, interval)
        self._last_request_at = 0.0
        self.page_cache: Dict[str, str] = {}

    def _throttle(self) -> None:
        wait = self.interval - (time.time() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.time()

    def fetch_text(self, url: str) -> str:
        if url in self.page_cache:
            return self.page_cache[url]
        for attempt in range(1, 6):
            self._throttle()
            try:
                r = self.http.request(
                    "GET",
                    url,
                    headers=self.headers,
                    retries=False,
                    timeout=urllib3.Timeout(connect=8.0, read=20.0),
                )
                if r.status == 200:
                    text = r.data.decode("utf-8", errors="ignore")
                    self.page_cache[url] = text
                    return text
                if r.status in (429, 500, 502, 503, 504):
                    time.sleep(attempt * 2.0)
                    continue
                return ""
            except Exception:
                time.sleep(attempt * 1.0)
        return ""

    @staticmethod
    def extract_emails(text: str) -> List[str]:
        seen: Set[str] = set()
        out: List[str] = []
        for e in EMAIL_RE.findall(text):
            n = e.lower()
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out

    @staticmethod
    def normalize_name(author: str) -> List[str]:
        return [t for t in re.sub(r"[^a-zA-Z ]", " ", author).lower().split() if t]

    def email_matches_author(self, email: str, author: str) -> bool:
        local = re.sub(r"[^a-z]", "", email.split("@", 1)[0].lower())
        tokens = self.normalize_name(author)
        if not local or not tokens:
            return False
        first, last = tokens[0], tokens[-1]
        initials = "".join(t[0] for t in tokens if t)
        strong_candidates = {
            f"{first}{last}",
            f"{last}{first}",
            f"{first[:1]}{last}",
            f"{last}{first[:1]}",
            f"{initials}{last}",
            f"{last}{initials}",
        }
        if any(c and c in local for c in strong_candidates):
            return True
        # only accept direct token hit when token is not too short
        if len(first) >= 4 and first in local:
            return True
        if len(last) >= 4 and last in local:
            return True
        return False

    @staticmethod
    def extract_links(html: str) -> List[str]:
        links = []
        seen: Set[str] = set()
        for link in URL_RE.findall(html):
            link = link.strip()
            if link in seen:
                continue
            seen.add(link)
            links.append(link)
        return links

    def query_openalex(self, author: str) -> List[str]:
        url = f"https://api.openalex.org/authors?search={quote_plus(author)}&per-page=3"
        text = self.fetch_text(url)
        if not text:
            return []
        try:
            data = json.loads(text)
        except Exception:
            return []
        links: List[str] = []
        for item in data.get("results", []):
            ids = item.get("ids", {}) or {}
            orcid = ids.get("orcid")
            if isinstance(orcid, str) and orcid:
                links.append(orcid)
        return links

    def query_semantic_scholar(self, author: str) -> List[str]:
        url = (
            "https://api.semanticscholar.org/graph/v1/author/search?"
            f"query={quote_plus(author)}&limit=3&fields=name,homepage,url"
        )
        text = self.fetch_text(url)
        if not text:
            return []
        try:
            data = json.loads(text)
        except Exception:
            return []
        links: List[str] = []
        for item in data.get("data", []):
            for field in ("homepage", "url"):
                v = item.get(field)
                if isinstance(v, str) and v.startswith("http"):
                    links.append(v)
        return links

    def query_dblp(self, author: str) -> List[str]:
        url = f"https://dblp.org/search/author/api?q={quote_plus(author)}&h=3&format=json"
        text = self.fetch_text(url)
        if not text:
            return []
        try:
            data = json.loads(text)
        except Exception:
            return []
        hits = (
            data.get("result", {})
            .get("hits", {})
            .get("hit", [])
        )
        if isinstance(hits, dict):
            hits = [hits]
        links: List[str] = []
        for hit in hits:
            info = hit.get("info", {}) or {}
            for field in ("url", "author"):
                v = info.get(field)
                if isinstance(v, str) and v.startswith("http"):
                    links.append(v)
        return links

    def query_crossref(self, title: str) -> List[str]:
        q = quote_plus(title[:120])
        url = f"https://api.crossref.org/works?query.title={q}&rows=3"
        text = self.fetch_text(url)
        if not text:
            return []
        try:
            data = json.loads(text)
        except Exception:
            return []
        links: List[str] = []
        items = data.get("message", {}).get("items", [])
        for item in items:
            doi = item.get("DOI")
            if isinstance(doi, str) and doi:
                links.append(f"https://doi.org/{doi}")
        return links

    def gather_candidate_emails_by_paper(
        self,
        arxiv_id: str,
        title: str,
        include_external_links: bool = True,
    ) -> List[Tuple[str, str]]:
        candidates: List[Tuple[str, str]] = []
        abs_url = f"https://arxiv.org/abs/{arxiv_id}"
        html_url = f"https://arxiv.org/html/{arxiv_id}"

        abs_text = self.fetch_text(abs_url)
        html_text = self.fetch_text(html_url)
        for e in self.extract_emails(abs_text):
            candidates.append((e, "arxiv_abs"))
        for e in self.extract_emails(html_text):
            candidates.append((e, "arxiv_html"))

        if include_external_links:
            links = self.extract_links(abs_text)
            for link in links[:8]:
                page = self.fetch_text(link)
                for e in self.extract_emails(page):
                    candidates.append((e, f"page:{link[:80]}"))
            for e in self.query_github_readme(links):
                candidates.append((e, "github_readme"))
            for link in self.query_crossref(title):
                page = self.fetch_text(link)
                for e in self.extract_emails(page):
                    candidates.append((e, f"meta:{link[:80]}"))
        return candidates

    def query_github_readme(self, links: List[str]) -> List[str]:
        out: List[str] = []
        for link in links:
            if "github.com/" not in link:
                continue
            parts = link.split("github.com/", 1)[-1].split("/")
            if len(parts) < 2:
                continue
            owner, repo = parts[0], parts[1]
            raw = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
            text = self.fetch_text(raw)
            out.extend(self.extract_emails(text))
        return out

    def gather_candidate_emails(self, row: MissingRow) -> List[Tuple[str, str]]:
        candidates: List[Tuple[str, str]] = []
        arxiv_id = row.arxiv_id
        abs_url = f"https://arxiv.org/abs/{arxiv_id}"
        html_url = f"https://arxiv.org/html/{arxiv_id}"

        abs_text = self.fetch_text(abs_url)
        html_text = self.fetch_text(html_url)
        for e in self.extract_emails(abs_text):
            candidates.append((e, "arxiv_abs"))
        for e in self.extract_emails(html_text):
            candidates.append((e, "arxiv_html"))

        links = self.extract_links(abs_text)
        for link in links[:8]:
            page = self.fetch_text(link)
            for e in self.extract_emails(page):
                candidates.append((e, f"page:{link[:80]}"))
        for e in self.query_github_readme(links):
            candidates.append((e, "github_readme"))

        meta_links: List[str] = []
        meta_links.extend(self.query_openalex(row.author))
        meta_links.extend(self.query_semantic_scholar(row.author))
        meta_links.extend(self.query_dblp(row.author))
        meta_links.extend(self.query_crossref(row.title))
        seen_links: Set[str] = set()
        for link in meta_links:
            if link in seen_links:
                continue
            seen_links.add(link)
            page = self.fetch_text(link)
            for e in self.extract_emails(page):
                candidates.append((e, f"meta:{link[:80]}"))

        # Google Scholar only as manual补充入口，不做自动化抓取
        return candidates

    def enrich(
        self,
        author_csv: str,
        missing_csv: str,
        out_csv: str,
        limit: int = 0,
        enable_author_meta: bool = False,
        include_external_links: bool = True,
    ) -> None:
        base_rows = list(csv.DictReader(open(author_csv, "r", encoding="utf-8")))
        missing_rows = [MissingRow(**r) for r in csv.DictReader(open(missing_csv, "r", encoding="utf-8"))]
        if limit > 0:
            missing_rows = missing_rows[:limit]

        known_author_email: Set[Tuple[str, str]] = set()
        for r in base_rows:
            a = r.get("author", "").strip()
            e = r.get("email", "").strip().lower()
            if a and e:
                known_author_email.add((a.lower(), e))

        source_map: Dict[Tuple[str, str], str] = {}
        updated_count = 0
        by_paper: Dict[str, List[MissingRow]] = {}
        for row in missing_rows:
            by_paper.setdefault(row.arxiv_id, []).append(row)

        paper_items = list(by_paper.items())
        for idx, (arxiv_id, rows) in enumerate(paper_items, start=1):
            print(f"[{idx}/{len(paper_items)}] enrich paper {arxiv_id} authors={len(rows)}")
            candidates = self.gather_candidate_emails_by_paper(
                arxiv_id,
                rows[0].title,
                include_external_links=include_external_links,
            )

            if enable_author_meta:
                for row in rows:
                    if any(self.email_matches_author(e, row.author) for e, _ in candidates):
                        continue
                    links = (
                        self.query_openalex(row.author)
                        + self.query_semantic_scholar(row.author)
                        + self.query_dblp(row.author)
                    )
                    for link in links:
                        page = self.fetch_text(link)
                        for e in self.extract_emails(page):
                            candidates.append((e, f"meta:{link[:80]}"))

            for row in rows:
                chosen_email: Optional[str] = None
                chosen_source: str = ""
                seen_candidate: Set[str] = set()
                for email, source in candidates:
                    if email in seen_candidate:
                        continue
                    seen_candidate.add(email)
                    if not self.email_matches_author(email, row.author):
                        continue
                    if (row.author.lower(), email) in known_author_email:
                        continue
                    chosen_email = email
                    chosen_source = source
                    break

                if chosen_email:
                    known_author_email.add((row.author.lower(), chosen_email))
                    source_map[(row.author.lower(), row.arxiv_id)] = chosen_source
                    for r in base_rows:
                        if (
                            r.get("author", "") == row.author
                            and r.get("arxiv_id", "") == row.arxiv_id
                            and not r.get("email", "").strip()
                        ):
                            r["email"] = chosen_email
                            updated_count += 1
                            break

        # 全局去重作者邮箱
        deduped: List[Dict[str, str]] = []
        seen: Set[Tuple[str, str, str]] = set()
        for r in base_rows:
            a = r.get("author", "").strip().lower()
            e = r.get("email", "").strip().lower()
            aid = r.get("arxiv_id", "").strip().lower()
            key = (a, e, aid if not e else "")
            if key in seen:
                continue
            seen.add(key)
            r["source"] = source_map.get((r.get("author", "").lower(), r.get("arxiv_id", "")), r.get("source", ""))
            deduped.append(r)

        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            cols = ["author", "email", "arxiv_id", "published", "title", "source"]
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            writer.writerows(deduped)

        print(f"补全完成，新增邮箱: {updated_count}")
        print(f"输出文件: {out_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description="多渠道补全作者邮箱（论文页/主页/API）")
    parser.add_argument(
        "--author-csv",
        default="/Users/meiling/Desktop/work/pa/axiv-spyder/agent_author_email_2024_2026.csv",
    )
    parser.add_argument(
        "--missing-csv",
        default="/Users/meiling/Desktop/work/pa/axiv-spyder/agent_missing_author_email_2024_2026.csv",
    )
    parser.add_argument(
        "--out-csv",
        default="/Users/meiling/Desktop/work/pa/axiv-spyder/agent_author_email_2024_2026_enriched.csv",
    )
    parser.add_argument("--limit", type=int, default=0, help="只处理前N条缺失作者，0表示全部")
    parser.add_argument("--interval", type=float, default=1.5, help="请求间隔秒")
    parser.add_argument("--enable-author-meta", action="store_true", help="启用作者级元数据补全（更慢）")
    parser.add_argument("--no-external-links", action="store_true", help="仅使用arXiv页面，不抓外链站点")
    args = parser.parse_args()

    enricher = MultiSourceEmailEnricher(interval=args.interval)
    enricher.enrich(
        author_csv=args.author_csv,
        missing_csv=args.missing_csv,
        out_csv=args.out_csv,
        limit=args.limit,
        enable_author_meta=args.enable_author_meta,
        include_external_links=(not args.no_external_links),
    )


if __name__ == "__main__":
    main()
