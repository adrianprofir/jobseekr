# Contributing to jobseekr

Thanks for helping.
jobseekr is a small, boring Django app on purpose, so the best contributions are focused and easy to review.
For anything bigger than a bug fix, please open an issue first to agree on the approach.

By contributing you agree that your contribution is released under the [MIT licence](LICENSE).

## Development setup

You need [uv](https://docs.astral.sh/uv/) and a PostgreSQL server.
The quickest way to get a throwaway database is Docker:

```sh
docker run -d --name jobseekr-dev-db \
  -e POSTGRES_USER=jobtracker -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=jobtracker \
  -p 127.0.0.1:5432:5432 postgres:18-alpine
```

Create a local `.env` and switch it to development mode:

```sh
cp .env.example .env
# then edit .env:
#   DJANGO_DEBUG=true
#   POSTGRES_PASSWORD=dev
```

Install dependencies, create the schema and an account, and start the dev server:

```sh
uv sync
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py createsuperuser
uv run --env-file .env python manage.py runserver
```

Open http://127.0.0.1:8000 and log in.
Uploaded documents go to `private-media/`, which is git-ignored.

## The checks CI runs

Run these before you open a pull request:

```sh
uv run ruff check .
uv run ruff format --check .
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py makemigrations --check --dry-run
uv run --env-file .env pytest
```

The tests need the same PostgreSQL server; Django creates and drops a separate test database.
CI also builds the Docker image, starts the Compose stack and smoke-tests it, and scans the repository for secrets with gitleaks.

## Guidelines

- Test behaviour the way a user meets it: through the views and forms, not by reaching into internals. For a bug fix, add a test that fails without the fix.
- Keep it simple. Do not add an abstraction, dependency or setting without a concrete need.
- Changes that add a model field need a migration, and `makemigrations --check` must stay clean.
- Settings come from environment variables. Add new ones to `.env.example` and to the configuration table in the README.
- Update the documentation when behaviour changes. Write Markdown with one sentence per line, so diffs and reviews stay readable.
- Never commit secrets, real personal data, real addresses or real server details. Use `.example` domains, the RFC 5737 documentation addresses (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and fictional names.

## Adding support for a job board

Link capture reads a pasted posting URL and pre-fills the form.
Most boards already work through their schema.org `JobPosting` data or page metadata.
Add a board-specific parser only when those do not give the role, company or location.

1. **Write a synthetic fixture.** Add a small HTML file to `tracker/capture/postings/`, modelled on the board's real markup: the same element structure, class names, `<title>` format and metadata, but with invented content. Use a fictional company (for example "Nordlys Energy" or "Fjordsoft ApS"), invented roles and `.example` domains.
2. **Add the parser.** In `tracker/capture/extract.py`, write `from_<board>(soup)` that returns a `Posting` with the fields the board's layout gives. Then register the board's domain in `BOARDS` with the name used as the application's source. Country sites (such as `dk.<board>.com`) are matched automatically.
3. **Test it.** Add a row to the parametrized `test_recorded_postings` in `tracker/tests/test_capture_extract.py` with the fixture, a posting URL on the board's domain, and the expected role, company, location and source. Add a focused test for anything unusual, such as how the ad text is cleaned.
4. **Document it.** Add the fixture to `tracker/capture/postings/README.md`.

### Fixture rules

Fixtures are public, so they must never contain a real posting.
Some of them are also served to visitors of the public demo as sample postings (`tracker/capture/samples.py`).

- Do not save a real job ad, even trimmed. Re-create the structure with invented text instead.
- No real people, e-mail addresses, phone numbers or company logos.
- No real employers' names. Do not use the name of a real company as the hiring company.
- No tracking identifiers, session tokens or cookies from a recorded page.

### Network rules for capture

Everything under `tracker/capture/fetch.py` guards the server against being turned into a way to reach private networks.
Do not weaken it, and add a test for every change.
Tests must never touch the network: use the fixtures and the `serve_posting` helper in `tracker/tests/conftest.py`.

## Pull requests

- Keep a pull request to one change, and describe what it does and why.
- Do not put real addresses, host names, server paths or screenshots of real data in the description.
- Make sure the checks above pass.

## Reporting bugs and vulnerabilities

Use the issue templates for bugs and feature requests.
Report security problems privately; see [SECURITY.md](SECURITY.md).
