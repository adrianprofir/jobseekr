"""Read role, company, location and the ad text out of a job posting page.

Sources, most trusted first:

1. schema.org JobPosting data (JSON-LD), which Google asks job sites to publish
   and which LinkedIn, The Hub, Indeed and most career-site systems include.
2. Known page layouts and title formats of common job boards.
3. Generic page metadata: OpenGraph tags and the page title.

Every field takes the first non-empty value in that order. All of it is
best-effort: the user reviews the result before anything is saved.
"""

import html
import json
import re
from dataclasses import dataclass, fields
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from ..models import WorkMode

MAX_DESCRIPTION_CHARS = 50_000
FIELD_MAX_CHARS = 200

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "figure",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav",
    "ol", "p", "pre", "section", "table", "tr", "ul",
}  # fmt: skip
SKIPPED_TAGS = {
    "button", "canvas", "form", "iframe", "img", "input", "noscript", "object", "script",
    "select", "style", "svg", "template", "textarea",
}  # fmt: skip
# Page chrome left out when the ad text has to be taken from the whole page.
CHROME_TAGS = {"nav", "header", "footer", "aside", "dialog"}

TITLE_SEPARATORS = re.compile(r"\s+[|\-–—·•]\s+")


@dataclass
class Posting:
    role_title: str = ""
    company: str = ""
    location: str = ""
    work_mode: str = ""
    salary: str = ""
    description: str = ""
    source: str = ""

    def merge(self, other):
        """Fill this posting's empty fields from another one."""
        for field in fields(self):
            if not getattr(self, field.name):
                setattr(self, field.name, getattr(other, field.name))

    def is_empty(self):
        return not (self.role_title or self.company or self.description)


def clean(value, limit=FIELD_MAX_CHARS):
    if not isinstance(value, str):
        return ""
    value = " ".join(html.unescape(value).split())
    if len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value


def html_to_text(node):
    """Readable plain text from an HTML element: paragraphs, line breaks and bullets."""
    parts = []
    # An explicit stack instead of recursion: pages can nest very deeply.
    stack = [(node, False)]
    while stack:
        current, closing = stack.pop()
        if closing:
            # A list item ends where the next one starts; other blocks end a paragraph.
            if current.name in BLOCK_TAGS and current.name != "li":
                parts.append("\n\n")
            continue
        if isinstance(current, Comment):
            continue
        if isinstance(current, NavigableString):
            if type(current) is NavigableString:
                parts.append(re.sub(r"\s+", " ", str(current)))
            continue
        if not isinstance(current, Tag) or current.name in SKIPPED_TAGS:
            continue
        if current.name == "br":
            parts.append("\n")
            continue
        if current.name == "li":
            parts.append("\n- ")
        elif current.name in BLOCK_TAGS:
            parts.append("\n\n")
        stack.append((current, True))
        stack.extend((child, False) for child in reversed(current.contents))

    lines = []
    for line in "".join(parts).replace("\xa0", " ").split("\n"):
        line = " ".join(line.split())
        if line == "-":
            continue
        if line or (lines and lines[-1]):
            lines.append(line)
    text = "\n".join(lines).strip()
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS].rstrip() + "\n…"
    return text


def fragment_to_text(markup):
    """Text from an HTML fragment that may itself be entity-escaped (as LinkedIn does)."""
    if not isinstance(markup, str):
        return ""
    if "<" not in markup and "&lt;" in markup:
        markup = html.unescape(markup)
    return html_to_text(BeautifulSoup(markup, "html.parser"))


def text_of(soup, selector, limit=FIELD_MAX_CHARS):
    element = soup.select_one(selector)
    return clean(element.get_text(" "), limit) if element else ""


def element_text(soup, selector):
    element = soup.select_one(selector)
    return html_to_text(element) if element else ""


def meta(soup, *names):
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and (content := clean(tag.get("content", ""), 1000)):
            return content
    return ""


def page_title(soup):
    return clean(soup.title.get_text(" "), 1000) if soup.title else ""


# --- schema.org JobPosting -------------------------------------------------


def _iter_json_ld(soup):
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        try:
            data = json.loads(script.string or "", strict=False)
        except ValueError:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(reversed(item))
            elif isinstance(item, dict):
                yield item
                if "@graph" in item:
                    stack.append(item["@graph"])


