import re
import pdfplumber
from pathlib import Path


def extract_text_from_pdf(filepath: str) -> str:
    """Extracts full text from a PDF using pdfplumber."""
    text = ""
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def parse_pdf(filepath: str):
    """Parses a PDF into sections with title + content."""
    text = extract_text_from_pdf(filepath)

    # Regex for section headers like "1.1 Something", "1.2 Title", etc.
    pattern = re.compile(
        r"(?P<title>\d+(\.\d+)*\s+[^\n]+)\n(?P<content>.*?)(?=\n\d+(\.\d+)*\s+|$)",
        re.S,
    )

    sections = []
    for match in pattern.finditer(text):
        title = match.group("title").strip()
        content = match.group("content").strip()
        sections.append({"title": title, "content": content})

    return sections


def parse_input_folder(input_dir="input"):
    results = {}
    for file in Path(input_dir).glob("*.pdf"):
        if file.is_file():
            results[file.name] = parse_pdf(str(file))
    return results
