"""The seed bundle for a demo sandbox: a fictional job search, dated relative to today.

Kim Sandvik, a fictional backend developer, has applied to fifteen roles at
fictional companies, with every status, a history for each application, and
follow-ups that are overdue, due today, gone quiet and coming up, so every page
of the tracker has something to show on the first visit. Contacts use
`.example` addresses. The three documents are generated PDFs (demo/pdf.py).
"""

from datetime import datetime, time, timedelta

from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import slugify

from tracker.models import Application, ApplicationEvent, Company, Document, Status

from . import pdf

PERSON = "Kim Sandvik"
EMAIL = "kim.sandvik@mail.example"

COMPANIES = {
    "Nordlys Energy": "Renewable energy trading platform.",
    "Fjordsoft ApS": "Accounting software for small businesses.",
    "Havbro Logistics": "Freight planning for ports and warehouses.",
    "Granlund Health": "Patient booking and records for clinics.",
    "Solvind Robotics": "Warehouse robots and their fleet software.",
    "Tidevand Bank": "",
    "Mosegaard Games": "Small studio making co-op puzzle games.",
    "Lysholt Software": "",
    "Brinkhus Insurance": "",
    "Kobbervej Studio": "Design and development agency.",
    "Ravnsborg Retail": "",
    "Aalund Maps": "Open map data for municipalities.",
}

CV_BACKEND = "cv-backend"
CV_DATA = "cv-data"
LETTER = "letter-nordlys"

AD = (
    "{company} is looking for a {role} to join a small product team.\n\n"
    "What you will do:\n"
    "- Build and run the services behind our product, mostly in Python.\n"
    "- Work closely with design and support to ship small changes often.\n"
    "- Help keep the platform fast, observable and easy to change.\n\n"
    "What we hope you bring:\n"
    "- A few years of experience with Python and PostgreSQL.\n"
    "- Care for tests, code review and clear writing.\n\n"
    "Apply with a CV and a few lines about why the role interests you."
)

