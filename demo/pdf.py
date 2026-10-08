"""A tiny PDF writer for the demo's sample documents: one A4 page of plain text.

The sample CVs and cover letter are generated rather than committed, so the
repo holds no binary documents and every sandbox gets its own fresh copy. Only
the standard Helvetica fonts are used, so nothing is embedded.
"""

import textwrap

PAGE_WIDTH, PAGE_HEIGHT = 595, 842  # A4 in points
MARGIN = 64
# (font resource, size, line height, wrap width in characters)
STYLES = {
    "title": ("F2", 20, 28, 48),
    "subtitle": ("F1", 10.5, 16, 90),
    "heading": ("F2", 12, 22, 70),
    "body": ("F1", 10.5, 15, 92),
    "gap": ("F1", 10.5, 8, 92),
}


def _escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content(lines):
    """The page's content stream: each (style, text) line wrapped and placed top to bottom."""
    out = ["BT"]
    y = PAGE_HEIGHT - MARGIN
    for style, text in lines:
        font, size, leading, width = STYLES[style]
        if style == "heading":
            y -= 6
        for part in textwrap.wrap(text, width) or [""]:
            y -= leading
            out.append(f"/{font} {size} Tf 1 0 0 1 {MARGIN} {y:.1f} Tm ({_escape(part)}) Tj")
    out.append("ET")
    return "\n".join(out).encode("cp1252")


def render(lines, title=""):
    """A one-page PDF of `lines`, a list of (style, text) pairs, as bytes."""
    content = _content(lines)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            "/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>"
        ).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Title (" + _escape(title).encode("cp1252") + b") /Producer (jobseekr demo) >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        len(objects),
        xref,
    )
    return bytes(pdf)
