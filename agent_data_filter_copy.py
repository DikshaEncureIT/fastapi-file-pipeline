from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import os
import json
import re
import requests

app = FastAPI()

# Paths to your data files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "output", "unique_titles_template.json")
MERGED_PATH = os.path.join(BASE_DIR, "output", "merged_data.json")

# Load template keys and merged content
with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    template_json = json.load(f)
    TEMPLATE_KEYS = set(template_json.get("template", {}).keys())

with open(MERGED_PATH, "r", encoding="utf-8") as f:
    merged_json = json.load(f)
    MERGED_CONTENT = merged_json.get("combined_content", [])

# Build normalized lookup for section titles for quick search
section_lookup = {}
for item in MERGED_CONTENT:
    if item.get("type") == "section" and "title" in item:
        norm_title = re.sub(r"\s+", "_", item["title"].strip().lower())
        section_lookup[norm_title] = item

class SectionResponse(BaseModel):
    template_key: str
    matched_title: str = ""
    content: str = ""
    llm_used: bool = False

def normalize_key(key: str) -> str:
    return key.strip().lower()

def search_content_for_key(key: str, cohere_api_key=None):
    query = key.replace("_", " ").strip().lower()
    # Try exact match
    for norm_title, section in section_lookup.items():
        if query == section["title"].strip().lower():
            return section["title"], section.get("text", ""), False
    # Try substring match
    for norm_title, section in section_lookup.items():
        if query in section["title"].strip().lower():
            return section["title"], section.get("text", ""), False
    # Fallback to Cohere LLM if key not found and API key present
    if cohere_api_key:
        context = "\n\n".join(
            f"Title: {sec['title']}\nContent: {sec.get('text','')}"
            for sec in MERGED_CONTENT if sec.get("type") == "section"
        )
        prompt = (
            f"Extract relevant information about '{query}' from the following text. "
            "If none found, reply 'Not found.'\n\n{context}"
        )
        headers = {"Authorization": f"Bearer {cohere_api_key}"}
        data = {
            "model": "command-r",
            "prompt": prompt,
            "max_tokens": 350,
            "temperature": 0.1,
            "truncate": "END",
        }
        resp = requests.post("https://api.cohere.ai/v1/generate", headers=headers, json=data)
        if resp.ok:
            text = resp.json().get("generations", [{}])[0].get("text", "").strip()
            if text.lower() != "not found.":
                return "(LLM extracted)", text, True
    # No suitable content found
    return "", "", False

@app.get("/template_keys", response_model=List[str])
def get_template_keys():
    # List all available template keys
    return sorted(TEMPLATE_KEYS)

@app.get("/section/{template_key}", response_model=SectionResponse)
def get_section(template_key: str):
    norm_key = normalize_key(template_key)
    matched_key = None
    for k in TEMPLATE_KEYS:
        if normalize_key(k) == norm_key:
            matched_key = k
            break
    if not matched_key:
        raise HTTPException(status_code=404, detail="Template key not found")

    title, content, llm_used = search_content_for_key(matched_key, cohere_api_key=os.getenv("COHERE_API_KEY"))
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    return SectionResponse(template_key=matched_key, matched_title=title, content=content, llm_used=llm_used)

@app.post("/sections", response_model=List[SectionResponse])
def get_all_sections():
    # Process all keys automatically without request body input
    results = []
    for key in sorted(TEMPLATE_KEYS):
        title, content, llm_used = search_content_for_key(key, cohere_api_key=os.getenv("COHERE_API_KEY"))
        if not content:
            content = "Content not found"
        results.append(SectionResponse(template_key=key, matched_title=title, content=content, llm_used=llm_used))
    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent_data:app", host="0.0.0.0", port=8000, reload=True)