# Each application: its fields, then its history as (days ago, kind, from, to, message).
# Days are counted back from today; follow-ups are relative to today too.
APPLICATIONS = [
    {
        "company": "Nordlys Energy",
        "role_title": "Backend Developer (Python)",
        "status": Status.INTERVIEWING,
        "applied": 12,
        "follow_up": 0,
        "location": "Copenhagen",
        "work_mode": "hybrid",
        "salary": "650-700k DKK + pension",
        "source": "LinkedIn",
        "contact": ("Mette Holm", "mette.holm@nordlys-energy.example"),
        "cv": CV_BACKEND,
        "letter": LETTER,
        "ad": True,
        "notes": "Team of six; they deploy several times a day.",
        "history": [
            (5, "status", "applied", "interviewing", "Phone screen booked for Thursday."),
            (
                2,
                "note",
                "",
                "",
                "Technical interview with two team leads. Asked about query "
                "performance and how I review code. Promised an answer within a week.",
            ),
        ],
    },
    {
        "company": "Fjordsoft ApS",
        "role_title": "Python Developer",
        "status": Status.APPLIED,
        "applied": 9,
        "follow_up": -3,
        "location": "Aarhus",
        "work_mode": "onsite",
        "source": "Indeed",
        "contact": ("Jonas Krog", "jonas.krog@fjordsoft.example"),
        "cv": CV_BACKEND,
        "ad": True,
        "history": [],
    },
    {
        "company": "Havbro Logistics",
        "role_title": "Platform Engineer",
        "status": Status.APPLIED,
        "applied": 31,
        "location": "Esbjerg",
        "work_mode": "remote",
        "source": "Jobindex",
        "contact": ("Sara Lund", "sara.lund@havbro-logistics.example"),
        "cv": CV_DATA,
        "history": [
            (20, "note", "", "", "Recruiter said they would come back after the holidays."),
        ],
    },
    {
        "company": "Granlund Health",
        "role_title": "Data Engineer",
        "status": Status.INTERVIEWING,
        "applied": 21,
        "follow_up": 2,
        "location": "Odense",
        "work_mode": "hybrid",
        "source": "Company website",
        "contact": ("Ali Navid", "ali.navid@granlund-health.example"),
        "cv": CV_DATA,
        "ad": True,
        "history": [
            (8, "status", "applied", "interviewing", "First interview with the data team."),
            (1, "note", "", "", "Second round booked with the CTO."),
        ],
    },
    {
        "company": "Solvind Robotics",
        "role_title": "Senior Backend Engineer",
        "status": Status.OFFER,
        "applied": 35,
        "follow_up": 5,
        "location": "Copenhagen",
        "work_mode": "hybrid",
        "salary": "720k DKK + pension",
        "source": "Referral",
        "contact": ("Emil Bach", "emil.bach@solvind-robotics.example"),
        "cv": CV_BACKEND,
        "ad": True,
        "notes": "Referred by a former colleague.",
        "history": [
            (26, "status", "applied", "interviewing", "Intro call with the hiring manager."),
            (17, "note", "", "", "Take-home task sent in."),
            (3, "status", "interviewing", "offer", "Offer received. Answer by Friday."),
        ],
    },
    {
        "company": "Tidevand Bank",
        "role_title": "Software Engineer, Payments",
        "status": Status.REJECTED,
        "applied": 40,
        "location": "Copenhagen",
        "work_mode": "onsite",
        "source": "LinkedIn",
        "cv": CV_BACKEND,
        "history": [
            (30, "status", "applied", "interviewing", "Phone screen."),
            (6, "status", "interviewing", "rejected", "They chose an internal candidate."),
        ],
    },
    {
        "company": "Mosegaard Games",
        "role_title": "Backend Developer",
        "status": Status.REJECTED,
        "applied": 18,
        "location": "Aalborg",
        "work_mode": "remote",
        "source": "The Hub",
        "cv": CV_BACKEND,
        "ad": True,
        "history": [
            (4, "status", "applied", "rejected", "Short rejection e-mail, no feedback."),
        ],
    },
    {
        "company": "Lysholt Software",
        "role_title": "Django Developer",
        "status": Status.WITHDRAWN,
        "applied": 28,
        "location": "Roskilde",
        "source": "Recruiter",
        "contact": ("Nora Vang", "nora.vang@recruiting.example"),
        "cv": CV_BACKEND,
        "history": [
            (10, "status", "applied", "withdrawn", "Withdrew: the role is mostly on-call support."),
        ],
    },
    {
        "company": "Brinkhus Insurance",
        "role_title": "Python Developer",
        "status": Status.NO_RESPONSE,
        "applied": 60,
        "location": "Aarhus",
        "source": "Jobindex",
        "cv": CV_BACKEND,
        "history": [
            (15, "status", "applied", "no_response", "No answer after six weeks."),
        ],
    },
    {
        "company": "Kobbervej Studio",
        "role_title": "Full-stack Developer",
        "status": Status.APPLIED,
        "applied": 26,
        "location": "Copenhagen",
        "work_mode": "hybrid",
        "source": "Company website",
        "cv": CV_BACKEND,
        "history": [],
    },
    {
        "company": "Ravnsborg Retail",
        "role_title": "Backend Engineer",
        "status": Status.WISHLIST,
        "created": 6,
        "location": "Copenhagen",
        "source": "LinkedIn",
        "notes": "Ask Sara about the team before applying.",
        "ad": True,
        "history": [],
    },
    {
        "company": "Aalund Maps",
        "role_title": "GIS Developer",
        "status": Status.WISHLIST,
        "created": 3,
        "follow_up": 6,
        "location": "Remote (Denmark)",
        "work_mode": "remote",
        "source": "The Hub",
        "notes": "Application deadline is the follow-up date.",
        "history": [],
    },
    {
        "company": "Fjordsoft ApS",
        "role_title": "Data Platform Engineer",
        "status": Status.APPLIED,
        "applied": 2,
        "follow_up": 9,
        "location": "Aarhus",
        "work_mode": "hybrid",
        "source": "LinkedIn",
        "cv": CV_DATA,
        "ad": True,
        "history": [],
    },
    {
        "company": "Nordlys Energy",
        "role_title": "Site Reliability Engineer",
        "status": Status.APPLIED,
        "applied": 1,
        "follow_up": 10,
        "location": "Copenhagen",
        "work_mode": "hybrid",
        "source": "Company website",
        "contact": ("Mette Holm", "mette.holm@nordlys-energy.example"),
        "cv": CV_BACKEND,
        "history": [],
    },
    {
        "company": "Havbro Logistics",
        "role_title": "Integration Developer",
        "status": Status.APPLIED,
        "applied": 4,
        "follow_up": -1,
        "location": "Esbjerg",
        "work_mode": "remote",
        "source": "Jobindex",
        "contact": ("Sara Lund", "sara.lund@havbro-logistics.example"),
        "cv": CV_BACKEND,
        "history": [],
    },
]

