from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber
from pathlib import Path
import json
import re
from typing import List, Optional, Dict, Any
import logging
from datetime import datetime
import hashlib
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Enhanced PDF to JSON Converter",
    description="Extract sections and tables from PDF files with advanced processing",
    version="2.5.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INPUT_FOLDER = Path("input")
OUTPUT_FOLDER = Path("output")

# Ensure folders exist
INPUT_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)


class PDFProcessor:
    """Enhanced PDF processing with complete table/section separation."""

    def __init__(self):
        self.section_patterns = [
            re.compile(r'^(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z\s]{5,}?)$', re.MULTILINE),
            re.compile(r'^([A-Z]+)\.\s+([A-Z][A-Za-z\s]{5,}?)$', re.MULTILINE),
            re.compile(r'^([IVX]+)\.\s+([A-Z][A-Za-z\s]{5,}?)$', re.MULTILINE),
        ]
        self.global_table_headers: Optional[List[str]] = None

    # Utility: check if text object outside table bboxes
    def _not_within_bboxes(self, obj, bboxes):
        def obj_in_bbox(_bbox):
            v_mid = (obj["top"] + obj["bottom"]) / 2
            h_mid = (obj["x0"] + obj["x1"]) / 2
            x0, top, x1, bottom = _bbox
            return (h_mid >= x0) and (h_mid < x1) and (v_mid >= top) and (v_mid < bottom)
        return not any(obj_in_bbox(__bbox) for __bbox in bboxes)

    def is_likely_header_row(self, row: List[Any]) -> bool:
        if not row or len(row) < 2:
            return False
        first_cell = str(row[0]).strip() if row[0] else ""
        data_indicators = [
            first_cell.isdigit() and len(first_cell) > 2,
            first_cell.startswith(('⦁', '•', '-', '*')),
            'The Sole' in first_cell,
            'Certificate' in first_cell,
            len(first_cell) > 100,
        ]
        if any(data_indicators):
            return False

        header_score = 0
        data_score = 0
        for i, cell in enumerate(row[:6]):
            if cell and isinstance(cell, (str, int, float)):
                cell_text = str(cell).strip()
                cell_lower = cell_text.lower()
                if len(cell_text.split()) <= 4 and len(cell_text) < 60:
                    header_score += 1
                if cell_text.isupper() or (cell_text and cell_text[0].isupper()):
                    header_score += 1
                if any(word in cell_lower for word in [
                    'no', 'name', 'type', 'date', 'description', 'criteria',
                    'evidence', 'id', 'code', 'status', 'category', 'amount',
                    'qualification', 'documentary', 'information', 'details'
                ]):
                    header_score += 2
                if len(cell_text) > 100:
                    data_score += 3
                if cell_text.startswith(('⦁', '•', '-')):
                    data_score += 3
                if '\n' in cell_text and len(cell_text.split('\n')) > 2:
                    data_score += 2
                if re.search(r'\d{2,}', cell_text):
                    data_score += 1
        return header_score > data_score and data_score < 4

    def clean_and_normalize_headers(self, headers: List[Any]) -> List[str]:
        cleaned, seen_headers = [], {}
        for i, header in enumerate(headers):
            if header:
                clean_header = str(header).strip()
                clean_header = re.sub(r'\s+', ' ', clean_header)
                clean_header = re.sub(r'[^\w\s\-_]', '', clean_header)
                if not clean_header:
                    clean_header = f"Column_{i+1}"
                original, counter = clean_header, 1
                while clean_header in seen_headers:
                    clean_header = f"{original}_{counter}"
                    counter += 1
                seen_headers[clean_header] = True
                cleaned.append(clean_header)
            else:
                column_name = f"Column_{i+1}"
                while column_name in seen_headers:
                    column_name = f"Column_{i+1}_{len(seen_headers)}"
                seen_headers[column_name] = True
                cleaned.append(column_name)
        return cleaned

    def create_row_dict(self, row: List[Any], headers: List[str]) -> Dict[str, str]:
        row_dict = {}
        for i, cell in enumerate(row):
            header = headers[i] if i < len(headers) else f"Column_{i+1}"
            if cell is None or cell == '':
                cell_value = ""
            elif isinstance(cell, (int, float)):
                cell_value = str(cell)
            else:
                cell_value = re.sub(r'\s+', ' ', str(cell).strip())
            row_dict[header] = cell_value
        for i in range(len(row), len(headers)):
            row_dict[headers[i]] = ""
        return row_dict

    def extract_metadata(self, pdf_path: Path, pdf) -> Dict[str, Any]:
        metadata = {
            "filename": pdf_path.name,
            "file_size": pdf_path.stat().st_size,
            "page_count": len(pdf.pages),
            "processed_at": datetime.now().isoformat(),
        }
        try:
            if hasattr(pdf, 'metadata') and pdf.metadata:
                pdf_info = pdf.metadata
                metadata.update({
                    "title": pdf_info.get('Title', ''),
                    "author": pdf_info.get('Author', ''),
                    "subject": pdf_info.get('Subject', ''),
                    "creator": pdf_info.get('Creator', ''),
                    "producer": pdf_info.get('Producer', ''),
                    "creation_date": str(pdf_info.get('CreationDate', '')),
                    "modification_date": str(pdf_info.get('ModDate', '')),
                })
        except Exception as e:
            logger.warning(f"Could not extract metadata: {e}")
        return metadata

    def get_content_hash(self, content: str) -> str:
        return hashlib.md5(content.encode()).hexdigest()

    def build_table_text_blacklist(self, all_tables: List[Dict]) -> set:
        """Build an optimized blacklist of text that appears in tables"""
        blacklist = set()
        
        # Limit blacklist size to avoid performance issues
        MAX_BLACKLIST_SIZE = 1000
        processed_count = 0

        for table_info in all_tables:
            if processed_count >= MAX_BLACKLIST_SIZE:
                break
                
            table_data = table_info.get('data', [])
            for row in table_data:
                if processed_count >= MAX_BLACKLIST_SIZE:
                    break
                    
                for key, value in row.items():
                    if processed_count >= MAX_BLACKLIST_SIZE:
                        break
                        
                    if value and len(value.strip()) > 15:  # Only longer, more unique text
                        blacklist.add(value.strip())
                        processed_count += 1
                        
                        # Add significant words only
                        words = value.split()
                        for word in words:
                            if len(word) > 12 and processed_count < MAX_BLACKLIST_SIZE:  # More selective
                                blacklist.add(word)
                                processed_count += 1

        logger.info(f"Built optimized table text blacklist with {len(blacklist)} entries")
        return blacklist

    def should_merge_tables(self, table1_data: List[Dict], table2_data: List[Dict], page1: int, page2: int) -> bool:
        if not table1_data or not table2_data:
            return False

        if abs(page2 - page1) > 1:
            return False

        cols1 = len(table1_data[0]) if table1_data else 0
        cols2 = len(table2_data[0]) if table2_data else 0
        if abs(cols1 - cols2) > 1:
            return False

        first_row_values = list(table2_data[0].values()) if table2_data else []
        if self.is_likely_header_row(first_row_values):
            return False

        return True

    def process_page_batch(self, pages_batch: List[tuple], pdf_path: Path) -> Dict[str, Any]:
        """Process a batch of pages in parallel"""
        batch_tables = []
        batch_texts = []
        
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page_data in pages_batch:
                page = pdf.pages[page_num]
                
                # Early termination for empty pages
                page_text = page.extract_text()
                if not page_text or len(page_text.strip()) < 50:
                    logger.info(f"Skipping empty page {page_num + 1}")
                    continue
                
                # Extract tables
                try:
                    tables = page.extract_tables()
                    for table_idx, table in enumerate(tables):
                        if table and len(table) >= 1:
                            table_str = str(table)
                            table_hash = self.get_content_hash(table_str)
                            batch_tables.append({
                                "hash": table_hash,
                                "table": table,
                                "page": page_num + 1,
                                "position": (page_num + 1) * 1000 + table_idx * 10
                            })
                except Exception as e:
                    logger.warning(f"Error extracting tables from page {page_num + 1}: {str(e)}")
                
                # Extract filtered text
                try:
                    table_bboxes = [table.bbox for table in page.find_tables()]
                    filtered_page = page.filter(lambda obj: self._not_within_bboxes(obj, table_bboxes))
                    page_text_no_tables = filtered_page.extract_text() or ""
                    batch_texts.append({
                        "page": page_num + 1,
                        "text": page_text_no_tables
                    })
                except Exception as e:
                    logger.warning(f"Error extracting filtered text from page {page_num + 1}: {str(e)}")
        
        return {"tables": batch_tables, "texts": batch_texts}


