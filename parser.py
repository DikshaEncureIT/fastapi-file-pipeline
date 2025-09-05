import os
import re
import json
import fitz  # PyMuPDF

def extract_sections(text):
    """
    Extract sections using regex pattern like '1.2 Project Objective'
    """
    section_pattern = re.compile(
        r'(^|\n)(\d+(\.\d+)*\s+[A-Za-z].*?)\n', re.MULTILINE)

    matches = list(section_pattern.finditer(text))
    sections = []

    for i, match in enumerate(matches):
        title = match.group(2).strip()
        content_start = match.end()
        content_end = matches[i+1].start() if (i+1) < len(matches) else len(text)
        content = text[content_start:content_end].strip()
        content = re.sub(r'\n+', '\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        sections.append({
            "section_title": title,
            "section_content": content
        })

    return sections

def extract_pdf_text(pdf_path):
    """
    Extract text from a PDF file using PyMuPDF
    """
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    return full_text

def parse_pdfs_to_json(input_folder, output_folder):
    """
    Read all PDFs in input_folder, parse sections, save JSON in output_folder
    """
    os.makedirs(output_folder, exist_ok=True)
    all_data = {}

    for file in os.listdir(input_folder):
        if file.lower().endswith(".pdf"):
            pdf_path = os.path.join(input_folder, file)
            print(f"Processing: {file}")
            text = extract_pdf_text(pdf_path)
            sections = extract_sections(text)
            all_data[file] = sections

            # Save per-PDF JSON
            output_path = os.path.join(output_folder, f"{os.path.splitext(file)[0]}.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(sections, f, indent=4, ensure_ascii=False)

    return all_data
