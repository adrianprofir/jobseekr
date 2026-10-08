from pathlib import Path

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.formats import date_format

from .models import (
    Application,
    Company,
    Document,
    Status,
    WorkMode,
    fill_blank_applied_date,
    normalize_company_name,
)

# Allowed document types and the leading bytes every genuine file of that type
# starts with. DOCX files are ZIP archives.
DOCUMENT_SIGNATURES = {
    ".pdf": b"%PDF-",
    ".docx": b"PK\x03\x04",
}


# The longest text a demo account may save in the fields that have no length
# limit otherwise. Self-hosted instances keep them unlimited.
DEMO_TEXT_LIMITS = {"notes": 5_000, "posting_text": 20_000, "message": 2_000}
UPLOAD_FIELDS = ["new_cv", "new_cv_label", "new_cover_letter", "new_cover_letter_label"]


def limit_demo_text(form):
    if not settings.DEMO_MODE:
        return
    for name, limit in DEMO_TEXT_LIMITS.items():
        if field := form.fields.get(name):
            field.max_length = limit
            field.validators.append(MaxLengthValidator(limit))
            field.widget.attrs["maxlength"] = str(limit)


def validate_document_file(upload):
    suffix = Path(upload.name).suffix.lower()
    if suffix not in DOCUMENT_SIGNATURES:
        raise ValidationError("Upload a PDF or DOCX file.")
    max_bytes = settings.DOCUMENT_MAX_UPLOAD_MB * 1024 * 1024
    if upload.size > max_bytes:
        raise ValidationError(f"The file is larger than {settings.DOCUMENT_MAX_UPLOAD_MB} MB.")
    upload.seek(0)
    head = upload.read(len(DOCUMENT_SIGNATURES[suffix]))
    upload.seek(0)
    if head != DOCUMENT_SIGNATURES[suffix]:
        raise ValidationError(f"This does not look like a valid {suffix[1:].upper()} file.")


def create_document(owner, kind, upload, label=""):
    filename = Path(upload.name)
    label_limit = Document._meta.get_field("label").max_length
    filename_limit = Document._meta.get_field("original_filename").max_length
    original_filename = filename.name
    if len(original_filename) > filename_limit:
        original_filename = filename.stem[: filename_limit - len(filename.suffix)] + filename.suffix
    return Document.objects.create(
        owner=owner,
        kind=kind,
        label=label.strip() or filename.stem[:label_limit],
        file=upload,
        original_filename=original_filename,
        size=upload.size,
    )


def document_choice_label(document):
    uploaded = date_format(timezone.localtime(document.uploaded_at), "j M Y")
    return f"{document.label} ({document.extension.upper()}, uploaded {uploaded})"


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs, format="%Y-%m-%d")


