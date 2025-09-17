from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import re
import requests
import logging
from datetime import datetime

app = FastAPI(title="LLM-Powered Content Search API", version="2.0.0")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# Prepare full context for LLM (all content)
def prepare_full_context():
    """Prepare the complete context from all sections"""
    sections = []
    for item in MERGED_CONTENT:
        if item.get("type") == "section" and item.get("title") and item.get("text"):
            sections.append(f"Section Title: {item['title']}\nContent: {item['text']}")
    return "\n\n---\n\n".join(sections)

def find_relevant_sections(template_key: str, max_sections: int = 5):
    """Find the most relevant sections for a template key to reduce context size"""
    import re
    
    # Normalize template key for better matching
    normalized_key = template_key.lower().replace("_", " ").replace("-", " ")
    key_words = set(normalized_key.split())
    
    relevant_sections = []
    
    logger.info(f"Searching for template_key: '{template_key}' -> normalized: '{normalized_key}' -> key_words: {key_words}")
    
    for item in MERGED_CONTENT:
        if item.get("type") == "section" and item.get("title") and item.get("text"):
            title = item['title'].lower()
            text = item['text'].lower()
            
            # Calculate relevance score
            title_words = set(re.findall(r'\b\w+\b', title))
            text_words = set(re.findall(r'\b\w+\b', text[:1000]))  # Increased to 1000 chars
            
            # Score based on word overlap
            title_score = len(key_words & title_words) * 5  # Increased title importance
            text_score = len(key_words & text_words)
            
            # Bonus for exact phrase matches
            exact_title_match = normalized_key in title
            exact_text_match = normalized_key in text
            
            if exact_title_match:
                title_score += 20  # Higher bonus for exact title match
            if exact_text_match:
                text_score += 10   # Higher bonus for exact text match
            
            # Additional partial matching
            partial_title_score = 0
            partial_text_score = 0
            for word in key_words:
                if word in title:
                    partial_title_score += 2
                if word in text:
                    partial_text_score += 1
                    
            total_score = title_score + text_score + partial_title_score + partial_text_score
            
            # Debug logging for bid_preparation_cost
            if template_key == "bid_preparation_cost" and total_score > 0:
                logger.info(f"Section '{item['title'][:50]}...' scored {total_score} (title:{title_score}, text:{text_score}, partial_title:{partial_title_score}, partial_text:{partial_text_score})")
            
            if total_score > 0:
                relevant_sections.append({
                    'item': item,
                    'score': total_score,
                    'title': item['title']
                })
    
    # Sort by relevance and return top sections
    relevant_sections.sort(key=lambda x: x['score'], reverse=True)
    
    # Debug logging
    logger.info(f"Found {len(relevant_sections)} relevant sections for '{template_key}'")
    if relevant_sections:
        top_3 = relevant_sections[:3]
        for i, section in enumerate(top_3, 1):
            logger.info(f"  {i}. '{section['title']}' (score: {section['score']})")
    
    if relevant_sections:
        sections = []
        for section_data in relevant_sections[:max_sections]:
            item = section_data['item']
            sections.append(f"Section Title: {item['title']}\nContent: {item['text']}")
        return "\n\n---\n\n".join(sections)
    
    # Fallback to full context if no relevant sections found
    logger.warning(f"No relevant sections found for '{template_key}', using full context")
    return prepare_full_context()

FULL_CONTEXT = prepare_full_context()
logger.info(f"Loaded {len([item for item in MERGED_CONTENT if item.get('type') == 'section'])} sections for LLM context")

class SectionResponse(BaseModel):
    template_key: str
    content: str = ""
    llm_used: bool = True  # Always true now since we're using LLM-first approach
    search_query: str = ""
    tokens_used: Optional[int] = None
    processing_time_ms: Optional[int] = None

class LLMSearchRequest(BaseModel):
    template_key: str
    max_tokens: int = 500
    temperature: float = 0.1
    custom_instruction: Optional[str] = None

class BulkSearchRequest(BaseModel):
    template_keys: List[str]
    max_tokens: int = 500
    temperature: float = 0.1
    custom_instruction: Optional[str] = None

def get_cohere_api_key():
    """Get Cohere API key with validation"""
    api_key = os.getenv("COHERE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500, 
            detail="COHERE_API_KEY environment variable is required"
        )
    return api_key

