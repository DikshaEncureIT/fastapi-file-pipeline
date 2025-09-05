import re
import pdfplumber
import camelot
from pathlib import Path


# -------- TEXT EXTRACTION -------- #
def extract_text_from_pdf(filepath: str, start: int = None, end: int = None) -> str:
    text = ""
    with pdfplumber.open(filepath) as pdf:
        total_pages = len(pdf.pages)
        start_page = start if start else 1
        end_page = end if end else total_pages

        for i in range(start_page - 1, end_page):
            if i < total_pages:
                page_text = pdf.pages[i].extract_text()
                if page_text:
                    text += page_text + "\n"
    return text


# def parse_pdf_text(filepath: str, start: int = None, end: int = None):
#     text = extract_text_from_pdf(filepath, start, end)
#     pattern = re.compile(
#         r"(?P<title>\d+(\.\d+)*\s+[^\n]+)\n(?P<content>.*?)(?=\n\d+(\.\d+)*\s+|$)",
#         re.S,
#     )
#     sections = []
#     for match in pattern.finditer(text):
#         title = match.group("title").strip()
#         content = match.group("content").strip()
#         sections.append({"title": title, "content": content})
#     return sections

def parse_pdf_text(filepath: str, start: int = None, end: int = None):
    text = extract_text_from_pdf(filepath, start, end)

    pattern = re.compile(
        r"(?P<title>\d+(\.\d+)*\s+[^\n]+)\n(?P<content>.*?)(?=\n\d+(\.\d+)*\s+|$)",
        re.S,
    )

    sections = []
    for match in pattern.finditer(text):
        title = match.group("title").strip()
        content = match.group("content").strip()

        # 🚫 Skip items that look like table rows instead of real headings
        # Example: "1 The Sole Bidder must..." should be part of a table, not a section
        if re.match(r"^\d+\s", title) and len(title.split()) > 3:
            continue

        sections.append({"title": title, "content": content})

    return sections



# -------- TABLE EXTRACTION -------- #
def extract_tables_from_pdf(filepath: str, start: int = None, end: int = None):
    pages = f"{start}-{end}" if start and end else "all"

    results = []
    try:
        tables = camelot.read_pdf(filepath, pages=pages, flavor="lattice")
        for i, table in enumerate(tables):
            results.append({
                "table_no": i + 1,
                "data": table.df.to_dict(orient="records")
            })
    except Exception as e:
        print(f"⚠️ Camelot lattice error: {e}")

    return results


# -------- PIPELINE -------- #
def parse_input_folder(input_dir="input", start: int = None, end: int = None):
    results = {}
    for file in Path(input_dir).glob("*.pdf"):
        if file.is_file():
            results[file.name] = {
                "sections": parse_pdf_text(str(file), start, end),
                "tables": extract_tables_from_pdf(str(file), start, end),
            }
    return results
