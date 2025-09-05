# main.py
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import os
import PyPDF2
import camelot
import re

app = FastAPI(title="PDF File Pipeline")

INPUT_FOLDER = "input"

# Function to extract text from PDF
def extract_text(pdf_path, start_page=8, end_page=12):
    pdf_file = open(pdf_path, "rb")
    reader = PyPDF2.PdfReader(pdf_file)
    if end_page is None:
        end_page = len(reader.pages)
    text_data = []
    
    for i in range(start_page - 1, end_page):
        page = reader.pages[i]
        text_data.append(page.extract_text())
    
    pdf_file.close()
    return "\n".join(text_data)

# Function to extract tables from PDF using Camelot
def extract_tables(pdf_path, start_page=1, end_page=None):
    tables_list = []
    pdf_reader = PyPDF2.PdfReader(open(pdf_path, "rb"))
    if end_page is None:
        end_page = len(pdf_reader.pages)
    for page in range(start_page, end_page + 1):
        tables = camelot.read_pdf(pdf_path, pages=str(page))
        for table in tables:
            tables_list.append(table.df.to_dict(orient="records"))
    return tables_list

# Function to split text into sections
def split_sections(raw_text):
    sections = []
    pattern = r"(\d+\.\d+ [A-Za-z ]+)"  # matches titles like "1.1 Project Background"
    matches = re.split(pattern, raw_text)

    for i in range(1, len(matches), 2):
        title = matches[i].strip()
        content = matches[i + 1].strip() if (i + 1) < len(matches) else ""
        sections.append({
            "title": title,
            "content": content
        })
    return sections

@app.get("/")
def run_pipeline():
    results = {}
    
    # Check input folder
    if not os.path.exists(INPUT_FOLDER):
        return JSONResponse(content={"error": "Input folder not found."}, status_code=404)
    
    pdf_files = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(".pdf")]
    
    if not pdf_files:
        return JSONResponse(content={"error": "No PDF files found in input folder."}, status_code=404)
    
    # Process each PDF
    for pdf_file in pdf_files:
        pdf_path = os.path.join(INPUT_FOLDER, pdf_file)
        raw_text = extract_text(pdf_path)
        sections = split_sections(raw_text)
        tables = extract_tables(pdf_path)
        
        results[pdf_file] = {
            "sections": sections,
            "tables": tables
        }
    
    return JSONResponse(content=results)