def _is_job_posting(item):
    kind = item.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return "JobPosting" in kinds


def _name(value):
    if isinstance(value, dict):
        return clean(value.get("name", ""))
    return clean(value) if isinstance(value, str) else ""


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _place_name(place):
    if not isinstance(place, dict):
        return clean(place)
    address = place.get("address")
    if isinstance(address, str):
        return clean(address)
    if isinstance(address, dict):
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            if value := _name(address.get(key)):
                return value
    return _name(place)


def _salary(value):
    if not isinstance(value, dict):
        return clean(value) if isinstance(value, (str, int, float)) else ""
    currency = clean(value.get("currency", ""))
    amount = value.get("value")
    unit = ""
    if isinstance(amount, dict):
        unit = clean(amount.get("unitText", "")).lower()
        low, high = amount.get("minValue"), amount.get("maxValue")
        single = amount.get("value")
        if low is not None and high is not None and low != high:
            amount = f"{_number(low)}-{_number(high)}"
        else:
            amount = _number(single if single is not None else (low or high))
    else:
        amount = _number(amount)
    if not amount:
        return ""
    text = " ".join(part for part in (amount, currency) if part)
    return clean(f"{text} per {unit}" if unit else text)


def _number(value):
    if isinstance(value, (int, float)):
        return f"{value:,.0f}" if value >= 1000 else f"{value:g}"
    return clean(value) if isinstance(value, str) else ""


def from_json_ld(soup):
    for item in _iter_json_ld(soup):
        if not _is_job_posting(item):
            continue
        places = [_place_name(place) for place in _as_list(item.get("jobLocation"))]
        location = " / ".join(dict.fromkeys(place for place in places if place))
        remote = any(
            str(kind).upper() == "TELECOMMUTE" for kind in _as_list(item.get("jobLocationType"))
        )
        return Posting(
            role_title=clean(item.get("title", "")),
            company=_name(item.get("hiringOrganization")),
            location=clean(location),
            work_mode=WorkMode.REMOTE if remote and not location else "",
            salary=_salary(item.get("baseSalary")),
            description=fragment_to_text(item.get("description")),
        )
    return Posting()


# --- Job boards ------------------------------------------------------------


def from_linkedin(soup):
    posting = Posting(
        role_title=text_of(soup, ".top-card-layout__title, .topcard__title"),
        company=text_of(soup, ".topcard__org-name-link, .topcard__flavor a"),
        location=text_of(soup, ".topcard__flavor--bullet"),
        description=element_text(soup, ".show-more-less-html__markup, .description__text"),
    )
    title = meta(soup, "og:title") or page_title(soup)
    patterns = [
        # "Acme hiring Senior Engineer in Aarhus | LinkedIn"
        r"^(?P<company>.+?) hiring (?P<role>.+?) in (?P<location>.+?) \| LinkedIn",
        # Danish: "Acme søger en Senior Engineer i Aarhus | LinkedIn"
        r"^(?P<company>.+?) søger (?:en |et )?(?P<role>.+?) i (?P<location>.+?) \| LinkedIn",
        # "Senior Engineer at Acme — Aarhus, Denmark | LinkedIn Jobs"
        r"^(?P<role>.+?) at (?P<company>.+?) [—–-] (?P<location>.+?) \| LinkedIn",
    ]
    for pattern in patterns:
        if match := re.match(pattern, title):
            posting.merge(
                Posting(
                    role_title=clean(match["role"]),
                    company=clean(match["company"]),
                    location=clean(match["location"]),
                )
            )
            break
    return posting


def from_jobindex(soup):
    posting = Posting(
        role_title=meta(soup, "og:title")
        or text_of(soup, ".PaidJob-inner h4, .jobtext-jobad h1, h1"),
        company=text_of(soup, ".jix-toolbar-top__company a, .vp-card__name"),
        location=text_of(soup, ".jix_robotjob--area, .jobad-element-area span"),
    )
    # The ad repeats the title and location above its text; keep only the text.
    ad = soup.select_one(".PaidJob-inner, .jobtext-jobad__body, .jobtext-jobad")
    if ad:
        for element in ad.select("h1, h4, .jobad-element-area"):
            element.decompose()
        posting.description = html_to_text(ad)
    return posting


