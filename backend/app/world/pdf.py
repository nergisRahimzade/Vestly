"""Documents as PDFs: render text to a PDF page, optionally as a noisy "scan", and read the text back out.

A scan here is simulated in the text layer: letters are swapped the way OCR misreads them (o→0, l→1, rn→m...)
before the page is drawn, and the reader recovers the text with a real PDF parser. There is no image and no OCR;
a true image scan would need an OCR engine, which Vestly does not have.
"""

import base64
import io
import random
import re

PDF_PREFIX = "data:application/pdf;base64,"
# The way OCR misreads print: one character or pair for another.
MISREADS = (("o", "0"), ("l", "1"), ("m", "rn"), ("e", "c"), ("a", "o"), ("i", "l"), ("S", "5"))
WORD = re.compile(r"[A-Za-z]{4,}")


def scan_noise(lines: list[str], seed: int, rate: float = 0.25) -> list[str]:
    """Misread some letters in longer words. Digits, addresses and the label before a colon stay as printed."""
    rng = random.Random(seed)

    def noisy(match: re.Match) -> str:
        word = match.group(0)
        if rng.random() > rate:
            return word
        for printed, read in rng.sample(MISREADS, len(MISREADS)):
            if printed in word:
                return word.replace(printed, read, 1)
        return word

    out = []
    for line in lines:
        label, colon, rest = line.partition(":")
        if colon and "@" not in line and len(label) < 20:
            out.append(label + colon + WORD.sub(noisy, rest))
        elif "@" in line:
            out.append(line)
        else:
            out.append(WORD.sub(noisy, line))
    return out


def render_pdf(title: str, lines: list[str]) -> bytes:
    """One page of plain text, the way a provider export or a printed email looks."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    # invariant=1 keeps the file byte-for-byte the same for the same text, so its hash is stable.
    page = canvas.Canvas(buffer, pagesize=A4, invariant=1, pageCompression=0)
    page.setTitle(title)
    page.setFont("Courier", 10)
    y = A4[1] - 60
    for line in lines:
        if y < 50:
            page.showPage()
            page.setFont("Courier", 10)
            y = A4[1] - 60
        page.drawString(50, y, line)
        y -= 14
    page.save()
    return buffer.getvalue()


def pdf_text(data: bytes) -> str:
    """The text a PDF parser reads from the pages, one line per printed line."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def encode(data: bytes) -> str:
    return PDF_PREFIX + base64.b64encode(data).decode("ascii")


def decode(body: str) -> bytes:
    if not body.startswith(PDF_PREFIX):
        raise ValueError("Not a stored PDF.")
    return base64.b64decode(body[len(PDF_PREFIX):])


def is_pdf(body: str) -> bool:
    return body.startswith(PDF_PREFIX)


READ_FROM = re.compile(r"text read from PDF sha256:([0-9a-f]{64})$")


def source_pdf_hash(title: str) -> str | None:
    """The hash of the PDF a stored text was read from, as the loader names it in the text's title."""
    match = READ_FROM.search(title)
    return match.group(1) if match else None
