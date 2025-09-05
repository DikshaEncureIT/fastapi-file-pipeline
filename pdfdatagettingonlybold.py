import fitz  # PyMuPDF
import json

def extract_sections(pdf_path, start_page=1, end_page=None):
    doc = fitz.open(pdf_path)
    results = []

    if end_page is None or end_page > len(doc):
        end_page = len(doc)

    for page_num in range(start_page - 1, end_page):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]

        current_title = None
        current_content = []

        for b in blocks:
            if "lines" in b:
                for line in b["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if not text:
                            continue

                        if "Bold" in span["font"]:  # new section starts
                            # save previous section before starting new one
                            if current_title:
                                results.append({
                                    "title": current_title,
                                    "content": " ".join(current_content).strip()
                                })
                                current_content = []
                            current_title = text
                        else:
                            current_content.append(text)

        # save last section of the page
        if current_title:
            results.append({
                "title": current_title,
                "content": " ".join(current_content).strip()
            })

    return results


if __name__ == "__main__":
    pdf_file = "/home/diksha-encureitlp43/Documents/EncureIT/Project/fastapi-file-pipeline/input/JAMNAGAR MUNICIPAL CORPORATION.pdf"  # replace with your PDF path
    data = extract_sections(pdf_file, start_page=8, end_page=11)

    # Pretty print JSON
    print(json.dumps(data, indent=4, ensure_ascii=False))