class CompanyNameField(forms.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("strip", False)
        super().__init__(**kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        if value in self.empty_values:
            return self.empty_value
        normalized = normalize_company_name(value)
        if not normalized:
            return self.empty_value
        return normalized


class ApplicationForm(forms.ModelForm):
    company_name = CompanyNameField(
        label="Company",
        max_length=200,
        widget=forms.TextInput(attrs={"list": "company-options", "autocomplete": "off"}),
        help_text="Pick an existing company or type a new name to create it.",
    )
    new_cv = forms.FileField(
        label="Upload a new CV",
        required=False,
        validators=[validate_document_file],
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.docx"}),
    )
    new_cv_label = forms.CharField(
        label="Name for the new CV",
        max_length=200,
        required=False,
        help_text="Optional. Defaults to the file name.",
    )
    new_cover_letter = forms.FileField(
        label="Upload a new cover letter",
        required=False,
        validators=[validate_document_file],
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.docx"}),
    )
    new_cover_letter_label = forms.CharField(
        label="Name for the new cover letter",
        max_length=200,
        required=False,
        help_text="Optional. Defaults to the file name.",
    )

    field_order = [
        "posting_url",
        "company_name",
        "role_title",
        "status",
        "date_applied",
        "location",
        "work_mode",
        "salary",
        "source",
        "contact_name",
        "contact_email",
        "next_follow_up",
        "cv",
        "new_cv",
        "new_cv_label",
        "cover_letter",
        "new_cover_letter",
        "new_cover_letter_label",
        "notes",
        "posting_text",
    ]

    class Meta:
        model = Application
        fields = [
            "posting_url",
            "role_title",
            "status",
            "date_applied",
            "location",
            "work_mode",
            "salary",
            "source",
            "contact_name",
            "contact_email",
            "next_follow_up",
            "cv",
            "cover_letter",
            "notes",
            "posting_text",
        ]
        widgets = {
            "posting_url": forms.URLInput(attrs={"placeholder": "https://", "autofocus": True}),
            "date_applied": DateInput(),
            "next_follow_up": DateInput(),
            "notes": forms.Textarea(attrs={"rows": 4}),
            "posting_text": forms.Textarea(attrs={"rows": 10}),
            "source": forms.TextInput(attrs={"list": "source-options"}),
        }

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        if self.instance.pk:
            self.fields["company_name"].initial = self.instance.company.name
        if self.instance.pk or self.initial.get("posting_url"):
            # The URL is already there; start typing where the work is.
            self.fields["posting_url"].widget.attrs.pop("autofocus", None)
            if not self.instance.pk:
                self.fields["company_name"].widget.attrs["autofocus"] = True
        self.fields["work_mode"].choices = [("", "Not specified"), *WorkMode.choices]
        self.fields["cv"].empty_label = "No CV / not recorded"
        self.fields["cover_letter"].empty_label = "No cover letter"
        for name in ("cv", "cover_letter"):
            field = self.fields[name]
            field.queryset = field.queryset.filter(owner=owner)
            field.label_from_instance = document_choice_label
        if not settings.DOCUMENT_UPLOADS_ENABLED:
            for name in UPLOAD_FIELDS:
                del self.fields[name]
        limit_demo_text(self)

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        current = cleaned.get("date_applied")
        filled = fill_blank_applied_date(status, current)
        if filled != current:
            cleaned["date_applied"] = filled
        if settings.DEMO_MODE:
            self._check_demo_room(cleaned.get("company_name"))
        return cleaned

    def _check_demo_room(self, company_name):
        """A demo account holds a bounded number of applications and companies."""
        limit = settings.SANDBOX_MAX_APPLICATIONS
        if not self.instance.pk and Application.objects.filter(owner=self.owner).count() >= limit:
            raise ValidationError(
                f"A demo account holds at most {limit} applications. Delete one to add another."
            )
        companies = Company.objects.filter(owner=self.owner)
        limit = settings.SANDBOX_MAX_COMPANIES
        if (
            company_name
            and not companies.filter(name__iexact=company_name).exists()
            and companies.count() >= limit
        ):
            self.add_error(
                "company_name",
                f"A demo account holds at most {limit} companies. Pick an existing company.",
            )

    @transaction.atomic
    def save(self, commit=True):
        application = super().save(commit=False)
        application.owner = self.owner
        name = self.cleaned_data["company_name"]
        companies = Company.objects.filter(owner=self.owner)
        company = companies.filter(name__iexact=name).first()
        if company is None:
            try:
                with transaction.atomic():
                    company = Company.objects.create(owner=self.owner, name=name)
            except IntegrityError:
                company = companies.filter(name__iexact=name).first()
                if company is None:
                    raise
        application.company = company
        if "posting_text" in self.changed_data:
            application.posting_text_saved_at = timezone.now() if application.posting_text else None
        if upload := self.cleaned_data.get("new_cv"):
            application.cv = create_document(
                self.owner, Document.Kind.CV, upload, self.cleaned_data.get("new_cv_label", "")
            )
        if upload := self.cleaned_data.get("new_cover_letter"):
            application.cover_letter = create_document(
                self.owner,
                Document.Kind.COVER_LETTER,
                upload,
                self.cleaned_data.get("new_cover_letter_label", ""),
            )
        if commit:
            application.save()
        return application


class StatusForm(forms.Form):
    status = forms.ChoiceField(choices=Status.choices)
    note = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.TextInput(attrs={"placeholder": "Optional note, e.g. 'Phone screen booked'"}),
    )


class NoteForm(forms.Form):
    message = forms.CharField(
        label="Note",
        widget=forms.Textarea(
            attrs={"rows": 2, "placeholder": "What happened? e.g. 'Sent a follow-up email'"}
        ),
    )
    next_follow_up = forms.DateField(
        label="Next follow-up",
        required=False,
        widget=DateInput(),
        help_text="Leave empty to keep the current follow-up date.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        limit_demo_text(self)


class CompanyForm(forms.ModelForm):
    name = CompanyNameField(max_length=200)

    class Meta:
        model = Company
        fields = ["name", "website", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        limit_demo_text(self)

    def clean_name(self):
        # Model validation skips the per-owner unique constraint because the
        # owner is not a form field, so check it here.
        name = self.cleaned_data["name"]
        others = Company.objects.filter(owner_id=self.instance.owner_id).exclude(
            pk=self.instance.pk
        )
        if others.filter(name__iexact=name).exists():
            raise ValidationError("A company with this name already exists.")
        return name


class DocumentForm(forms.Form):
    kind = forms.ChoiceField(choices=Document.Kind.choices)
    label = forms.CharField(
        max_length=200,
        required=False,
        help_text="Leave empty to use the file name.",
    )
    file = forms.FileField(
        validators=[validate_document_file],
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.docx"}),
    )

    def save(self, owner):
        return create_document(
            owner, self.cleaned_data["kind"], self.cleaned_data["file"], self.cleaned_data["label"]
        )


class SnoozeForm(forms.Form):
    CHOICES = [(1, "1 day"), (3, "3 days"), (7, "1 week"), (14, "2 weeks")]

    days = forms.TypedChoiceField(choices=CHOICES, coerce=int, initial=3)
