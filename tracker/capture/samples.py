"""Sample postings for `CAPTURE_MODE=samples`: link capture without any outbound request.

A public demo must not make its server fetch whatever a visitor pastes, so in
samples mode capture only knows the links below. Each one is answered from a
synthetic page in `postings/` (the same pages the capture tests use) and then
goes through the normal extraction, so the demo shows exactly what capture does
with a real board's page. The Indeed sample answers like Indeed does to a bot,
to show what happens when a board blocks the request. Any other link is
refused without being fetched.
"""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .fetch import FetchedPage, FetchError, http_error_message

POSTINGS = Path(__file__).parent / "postings"

REFUSED = (
    "This demo only captures its sample postings, listed under the link box on the "
    "Applications page, and never fetches other sites. "
    "Run your own copy of jobseekr to capture any link."
)


@dataclass(frozen=True)
class Sample:
    board: str
    url: str
    page: str
    description: str
    # The HTTP status the board answers with; 403 is a board blocking the request.
    status: int = 200

    @property
    def blocked(self):
        return self.status >= 400


SAMPLES = [
    Sample(
        "LinkedIn",
        "https://www.linkedin.com/jobs/view/1234567890/",
        "linkedin-www.html",
        "Senior Python Engineer at Lindholm Analytics",
    ),
    Sample(
        "Jobindex",
        "https://www.jobindex.dk/vis-job/h1000001",
        "jobindex.html",
        "Junior Python Developer at Tidewater Imaging",
    ),
    Sample(
        "Teamtailor",
        "https://career.harbourline.example/jobs/1000001-senior-data-engineer",
        "teamtailor.html",
        "Senior Data Engineer at Harbourline Systems",
    ),
    Sample(
        "The Hub",
        "https://thehub.io/jobs/0123456789abcdef01234567",
        "thehub.html",
        "Founding Backend Engineer at Kestrel Sports",
    ),
    Sample(
        "Indeed",
        "https://dk.indeed.com/viewjob?jk=0123456789abcdef",
        "indeed-blocked.html",
        "Indeed blocks automated requests, so capture cannot read it",
        status=403,
    ),
]


def _key(url):
    """Compare links without the trailing slash or a difference in scheme case."""
    return url.strip().rstrip("/").lower()


SAMPLES_BY_URL = {_key(sample.url): sample for sample in SAMPLES}


def find_sample(url):
    return SAMPLES_BY_URL.get(_key(url or ""))


def fetch_sample(url):
    """Answer a sample link from its bundled page, as `fetch_page` would; refuse anything else."""
    sample = find_sample(url)
    if sample is None:
        raise FetchError(REFUSED)
    if sample.blocked:
        raise FetchError(http_error_message(sample.status, urlsplit(sample.url).hostname))
    return FetchedPage(url=sample.url, content=(POSTINGS / sample.page).read_bytes(), charset=None)