def from_thehub(soup):
    posting = Posting()
    # "The Hub | Founding Backend Engineer | Acme"
    parts = [part.strip() for part in (meta(soup, "og:title") or page_title(soup)).split("|")]
    if len(parts) == 3 and parts[0] == "The Hub":
        posting.role_title, posting.company = clean(parts[1]), clean(parts[2])
    return posting


def from_indeed(soup):
    posting = Posting(
        role_title=text_of(soup, "[data-testid='jobsearch-JobInfoHeader-title'], h1"),
        company=text_of(soup, "[data-testid='inlineHeader-companyName'], [data-company-name]"),
        location=text_of(soup, "[data-testid='inlineHeader-companyLocation']"),
        description=element_text(soup, "#jobDescriptionText"),
    )
    # "Backend Developer - Acme ApS - København | Indeed.com"
    title = re.sub(r"\s*\|\s*Indeed(\.\w+)*$", "", meta(soup, "og:title") or page_title(soup))
    parts = [part.strip() for part in title.split(" - ")]
    if len(parts) >= 3:
        posting.merge(
            Posting(
                role_title=clean(" - ".join(parts[:-2])),
                company=clean(parts[-2]),
                location=clean(parts[-1]),
            )
        )
    posting.role_title = posting.role_title.removesuffix(" - job post").strip()
    return posting


# Job boards by domain: the name used as the application's source, and the
# parser for their pages.
BOARDS = {
    "linkedin.com": ("LinkedIn", from_linkedin),
    "jobindex.dk": ("Jobindex", from_jobindex),
    "thehub.io": ("The Hub", from_thehub),
    "indeed.com": ("Indeed", from_indeed),
}
BOARD_NAMES = {name for name, _ in BOARDS.values()}


def board_for(url):
    """The (name, parser) of the job board a URL belongs to, or None."""
    host = (urlsplit(url).hostname or "").lower()
    for domain, board in BOARDS.items():
        name = domain.split(".")[0]
        # Indeed and LinkedIn run country sites such as dk.indeed.com or indeed.co.uk.
        if host == domain or host.endswith("." + domain) or f".{name}." in f".{host}.":
            return board
    return None


def board_name(url):
    board = board_for(url)
    return board[0] if board else ""


# --- Any page --------------------------------------------------------------


def from_metadata(soup):
    site_name = meta(soup, "og:site_name")
    title = meta(soup, "og:title", "twitter:title") or page_title(soup)
    role = title
    company = ""
    if site_name and site_name not in BOARD_NAMES:
        company = clean(site_name)
    # Drop a site name or "Careers" segment from titles like "Data Engineer - Acme".
    segments = [segment for segment in TITLE_SEPARATORS.split(title) if segment]
    if len(segments) > 1:
        role = segments[0]
        if not company and len(segments) == 2:
            tail = segments[1]
            if not re.search(r"(?i)\b(careers?|jobs?|karriere|job)\b", tail):
                company = tail
    return Posting(role_title=clean(role), company=clean(company))


def _main_text(soup):
    container = (
        soup.select_one("[itemprop='description']")
        or soup.find("main")
        or soup.find("article")
        or soup.select_one("[role='main']")
        or soup.body
    )
    if container is None:
        return meta(soup, "og:description", "description")
    for tag in container.find_all(CHROME_TAGS):
        tag.decompose()
    text = html_to_text(container)
    return text or meta(soup, "og:description", "description")


# Titles of bot checks and login walls served instead of the posting.
BLOCKED_PAGE_TITLES = re.compile(
    r"(?i)^(authenticating|just a moment|attention required|security check|sign in|log ?in)\b"
)


def extract_posting(content, url, charset=None):
    """Best-effort job details from a fetched page."""
    if isinstance(content, bytes):
        soup = BeautifulSoup(content, "html.parser", from_encoding=charset)
    else:
        soup = BeautifulSoup(content, "html.parser")
    if BLOCKED_PAGE_TITLES.match(page_title(soup)) and not soup.find(
        "script", attrs={"type": re.compile(r"ld\+json", re.I)}
    ):
        return Posting()
    posting = from_json_ld(soup)
    if board := board_for(url):
        name, parser = board
        posting.merge(parser(soup))
        posting.source = name
    posting.merge(from_metadata(soup))
    if not posting.description:
        posting.description = _main_text(soup)
    return posting
