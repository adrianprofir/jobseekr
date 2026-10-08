# Synthetic job postings

HTML used by the link-capture tests, so the tests never touch the network.
The demo (`CAPTURE_MODE=samples`) also serves a few of them as sample links; see `tracker/capture/samples.py`.

Every file here is written by hand.
None of them is a recording of a real job ad.
The companies, people, e-mail addresses and links are fictional (`.example` domains).
Each file imitates the markup that a job board or career-site system serves, so the extractor is tested against realistic page structure.

| File | Markup it imitates |
| --- | --- |
| `linkedin-dk.html` | LinkedIn guest job page on `dk.linkedin.com`: Danish page title, JSON-LD with an entity-escaped description, and the top card. |
| `linkedin-www.html` | The same job on `www.linkedin.com/jobs/view/<id>/`, served without JSON-LD, so the details come from the top card and the page title. |
| `jobindex.html` | Jobindex `vis-job` page for an ad hosted elsewhere (no JobPosting data, company in the toolbar). |
| `thehub.html` | The Hub job page (JSON-LD JobPosting, company in the page title). |
| `teamtailor.html` | A Teamtailor career site (JSON-LD JobPosting with several locations, empty `og:title`). |
| `emply.html` | An Emply career site (no structured data). |
| `indeed-synthetic.html` | Indeed's job page layout and title format, since Indeed blocks automated requests. |
| `indeed-blocked.html` | The kind of bot-check page a job board returns instead of the posting. |
| `career-page-opengraph.html` | A career page with only OpenGraph tags and a `<main>` element. |
| `not-a-posting.html` | A JavaScript-only page with nothing to extract. |
