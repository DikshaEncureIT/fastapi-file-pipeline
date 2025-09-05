from fastapi import FastAPI
from fastapi.responses import JSONResponse
import pdfplumber
import os
import google.generativeai as genai

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

app = FastAPI(title="PDF Extractor + Gemini")

INPUT_FOLDER = "input"


# --- Step 1: Extract sections from PDF ---
def extract_sections_with_bold(pdf_path, start_page=1, end_page=None):
    sections = []
    with pdfplumber.open(pdf_path) as pdf:
        if end_page is None:
            end_page = len(pdf.pages)

        current_title = None
        current_content = []

        for i in range(start_page - 1, end_page):
            page = pdf.pages[i]
            words = page.extract_words(extra_attrs=["fontname", "size"])

            for w in words:
                text = w["text"].strip()
                font = w["fontname"].lower()
                size = w["size"]

                if "bold" in font or size >= 11:
                    if current_title:
                        sections.append({
                            "title": current_title,
                            "content": " ".join(current_content).strip()
                        })
                        current_content = []
                    current_title = text
                else:
                    current_content.append(text)

        if current_title:
            sections.append({
                "title": current_title,
                "content": " ".join(current_content).strip()
            })
    return sections


# --- Step 2: Clean JSON with Gemini ---
def refine_with_gemini(sections):
    prompt = f"""
    You are a data cleaning assistant.
    I will give you sections extracted from a PDF.
    Please return them as valid JSON with fields: "title" and "content".
    Ensure no broken sentences and keep text clean.

    Sections:
    {sections}
    """

    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(prompt)

    return response.text


@app.get("/")
def run_pipeline(start_page: int = 1, end_page: int = None):
    results = {}

    if not os.path.exists(INPUT_FOLDER):
        return JSONResponse(content={"error": "Input folder not found."}, status_code=404)

    pdf_files = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(".pdf")]
    if not pdf_files:
        return JSONResponse(content={"error": "No PDF files found in input folder."}, status_code=404)

    for pdf_file in pdf_files:
        pdf_path = os.path.join(INPUT_FOLDER, pdf_file)
        sections = extract_sections_with_bold(pdf_path, start_page, end_page)
        refined_json = refine_with_gemini(sections)
        results[pdf_file] = refined_json

    return JSONResponse(content=results)
