from fastapi import FastAPI
from extractor import parse_input_folder
from llm_refiner import refine_tables_with_gemini

app = FastAPI()


@app.get("/")
def root():
    return {"status": "server running"}


@app.get("/index")
def run_pipeline(use_llm: bool = False):
    raw_data = parse_input_folder("input", start=1, end=30)

    if use_llm:
        refined_data = {}
        for fname, content in raw_data.items():
            refined_data[fname] = {
                "sections": content["sections"],   # keep text raw
                "tables": refine_tables_with_gemini(content["tables"])  # only tables cleaned
            }
        return {"status": "success", "processed_pages": "8-12", "data": refined_data}

    return {"status": "success", "processed_pages": "8-12", "data": raw_data}
