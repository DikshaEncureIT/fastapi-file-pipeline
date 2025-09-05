import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from parser import parse_pdfs_to_json

app = FastAPI(title="PDF Section Extractor")

# Folders
INPUT_FOLDER = os.path.join(os.getcwd(), "input")
OUTPUT_FOLDER = os.path.join(os.getcwd(), "output")
os.makedirs(INPUT_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Root endpoint
@app.get("/")
def root():
    return {"status": "server running"}

# Extract sections from all PDFs in input folder
@app.get("/extract_sections_from_input/")
def extract_sections_from_input():
    # Check if input folder has PDFs
    pdf_files = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(".pdf")]
    if not pdf_files:
        return JSONResponse(content={"error": "No PDF files found in input folder"}, status_code=404)

    # Parse PDFs
    parsed_data = parse_pdfs_to_json(INPUT_FOLDER, OUTPUT_FOLDER)

    # Return full extracted data
    return JSONResponse(content={
        "message": "PDFs extracted successfully",
        "parsed_files_count": len(parsed_data),
        "data": parsed_data
    }, status_code=200)