# Global function for multiprocessing (must be at module level)
def process_page_range_worker(pdf_path_str: str, start_page: int, end_page: int, section_patterns: List[str]) -> Dict[str, Any]:
    """Worker function for parallel page processing"""
    import pdfplumber
    import re
    import hashlib
    
    def get_content_hash(content: str) -> str:
        return hashlib.md5(content.encode()).hexdigest()
    
    def _not_within_bboxes(obj, bboxes):
        def obj_in_bbox(_bbox):
            v_mid = (obj["top"] + obj["bottom"]) / 2
            h_mid = (obj["x0"] + obj["x1"]) / 2
            x0, top, x1, bottom = _bbox
            return (h_mid >= x0) and (h_mid < x1) and (v_mid >= top) and (v_mid < bottom)
        return not any(obj_in_bbox(__bbox) for __bbox in bboxes)
    
    batch_tables = []
    batch_texts = []
    
    try:
        with pdfplumber.open(pdf_path_str) as pdf:
            for page_num in range(start_page, min(end_page, len(pdf.pages))):
                page = pdf.pages[page_num]
                
                # Early termination for empty pages
                page_text = page.extract_text()
                if not page_text or len(page_text.strip()) < 50:
                    continue
                
                # Extract tables
                try:
                    tables = page.extract_tables()
                    for table_idx, table in enumerate(tables):
                        if table and len(table) >= 1:
                            table_str = str(table)
                            table_hash = get_content_hash(table_str)
                            batch_tables.append({
                                "hash": table_hash,
                                "table": table,
                                "page": page_num + 1,
                                "position": (page_num + 1) * 1000 + table_idx * 10
                            })
                except Exception as e:
                    print(f"Error extracting tables from page {page_num + 1}: {str(e)}")
                
                # Extract filtered text
                try:
                    table_bboxes = [table.bbox for table in page.find_tables()]
                    filtered_page = page.filter(lambda obj: _not_within_bboxes(obj, table_bboxes))
                    page_text_no_tables = filtered_page.extract_text() or ""
                    batch_texts.append({
                        "page": page_num + 1,
                        "text": page_text_no_tables
                    })
                except Exception as e:
                    print(f"Error extracting filtered text from page {page_num + 1}: {str(e)}")
    
    except Exception as e:
        print(f"Error processing page range {start_page}-{end_page}: {str(e)}")
    
    return {"tables": batch_tables, "texts": batch_texts}