DOCUMENTS = {
    CV_BACKEND: {
        "kind": Document.Kind.CV,
        "label": "CV - backend focus",
        "filename": "Kim Sandvik CV backend.pdf",
        "uploaded": 45,
        "lines": [
            ("title", PERSON),
            ("subtitle", f"Backend developer · Copenhagen · {EMAIL}"),
            ("heading", "Profile"),
            (
                "body",
                "Backend developer with six years of Python and PostgreSQL, most of them "
                "building and running web services for small product teams. I like simple "
                "systems, good tests and code that the next person can change with confidence.",
            ),
            ("heading", "Experience"),
            ("body", "Backend Developer, Example Freight A/S (fictional), 2022 - present"),
            ("body", "- Moved order tracking from nightly batch jobs to an event-driven service."),
            ("body", "- Cut the slowest page from four seconds to under one with query tuning."),
            ("gap", ""),
            ("body", "Developer, Sample Studio ApS (fictional), 2019 - 2022"),
            ("body", "- Built booking and payment flows in Django for twenty client sites."),
            ("body", "- Introduced CI with tests and linting for every project."),
            ("heading", "Skills"),
            ("body", "Python, Django, FastAPI, PostgreSQL, Docker, Linux, CI/CD, observability."),
            ("heading", "Education"),
            ("body", "BSc in Software Engineering, Example University (fictional), 2019"),
            ("gap", ""),
            ("subtitle", "A sample document for the jobseekr demo. The person is fictional."),
        ],
    },
    CV_DATA: {
        "kind": Document.Kind.CV,
        "label": "CV - data engineering",
        "filename": "Kim Sandvik CV data.pdf",
        "uploaded": 32,
        "lines": [
            ("title", PERSON),
            ("subtitle", f"Backend and data engineer · Copenhagen · {EMAIL}"),
            ("heading", "Profile"),
            (
                "body",
                "Python developer who has spent the last three years on data pipelines: "
                "getting data in reliably, keeping it correct, and making it easy to query.",
            ),
            ("heading", "Experience"),
            ("body", "Backend Developer, Example Freight A/S (fictional), 2022 - present"),
            ("body", "- Built the ingestion pipeline for 40 million shipment events a month."),
            ("body", "- Added data quality checks that page the team before customers notice."),
            ("gap", ""),
            ("body", "Developer, Sample Studio ApS (fictional), 2019 - 2022"),
            ("body", "- Reporting database and dashboards for client sales data."),
            ("heading", "Skills"),
            ("body", "Python, SQL, PostgreSQL, dbt, Airflow, Docker, data modelling, testing."),
            ("heading", "Education"),
            ("body", "BSc in Software Engineering, Example University (fictional), 2019"),
            ("gap", ""),
            ("subtitle", "A sample document for the jobseekr demo. The person is fictional."),
        ],
    },
    LETTER: {
        "kind": Document.Kind.COVER_LETTER,
        "label": "Cover letter - Nordlys Energy",
        "filename": "Kim Sandvik cover letter Nordlys Energy.pdf",
        "uploaded": 12,
        "lines": [
            ("title", "Application: Backend Developer (Python)"),
            ("subtitle", f"{PERSON} · {EMAIL}"),
            ("gap", ""),
            ("body", "Dear Mette Holm,"),
            ("gap", ""),
            (
                "body",
                "I am applying for the Backend Developer role at Nordlys Energy. Your team "
                "ships small changes often and cares about the platform being easy to change, "
                "which is exactly how I like to work.",
            ),
            ("gap", ""),
            (
                "body",
                "At my current job I moved order tracking from nightly batch jobs to an "
                "event-driven service and made our slowest page four times faster. I would like "
                "to bring the same care to the services behind your trading platform.",
            ),
            ("gap", ""),
            ("body", "I would be glad to tell you more in an interview."),
            ("gap", ""),
            ("body", "Kind regards,"),
            ("body", PERSON),
            ("gap", ""),
            ("subtitle", "A sample document for the jobseekr demo. The person is fictional."),
        ],
    },
}


