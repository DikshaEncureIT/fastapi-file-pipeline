import re
from pathlib import Path
import pdfplumber
import camelot
from fastapi import FastAPI

app = FastAPI()


# ------------------ TEXT EXTRACTION ------------------ #
def extract_text_from_pdf(filepath: str, start: int = None, end: int = None) -> str:
    """Extracts text from a PDF using pdfplumber (with optional page range)."""
    text = ""
    with pdfplumber.open(filepath) as pdf:
        total_pages = len(pdf.pages)

        # Default: all pages
        start_page = start if start else 1
        end_page = end if end else total_pages

        for i in range(start_page - 1, end_page):
            if i < total_pages:
                page_text = pdf.pages[i].extract_text()
                if page_text:
                    text += page_text + "\n"
    return text


def parse_pdf_text(filepath: str, start: int = None, end: int = None):
    """Parses text into sections with title + content."""
    text = extract_text_from_pdf(filepath, start, end)

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


# ------------------ TABLE EXTRACTION ------------------ #
def extract_tables_from_pdf(filepath: str, start: int = None, end: int = None):
    """Extract tables from PDF using Camelot."""
    pages = f"{start}-{end}" if start and end else "all"
    tables = camelot.read_pdf(filepath, pages=pages)

    extracted_tables = []
    for i, table in enumerate(tables):
        extracted_tables.append({
            "table_no": i + 1,
            "data": table.df.to_dict(orient="records")  # JSON table
        })
    return extracted_tables


# ------------------ PIPELINE ------------------ #
def parse_input_folder(input_dir="input", start: int = None, end: int = None):
    results = {}
    for file in Path(input_dir).glob("*.pdf"):
        if file.is_file():
            results[file.name] = {
                "sections": parse_pdf_text(str(file), start, end),
                "tables": extract_tables_from_pdf(str(file), start, end)
            }
    return results


@app.get("/")
def root():
    return {"status": "server running"}


@app.get("/index")
def run_pipeline():
    # 👇 fixed page range (8–16)
    parsed_data = parse_input_folder("input", start=8, end=11)
    return {
        "status": "success",
        "processed_pages": "8-11",
        "data": parsed_data
    }