class PDFProcessorEnhanced(PDFProcessor):
    """Enhanced PDF processor with parallel processing capabilities"""
    
    def extract_sections_and_tables(self, pdf_path: Path) -> Dict[str, Any]:
        result = {
            "file": pdf_path.name,
            "metadata": {},
            "content": [],
            "processing_info": {"total_pages": 0, "tables_found": 0, "sections_found": 0, "warnings": []}
        }

        try:
            start_time = time.time()
            with pdfplumber.open(pdf_path) as pdf:
                result["metadata"] = self.extract_metadata(pdf_path, pdf)
                total_pages = len(pdf.pages)
                result["processing_info"]["total_pages"] = total_pages
                
                logger.info(f"Starting processing of {total_pages} pages for {pdf_path.name}")

                self.global_table_headers = None

                # STEP 1: Process pages in batches with parallel execution
                batch_size = min(20, max(1, total_pages // 10))  # Dynamic batch size
                page_batches = []
                
                for i in range(0, total_pages, batch_size):
                    batch = [(page_num, None) for page_num in range(i, min(i + batch_size, total_pages))]
                    page_batches.append(batch)
                
                logger.info(f"Processing {len(page_batches)} batches of {batch_size} pages each")
                
                all_tables = []
                all_page_texts = []
                seen_table_hashes = set()
                
                # Process pages in parallel using ProcessPoolExecutor
                max_workers = min(4, mp.cpu_count())  # Limit workers to avoid resource issues
                logger.info(f"Using {max_workers} parallel workers for processing")
                
                # Create page ranges for parallel processing
                page_ranges = []
                for i in range(0, total_pages, batch_size):
                    page_ranges.append((str(pdf_path), i, min(i + batch_size, total_pages)))
                
                with ProcessPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all tasks
                    future_to_range = {
                        executor.submit(process_page_range_worker, pdf_path_str, start, end, []):
                        (start, end) for pdf_path_str, start, end in page_ranges
                    }
                    
                    # Collect results as they complete
                    for future in as_completed(future_to_range):
                        start_page, end_page = future_to_range[future]
                        try:
                            batch_result = future.result()
                            logger.info(f"Completed pages {start_page + 1}-{end_page} in parallel")
                            
                            # Process tables from batch
                            for table_info in batch_result["tables"]:
                                table_hash = table_info["hash"]
                                if table_hash not in seen_table_hashes:
                                    seen_table_hashes.add(table_hash)
                                    processed_table = self.process_single_table(table_info["table"])
                                    if processed_table:
                                        all_tables.append({
                                            "type": "table",
                                            "page": table_info["page"],
                                            "position": table_info["position"],
                                            "data": processed_table
                                        })
                            
                            # Add texts from batch
                            all_page_texts.extend(batch_result["texts"])
                            
                        except Exception as e:
                            logger.error(f"Error processing pages {start_page + 1}-{end_page}: {str(e)}")
                            result["processing_info"]["warnings"].append(f"Failed to process pages {start_page + 1}-{end_page}: {str(e)}")

                logger.info(f"Extracted {len(all_tables)} tables from all pages")

                # STEP 2: Build blacklist from table content
                table_text_blacklist = self.build_table_text_blacklist(all_tables)

                # STEP 4: Extract sections from filtered texts only
                all_sections = self.extract_sections_from_all_pages(all_page_texts, table_text_blacklist)

                logger.info(f"Extracted {len(all_sections)} sections")

                # STEP 5 & 6: Merge adjacent tables and combine all content
                merged_tables = []
                i = 0
                while i < len(all_tables):
                    current_table = all_tables[i]
                    merged_table = {
                        "type": "table",
                        "page": current_table["page"],
                        "position": current_table["position"],
                        "data": current_table["data"].copy()
                    }
                    j = i + 1
                    while j < len(all_tables):
                        next_table = all_tables[j]
                        if self.should_merge_tables(
                            merged_table["data"],
                            next_table["data"],
                            merged_table["page"],
                            next_table["page"]
                        ):
                            merged_table["data"].extend(next_table["data"])
                            logger.info(f"Merged table from page {next_table['page']} with table from page {merged_table['page']}")
                            j += 1
                        else:
                            break
                    merged_tables.append(merged_table)
                    i = j

                all_content = merged_tables + all_sections
                all_content.sort(key=lambda x: x["position"])

                for item in all_content:
                    item.pop("position", None)

                result["content"] = all_content
                result["processing_info"]["tables_found"] = len(merged_tables)
                result["processing_info"]["sections_found"] = len(all_sections)

                total_time = time.time() - start_time
                logger.info(f"Final content: {len(all_content)} items ({len(merged_tables)} tables, {len(all_sections)} sections)")
                logger.info(f"Total processing time: {total_time:.2f}s ({total_time/total_pages:.3f}s per page)")
                
                result["processing_info"]["processing_time_seconds"] = round(total_time, 2)
                result["processing_info"]["avg_time_per_page"] = round(total_time/total_pages, 3)

        except Exception as e:
            error_msg = f"Error processing PDF {pdf_path.name}: {str(e)}"
            logger.error(error_msg)
            result["error"] = error_msg
            result["processing_info"]["warnings"].append(error_msg)

        return result

    def process_single_table(self, table: List[List[Any]]) -> List[Dict[str, str]]:
        if not table:
            return []

        processed_data = []
        first_row = table[0]

        if self.is_likely_header_row(first_row):
            headers = self.clean_and_normalize_headers(first_row)
            data_rows = table[1:]
            self.global_table_headers = headers
        else:
            if self.global_table_headers:
                headers = self.global_table_headers
                data_rows = table
            else:
                max_cols = max(len(r) for r in table) if table else 0
                headers = [f"Column_{i + 1}" for i in range(max_cols)]
                data_rows = table
                self.global_table_headers = headers

        for row in data_rows:
            if row and any(cell and str(cell).strip() for cell in row):
                processed_data.append(self.create_row_dict(row, headers))

        return processed_data

    def extract_sections_from_text(self, text: str, page_num: int, table_text_blacklist: set) -> List[Dict[str, Any]]:
        sections = []
        if not text:
            return sections

        # Identify section headers
        potential_sections = []
        for pattern in self.section_patterns:
            matches = list(pattern.finditer(text))
            for match in matches:
                if match.lastindex >= 2:
                    num, title = match.group(1), match.group(2)

                    title_lower = title.lower()
                    if any(keyword in title_lower for keyword in [
                        'qualification', 'documentary', 'evidence', 'bidder', 'certificate',
                        'sole', 'company', 'registered', 'turnover', 'crore', 'financial'
                    ]):
                        continue

                    potential_sections.append({
                        "start": match.start(),
                        "end": match.end(),
                        "number": num,
                        "title": title.strip(),
                        "match": match
                    })

        potential_sections.sort(key=lambda x: x["start"])

        for i, section_info in enumerate(potential_sections):
            start = section_info["end"]
            end = potential_sections[i + 1]["start"] if i + 1 < len(potential_sections) else len(text)

            raw_content = text[start:end].strip()

            lines = raw_content.split('\n')
            filtered_lines = []

            for line in lines:
                line = line.strip()
                if len(line) < 3:
                    continue

                skip_line = False

                for blacklisted_text in table_text_blacklist:
                    if (len(blacklisted_text) > 20 and
                            blacklisted_text in line and
                            len(line) < len(blacklisted_text) + 10):
                        skip_line = True
                        break

                if (not skip_line and (
                        re.match(r'^[\d\s\.\-]+$', line) or
                        re.search(r'^Copy of.*Certificate|^Valid Work Order|^Go-Live.*Certificate|^Documentary Evidence|^Qualification Criteria', line, re.IGNORECASE) or
                        re.match(r'^\d+\s+[A-Z][a-z]+.*\s+[A-Z][a-z]+.*$', line) or
                        'Column_' in line
                )):
                    skip_line = True

                if not skip_line:
                    filtered_lines.append(line)

            content = "\n".join(filtered_lines).strip()

            if content and len(content) > 15:
                sections.append({
                    "number": section_info["number"],
                    "title": section_info["title"],
                    "content": content,
                    "page": page_num
                })

        return sections

    def extract_sections_from_all_pages(self, all_page_texts: List[Dict], table_text_blacklist: set) -> List[Dict[str, Any]]:
        sections = []

        combined_text = ""
        page_boundaries = {}

        for page_info in all_page_texts:
            page_num = page_info["page"]
            page_text = page_info["text"]
            page_boundaries[len(combined_text)] = page_num
            combined_text += f"\n{page_text}\n"

        potential_sections = []
        for pattern in self.section_patterns:
            matches = list(pattern.finditer(combined_text))
            for match in matches:
                if match.lastindex >= 2:
                    num, title = match.group(1), match.group(2)

                    title_lower = title.lower()
                    if any(keyword in title_lower for keyword in [
                        'qualification', 'documentary', 'evidence', 'bidder', 'certificate',
                        'sole', 'company', 'registered', 'turnover', 'crore', 'financial'
                    ]):
                        continue

                    section_start_pos = match.start()
                    section_page = 1
                    for pos, page in page_boundaries.items():
                        if pos <= section_start_pos:
                            section_page = page
                        else:
                            break

                    potential_sections.append({
                        "start": match.start(),
                        "end": match.end(),
                        "number": num,
                        "title": title.strip(),
                        "page": section_page,
                        "position": section_page * 1000 + 500
                    })

        potential_sections.sort(key=lambda x: x["start"])

        for i, section_info in enumerate(potential_sections):
            start = section_info["end"]
            end = potential_sections[i + 1]["start"] if i + 1 < len(potential_sections) else len(combined_text)

            raw_content = combined_text[start:end].strip()

            lines = raw_content.split('\n')
            filtered_lines = []

            for line in lines:
                line = line.strip()
                if len(line) < 3:
                    continue

                skip_line = False

                for blacklisted_text in table_text_blacklist:
                    if (len(blacklisted_text) > 20 and
                            blacklisted_text in line and
                            len(line) < len(blacklisted_text) + 10):
                        skip_line = True
                        break

                if not skip_line:
                    if (re.match(r'^[\d\s\.\-]+$', line) or
                            re.search(r'^Copy of.*Certificate|^Valid Work Order|^Go-Live.*Certificate|^Documentary Evidence|^Qualification Criteria', line, re.IGNORECASE) or
                            re.match(r'^\d+\s+[A-Z][a-z]+.*\s+[A-Z][a-z]+.*$', line) or
                            'Column_' in line):
                        skip_line = True

                if not skip_line:
                    filtered_lines.append(line)

            content = "\n".join(filtered_lines).strip()

            if content and len(content) > 15:
                sections.append({
                    "type": "section",
                    "number": section_info["number"],
                    "title": section_info["title"],
                    "content": content,
                    "page": section_info["page"],
                    "position": section_info["position"]
                })

        return sections

    def simplify_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        simplified = {
            "filename": result.get("file") or result.get("metadata", {}).get("filename", ""),
            "pages": result.get("processing_info", {}).get("total_pages", 0),
            "content": []
        }

        for item in result.get("content", []):
            if item["type"] == "table":
                simplified["content"].append({
                    "type": "table",
                    "page": item["page"],
                    "rows": item["data"]
                })
            elif item["type"] == "section":
                simplified["content"].append({
                    "type": "section",
                    "page": item["page"],
                    "number": item.get("number", ""),
                    "title": item.get("title", ""),
                    "text": item.get("content", "")
                })

        return simplified


pdf_processor = PDFProcessorEnhanced()


@app.get("/")
def index():
    results = []
    if not INPUT_FOLDER.exists():
        raise HTTPException(status_code=404, detail="Input folder not found")
    pdf_files = list(INPUT_FOLDER.glob("*.pdf"))
    if not pdf_files:
        raise HTTPException(status_code=404, detail="No PDF files found in input folder")
    for pdf_file in pdf_files:
        logger.info(f"Processing {pdf_file.name}")
        result = pdf_processor.extract_sections_and_tables(pdf_file)
        simple_result = pdf_processor.simplify_result(result)
        results.append(simple_result)
        output_file = OUTPUT_FOLDER / f"{pdf_file.stem}_processed.json"
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(simple_result, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved output to {output_file}")
        except Exception as e:
            logger.warning(f"Could not save output file: {e}")
    return {"message": "PDF processing completed", "total_files": len(pdf_files),
            "timestamp": datetime.now().isoformat(), "results": results}


@app.post("/upload")
async def upload_and_process(files: List[UploadFile] = File(...)):
    results = []
    for file in files:
        if not file.filename.lower().endswith('.pdf'):
            results.append({"filename": file.filename, "error": "Only PDF files are supported"})
            continue
        try:
            temp_path = INPUT_FOLDER / file.filename
            content = await file.read()
            with open(temp_path, 'wb') as f:
                f.write(content)
            result = pdf_processor.extract_sections_and_tables(temp_path)
            simple_result = pdf_processor.simplify_result(result)
            results.append(simple_result)
            output_file = OUTPUT_FOLDER / f"{temp_path.stem}_processed.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(simple_result, f, indent=2, ensure_ascii=False)
        except Exception as e:
            results.append({"filename": file.filename, "error": str(e)})
    return {"message": "Upload and processing completed", "total_files": len(files), "results": results}


@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat(),
            "input_folder_exists": INPUT_FOLDER.exists(),
            "output_folder_exists": OUTPUT_FOLDER.exists()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
