"""Paste-a-link capture: fetch a job posting and pre-fill an application from it."""

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from django.conf import settings

from .extract import Posting, board_name, extract_posting
from .fetch import FetchError, fetch_page
from .samples import fetch_sample

logger = logging.getLogger(__name__)

__all__ = ["CaptureResult", "Posting", "board_name", "capture_posting", "normalize_posting_url"]


@dataclass
class CaptureResult:
    url: str
    posting: Posting | None = None
    error: str = ""


def normalize_posting_url(url):
    """Turn job board links copied from search pages into the posting's own address.

    LinkedIn and Indeed show postings inside search and feed pages, so the link
    people copy is often that page with the job ID in the query string.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    query = parse_qs(parts.query)
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        job_id = query.get("currentJobId", [""])[0]
        if job_id.isdigit():
            return f"https://www.linkedin.com/jobs/view/{job_id}/"
        if parts.path.startswith("/jobs/view/"):
            # Drop tracking parameters such as refId and trackingId.
            return f"https://{host}{parts.path}"
    if re.search(r"(^|\.)indeed\.", host):
        job_id = query.get("vjk", query.get("jk", [""]))[0]
        if re.fullmatch(r"[0-9a-f]{8,32}", job_id):
            return f"https://{host}/viewjob?jk={job_id}"
    return url


def _login_wall(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return host.endswith("linkedin.com") and parts.path.startswith(("/authwall", "/login", "/uas/"))


def capture_posting(url):
    """Fetch a posting and extract what it says. Never raises for a bad page.

    With CAPTURE_MODE=samples nothing is fetched: only the bundled sample
    postings are answered (see samples.py).
    """
    url = normalize_posting_url(url)
    fetch = fetch_sample if settings.CAPTURE_MODE == "samples" else fetch_page
    try:
        page = fetch(url)
    except FetchError as error:
        logger.warning("Capture of %s failed: %s", url, error)
        return CaptureResult(url=url, error=str(error))
    if _login_wall(page.url):
        logger.warning("Capture of %s hit a login wall at %s", url, page.url)
        return CaptureResult(
            url=url, error="LinkedIn asked for a login instead of showing the posting."
        )
    try:
        posting = extract_posting(page.content, page.url, page.charset)
    except Exception:
        logger.exception("Could not parse the page fetched from %s", page.url)
        return CaptureResult(url=url, error="The page was fetched but could not be read.")
    if posting.is_empty():
        logger.info("Capture of %s found no job details", url)
        return CaptureResult(url=url, error="The page loaded, but no job details were found on it.")
    logger.info(
        "Captured %s: role=%r company=%r location=%r", url, posting.role_title,
        posting.company, posting.location,
    )  # fmt: skip
    return CaptureResult(url=url, posting=posting)
