import json

import pytest

from tracker.capture import normalize_posting_url
from tracker.capture.extract import MAX_DESCRIPTION_CHARS, extract_posting, html_to_text
from tracker.models import WorkMode

from .conftest import POSTINGS


def extract(fixture, url):
    return extract_posting((POSTINGS / fixture).read_bytes(), url)


@pytest.mark.parametrize(
    "fixture,url,role,company,location,source",
    [
        (
            "linkedin-dk.html",
            "https://dk.linkedin.com/jobs/view/senior-python-engineer-at-lindholm-analytics-1234567890",
            "Senior Python Engineer",
            "Lindholm Analytics",
            "Aarhus",
            "LinkedIn",
        ),
        (
            "linkedin-www.html",
            "https://www.linkedin.com/jobs/view/1234567890/",
            "Senior Python Engineer",
            "Lindholm Analytics",
            "Aarhus, Central Denmark Region, Denmark",
            "LinkedIn",
        ),
        (
            "jobindex.html",
            "https://www.jobindex.dk/vis-job/h1000001",
            "Junior Python Developer",
            "Tidewater Imaging ApS",
            "Roskilde",
            "Jobindex",
        ),
        (
            "thehub.html",
            "https://thehub.io/jobs/0123456789abcdef01234567",
            "Founding Backend Engineer",
            "Kestrel Sports",
            "Copenhagen",
            "The Hub",
        ),
        (
            "teamtailor.html",
            "https://career.harbourline.example/jobs/1000001-senior-data-engineer",
            "Senior Data Engineer",
            "Harbourline Systems",
            "Aalborg / København / Viby J",
            "",
        ),
        (
            "indeed-synthetic.html",
            "https://dk.indeed.com/viewjob?jk=0123456789abcdef",
            "Python-udvikler",
            "Fjordsoft ApS",
            "2100 København",
            "Indeed",
        ),
        (
            "career-page-opengraph.html",
            "https://nordlys.example/careers/platform-engineer",
            "Platform Engineer",
            "Nordlys Energy",
            "",
            "",
        ),
    ],
)
def test_recorded_postings(fixture, url, role, company, location, source):
    posting = extract(fixture, url)

    assert posting.role_title == role
    assert posting.company == company
    assert posting.location == location
    assert posting.source == source
    assert len(posting.description) > 40


def test_linkedin_description_is_unescaped_into_paragraphs_and_bullets():
    posting = extract("linkedin-dk.html", "https://dk.linkedin.com/jobs/view/1")

    assert posting.description.startswith("Are you a Senior Python Engineer")
    assert "&lt;" not in posting.description
    assert "<strong>" not in posting.description
    assert "\n- " in posting.description
    assert "\n\n\n" not in posting.description


def test_jobindex_ad_text_leaves_out_the_repeated_title_and_location():
    posting = extract("jobindex.html", "https://www.jobindex.dk/vis-job/h1000001")

    assert posting.description.startswith("In this role, you will primarily work")
    assert "See travel time" not in posting.description


def test_page_text_is_used_when_there_is_no_structured_description():
    posting = extract("career-page-opengraph.html", "https://nordlys.example/jobs/1")

    assert posting.description == (
        "Platform Engineer\n\n"
        "Join the team running our grid analytics platform.\n\n"
        "What you will do\n\n"
        "- Run Kubernetes clusters\n"
        "- Automate everything you can"
    )


def test_page_without_a_posting_is_empty():
    assert extract("not-a-posting.html", "https://app.example/jobs/1").is_empty()


def test_bot_check_page_is_empty():
    assert extract("indeed-blocked.html", "https://dk.indeed.com/viewjob?jk=1").is_empty()


def test_career_site_without_structured_data_still_gives_a_title_and_text():
    posting = extract("emply.html", "https://tidewaterimaging.career.emply.example/ad/x/0abc12/en")

    assert "Junior Python Developer" in posting.role_title
    assert "Tidewater Imaging is seeking a Junior Python Developer" in posting.description


def json_ld_page(data):
    return f"""<html><head><title>Ignored</title>
    <script type="application/ld+json">{json.dumps(data)}</script></head><body></body></html>"""


def test_json_ld_in_a_graph_with_salary_and_remote_work():
    page = json_ld_page(
        {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebSite", "name": "Acme careers"},
                {
                    "@type": ["JobPosting"],
                    "title": "Data &amp; ML Engineer",
                    "hiringOrganization": "Acme A/S",
                    "jobLocationType": "TELECOMMUTE",
                    "baseSalary": {
                        "@type": "MonetaryAmount",
                        "currency": "DKK",
                        "value": {
                            "@type": "QuantitativeValue",
                            "minValue": 50000,
                            "maxValue": 60000,
                            "unitText": "MONTH",
                        },
                    },
                    "description": "<p>Build models.</p>",
                },
            ],
        }
    )
    posting = extract_posting(page, "https://acme.example/jobs/1")

    assert posting.role_title == "Data & ML Engineer"
    assert posting.company == "Acme A/S"
    assert posting.work_mode == WorkMode.REMOTE
    assert posting.salary == "50,000-60,000 DKK per month"
    assert posting.description == "Build models."


def test_broken_json_ld_falls_back_to_page_metadata():
    page = """<html><head><title>Backend Engineer | Globex</title>
    <script type="application/ld+json">{"@type": "JobPosting", broken</script></head>
    <body><main><p>About the job.</p></main></body></html>"""
    posting = extract_posting(page, "https://globex.example/jobs/1")

    assert posting.role_title == "Backend Engineer"
    assert posting.company == "Globex"
    assert posting.description == "About the job."


def test_long_fields_are_shortened():
    page = json_ld_page({"@type": "JobPosting", "title": "x" * 500, "description": "y " * 40_000})
    posting = extract_posting(page, "https://acme.example/jobs/1")

    assert len(posting.role_title) == 200
    assert len(posting.description) <= MAX_DESCRIPTION_CHARS + 2


def test_deeply_nested_html_does_not_crash():
    page = "<div>" * 5000 + "deep text" + "</div>" * 5000
    assert extract_posting(f"<html><body><main>{page}</main></body></html>", "https://x.example/")


def test_html_to_text_keeps_structure():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        "<h2>Role</h2><p>One<br>Two</p><ul><li>A</li><li></li><li>B</li></ul>"
        "<script>ignored()</script><!-- note -->",
        "html.parser",
    )
    assert html_to_text(soup) == "Role\n\nOne\nTwo\n\n- A\n- B"


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=1234567890",
            "https://www.linkedin.com/jobs/view/1234567890/",
        ),
        (
            "https://www.linkedin.com/jobs/search/?currentJobId=123&keywords=python",
            "https://www.linkedin.com/jobs/view/123/",
        ),
        (
            "https://dk.linkedin.com/jobs/view/backend-at-acme-123?refId=abc&trackingId=x",
            "https://dk.linkedin.com/jobs/view/backend-at-acme-123",
        ),
        (
            "https://dk.indeed.com/jobs?q=python&vjk=0123456789abcdef",
            "https://dk.indeed.com/viewjob?jk=0123456789abcdef",
        ),
        ("https://acme.example/jobs/1?utm_source=x", "https://acme.example/jobs/1?utm_source=x"),
    ],
)
def test_normalize_posting_url(url, expected):
    assert normalize_posting_url(url) == expected