def _at(today, days_ago, hour=10):
    """A moment `days_ago` days before today, during working hours."""
    moment = datetime.combine(today - timedelta(days=days_ago), time(hour, 15))
    return timezone.make_aware(moment)


def seed(user, today=None, stored_files=None):
    """Fill `user`'s empty account with the bundle. Stored file names go to `stored_files`."""
    today = today or timezone.localdate()
    companies = {
        name: Company.objects.create(
            owner=user,
            name=name,
            website=f"https://{slugify(name.removesuffix(' ApS'))}.example",
            notes=notes,
        )
        for name, notes in COMPANIES.items()
    }

    documents = {}
    for key, spec in DOCUMENTS.items():
        content = pdf.render(spec["lines"], title=spec["label"])
        document = Document.objects.create(
            owner=user,
            kind=spec["kind"],
            label=spec["label"],
            file=ContentFile(content, name=spec["filename"]),
            original_filename=spec["filename"],
            size=len(content),
        )
        if stored_files is not None:
            stored_files.append(document.file.name)
        Document.objects.filter(pk=document.pk).update(uploaded_at=_at(today, spec["uploaded"]))
        documents[key] = document

    for spec in APPLICATIONS:
        _seed_application(user, spec, companies, documents, today)


def _seed_application(user, spec, companies, documents, today):
    company = companies[spec["company"]]
    applied = spec.get("applied")
    started = applied if applied is not None else spec["created"]
    contact_name, contact_email = spec.get("contact", ("", ""))
    follow_up = spec.get("follow_up")
    application = Application.objects.create(
        owner=user,
        company=company,
        role_title=spec["role_title"],
        posting_url=f"{company.website}/jobs/{slugify(spec['role_title'])}",
        location=spec.get("location", ""),
        work_mode=spec.get("work_mode", ""),
        status=spec["status"],
        date_applied=today - timedelta(days=applied) if applied is not None else None,
        salary=spec.get("salary", ""),
        contact_name=contact_name,
        contact_email=contact_email,
        source=spec.get("source", ""),
        next_follow_up=today + timedelta(days=follow_up) if follow_up is not None else None,
        cv=documents.get(spec.get("cv")),
        cover_letter=documents.get(spec.get("letter")),
        notes=spec.get("notes", ""),
        posting_text=AD.format(company=company.name, role=spec["role_title"])
        if spec.get("ad")
        else "",
    )
    created_at = _at(today, started, hour=9)
    first_status = spec["history"][0][2] if spec["history"] and spec["history"][0][2] else None
    initial = first_status or spec["status"]
    events = [
        ApplicationEvent(
            application=application,
            kind=ApplicationEvent.Kind.CREATED,
            to_status=initial,
            message=f"Added as {Status(initial).label.lower()}.",
            created_at=created_at,
        )
    ]
    for days_ago, kind, from_status, to_status, message in spec["history"]:
        events.append(
            ApplicationEvent(
                application=application,
                kind=kind,
                from_status=from_status,
                to_status=to_status,
                message=message,
                created_at=_at(today, days_ago, hour=14),
            )
        )
    ApplicationEvent.objects.bulk_create(events)
    Application.objects.filter(pk=application.pk).update(
        created_at=created_at,
        updated_at=events[-1].created_at,
        posting_text_saved_at=created_at if application.posting_text else None,
    )