def query_cohere_llm(
    query: str, 
    template_key: str,
    max_tokens: int = 500, 
    temperature: float = 0.1,
    custom_instruction: Optional[str] = None
) -> tuple[str, int, int]:
    """
    Query Cohere LLM with the template key to find relevant content
    Returns: (content, processing_time_ms, estimated_tokens_used)
    """
    start_time = datetime.now()
    
    # Build the prompt
    if custom_instruction:
        instruction = custom_instruction
    else:
        instruction = (
            f"You are a content extractor. Your job is to find and extract ONLY existing content from the provided documents. "
            f"IMPORTANT: Do NOT generate, create, or infer any new information. Only extract what is explicitly written. "
            f"Template key: '{template_key}' - Find content that matches this topic from the provided sections. "
            f"RULES: "
            f"1. Extract ONLY exact content that exists in the provided sections "
            f"2. Do NOT add explanations, summaries, or your own interpretations "
            f"3. Do NOT generate any new information or make assumptions "
            f"4. If you find relevant content, quote it directly from the source "
            f"5. If no relevant content exists, respond EXACTLY with 'No relevant content found.' "
            f"6. Preserve the original formatting and structure of the extracted content"
        )
    
    # Use targeted search for better performance and accuracy
    targeted_content = find_relevant_sections(template_key, max_sections=3)
    
    prompt = f"""{instruction}

Template Key: {template_key}
Search Query: {query}

Available Content (Most Relevant Sections):
{targeted_content}

EXTRACT ONLY FROM ABOVE CONTENT - DO NOT GENERATE NEW TEXT:"""

    headers = {
        "Authorization": f"Bearer {get_cohere_api_key()}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "command-r-plus",  # Using the more powerful model
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": max(0.0, min(temperature, 0.1)),  # Cap temperature at 0.1 for factual responses
        "truncate": "END",
        "top_p": 0.1,  # More focused responses
        "frequency_penalty": 0.5,  # Reduce repetition
        "presence_penalty": 0.3  # Reduce generating new concepts
    }
    
    try:
        logger.info(f"Querying Cohere LLM for template key: {template_key}")
        resp = requests.post("https://api.cohere.ai/v1/generate", headers=headers, json=data, timeout=60)
        resp.raise_for_status()
        
        result = resp.json()
        text = result.get("generations", [{}])[0].get("text", "").strip()
        
        # Calculate processing time
        processing_time = int((datetime.now() - start_time).total_seconds() * 1000)
        
        # Estimate tokens used (rough approximation)
        estimated_tokens = len(prompt.split()) + len(text.split())
        
        # Validate that the response contains content from the source
        def validate_content(llm_response: str, source_content: str) -> bool:
            """Check if LLM response contains content that exists in source"""
            if llm_response.lower().strip() == "no relevant content found.":
                return True
            
            # Split response into sentences and check if key phrases exist in source
            import difflib
            words_in_response = llm_response.lower().split()
            words_in_source = source_content.lower().split()
            
            # Check if significant portion of response words exist in source
            common_words = set(words_in_response) & set(words_in_source)
            if len(common_words) < len(words_in_response) * 0.3:  # At least 30% should match
                return False
            
            # Check for long sequences that might indicate hallucination
            response_sentences = [s.strip() for s in llm_response.split('.') if s.strip()]
            for sentence in response_sentences:
                if len(sentence.split()) > 5:  # Check longer sentences
                    # Use fuzzy matching to see if sentence appears in source
                    matches = difflib.get_close_matches(sentence.lower(), 
                                                      [chunk.lower() for chunk in source_content.split('.') if len(chunk.strip()) > 10], 
                                                      n=1, cutoff=0.6)
                    if not matches:
                        logger.warning(f"Potentially hallucinated sentence detected: {sentence[:50]}...")
                        return False
            
            return True
        
        if text.lower() not in ["no relevant content found.", "not found.", ""] and text:
            # Validate content before returning using the same targeted content
            if validate_content(text, targeted_content):
                logger.info(f"LLM successfully found validated content for: {template_key}")
                return text, processing_time, estimated_tokens
            else:
                logger.warning(f"LLM response failed validation for: {template_key} - potential hallucination detected")
                return "No relevant content found for this template key.", processing_time, estimated_tokens
        else:
            logger.info(f"LLM did not find relevant content for: {template_key}")
            return "No relevant content found for this template key.", processing_time, estimated_tokens
            
    except requests.RequestException as e:
        logger.error(f"Error querying Cohere API: {str(e)}")
        raise HTTPException(status_code=503, detail=f"LLM service error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error in LLM query: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

def search_with_llm(
    template_key: str, 
    max_tokens: int = 500, 
    temperature: float = 0.1,
    custom_instruction: Optional[str] = None
) -> SectionResponse:
    """Search for content using LLM based on template key"""
    
    # Normalize the template key for search
    search_query = template_key.replace("_", " ").replace("-", " ").strip()
    
    # Query the LLM
    content, processing_time, tokens_used = query_cohere_llm(
        search_query, 
        template_key,
        max_tokens, 
        temperature,
        custom_instruction
    )
    
    return SectionResponse(
        template_key=template_key,
        content=content,
        llm_used=True,
        search_query=search_query,
        tokens_used=tokens_used,
        processing_time_ms=processing_time
    )

@app.get("/template_keys", response_model=List[str])
def get_template_keys():
    """List all available template keys"""
    return sorted(TEMPLATE_KEYS)

@app.get("/section/{template_key}", response_model=SectionResponse)
def get_section(
    template_key: str, 
    max_tokens: int = 500, 
    temperature: float = 0.1,
    custom_instruction: Optional[str] = None
):
    """Get content for a specific template key using LLM search"""
    
    # Validate template key exists
    if template_key not in TEMPLATE_KEYS:
        # Try to find similar keys
        normalized_input = template_key.lower().replace("_", "").replace("-", "")
        similar_keys = [
            key for key in TEMPLATE_KEYS 
            if normalized_input in key.lower().replace("_", "").replace("-", "")
        ]
        
        if similar_keys:
            suggestion_msg = f"Template key '{template_key}' not found. Similar keys: {', '.join(similar_keys[:5])}"
        else:
            suggestion_msg = f"Template key '{template_key}' not found. Use /template_keys to see available keys."
        
        raise HTTPException(status_code=404, detail=suggestion_msg)
    
    return search_with_llm(template_key, max_tokens, temperature, custom_instruction)

@app.post("/section/search", response_model=SectionResponse)
def search_section_with_llm(request: LLMSearchRequest):
    """Search for content using LLM with custom parameters"""
    return search_with_llm(
        request.template_key,
        request.max_tokens,
        request.temperature,
        request.custom_instruction
    )

@app.post("/sections/bulk", response_model=List[SectionResponse])
def bulk_search_sections(request: BulkSearchRequest):
    """Process multiple template keys using LLM"""
    results = []
    total_keys = len(request.template_keys)
    
    logger.info(f"Processing {total_keys} template keys with LLM")
    
    for i, key in enumerate(request.template_keys, 1):
        logger.info(f"Processing key {i}/{total_keys}: {key}")
        
        try:
            # Validate key exists
            if key not in TEMPLATE_KEYS:
                results.append(SectionResponse(
                    template_key=key,
                    content=f"Template key '{key}' not found in available keys.",
                    llm_used=False,
                    search_query="",
                ))
                continue
            
            result = search_with_llm(
                key,
                request.max_tokens,
                request.temperature,
                request.custom_instruction
            )
            results.append(result)
            
        except Exception as e:
            logger.error(f"Error processing key '{key}': {str(e)}")
            results.append(SectionResponse(
                template_key=key,
                content=f"Error processing key: {str(e)}",
                llm_used=False,
                search_query="",
            ))
    
    return results

@app.post("/sections/all", response_model=List[SectionResponse])
def search_all_sections(
    max_tokens: int = 500, 
    temperature: float = 0.1,
    custom_instruction: Optional[str] = None
):
    """Process all template keys using LLM"""
    request = BulkSearchRequest(
        template_keys=list(TEMPLATE_KEYS),
        max_tokens=max_tokens,
        temperature=temperature,
        custom_instruction=custom_instruction
    )
    return bulk_search_sections(request)

@app.get("/stats")
def get_stats():
    """Get statistics about the loaded data"""
    sections_count = len([item for item in MERGED_CONTENT if item.get("type") == "section"])
    total_content_length = len(FULL_CONTEXT)
    
    return {
        "total_template_keys": len(TEMPLATE_KEYS),
        "total_sections_available": sections_count,
        "total_content_characters": total_content_length,
        "cohere_api_configured": bool(os.getenv("COHERE_API_KEY")),
        "available_endpoints": [
            "GET /template_keys - List all template keys",
            "GET /section/{key} - Search single key with LLM",
            "POST /section/search - Search with custom parameters",
            "POST /sections/bulk - Search multiple specific keys",
            "POST /sections/all - Search all keys",
            "GET /stats - This endpoint"
        ]
    }

@app.get("/health")
def health_check():
    """Health check endpoint"""
    try:
        api_key = get_cohere_api_key()
        # Test a minimal API call
        test_response = requests.post(
            "https://api.cohere.ai/v1/generate",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "command-r", "prompt": "test", "max_tokens": 1},
            timeout=10
        )
        cohere_working = test_response.status_code == 200
    except:
        cohere_working = False
    
    return {
        "status": "healthy",
        "cohere_api_working": cohere_working,
        "total_template_keys": len(TEMPLATE_KEYS),
        "total_sections": len([item for item in MERGED_CONTENT if item.get("type") == "section"]),
        "llm_search_mode": True
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent_data:app", host="0.0.0.0", port=8000, reload=True)