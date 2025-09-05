import os
import json
import google.generativeai as genai
from dotenv import load_dotenv

# Load API key
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def refine_tables_with_gemini(tables: list):
    """Use Gemini 2.0 Flash-Lite to clean and normalize extracted tables."""
    model = genai.GenerativeModel("gemini-2.0-flash-lite")

    prompt = f"""
    You are a data cleaning assistant. 
    Take the raw extracted tables and return clean JSON.

    Rules:
    - Use the first row as headers.
    - Remove empty rows/columns.
    - Ensure rows are key-value JSON objects.

    Schema:
    [
      {{
        "table_no": number,
        "data": [
          {{ "Header1": "value1", "Header2": "value2" }}
        ]
      }}
    ]

    Return only JSON, no explanation.
    Raw tables:
    {json.dumps(tables, indent=2)}
    """

    response = model.generate_content(prompt)
    raw_output = response.text.strip()

    try:
        return json.loads(raw_output)
    except:
        cleaned = raw_output.strip("```json").strip("```").strip()
        return json.loads(cleaned)
