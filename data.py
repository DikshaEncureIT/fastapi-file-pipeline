from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import os
import json
import cohere

app = FastAPI()

# Initialize Cohere client with your API key from environment variables
co = cohere.ClientV2(api_key=os.getenv("COHERE_API_KEY"))

# Load your template keys and merged content JSON files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "output", "unique_titles_template.json")
MERGED_PATH = os.path.join(BASE_DIR, "output", "merged_data.json")

with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    template_json = json.load(f)
    TEMPLATE_KEYS = set(template_json.get("template", {}).keys())

with open(MERGED_PATH, "r", encoding="utf-8") as f:
    merged_json = json.load(f)
    MERGED_CONTENT = merged_json.get("combined_content", [])

class SectionResponse(BaseModel):
    template_key: str
    matched_title: str = ""
    content: str = ""
    llm_used: bool = False

def normalize_key(key: str) -> str:
    return key.strip().lower()

def search_content_for_key(key: str):
    query = key.replace("_", " ").strip().lower()

    context = "\n\n".join(
        f"Title: {sec['title']}\nContent: {sec.get('text', '')}"
        for sec in MERGED_CONTENT if sec.get("type") == "section"
    )

    system_message = {
        "role": "system",
        "content": "You are a helpful assistant extracting relevant information from the given text."
    }
    user_message = {
        "role": "user",
        "content": f"Extract relevant information about '{query}' from the following text. If none found, reply 'Not found.'\n\n{context}"
    }

    # Define tools if applicable (example tool shown, can be customized)
    tools = [
        cohere.ToolV2(
            type="function",
            function={
                "name": "extract_section_info",
                "description": "Extract relevant section info from merged content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Query string for extraction."}
                    },
                    "required": ["query"]
                }
            }
        )
    ]

    try:
        response = co.chat(
            model="command-r-08-2024",
            messages=[system_message, user_message],
            tools=tools,
            max_tokens=350,
            temperature=0.1,
            stop_sequences=["Not found."]
        )
        # Correct access to generated content from V2ChatResponse
        text = response.message.content.strip()
        if text.lower() != "not found.":
            return "(LLM extracted)", text, True
    except Exception as e:
        print(f"Cohere SDK error: {e}")

    return "", "", False

@app.get("/template_keys", response_model=List[str])
def get_template_keys():
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

    title, content, llm_used = search_content_for_key(matched_key)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    return SectionResponse(template_key=matched_key, matched_title=title, content=content, llm_used=llm_used)

@app.post("/sections", response_model=List[SectionResponse])
def get_all_sections():
    results = []
    for key in sorted(TEMPLATE_KEYS):
        title, content, llm_used = search_content_for_key(key)
        if not content:
            content = "Content not found"
        results.append(SectionResponse(template_key=key, matched_title=title, content=content, llm_used=llm_used))
    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("data:app", host="0.0.0.0", port=8000, reload=True)
