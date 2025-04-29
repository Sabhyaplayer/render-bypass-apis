# gdflix_api/app.py

import requests
# import cloudscraper # Keep commented unless needed for Cloudflare
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import re
import json
import traceback
import sys
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS # Import CORS

# Try importing lxml, fall back to html.parser if not installed
try:
    from bs4 import BeautifulSoup
    PARSER = "lxml"
    LXML_AVAILABLE = True
except ImportError:
    from bs4 import BeautifulSoup
    PARSER = "html.parser"
    print("Warning: lxml not found, using html.parser.", file=sys.stderr)

# --- Flask App Initialization ---
app = Flask(__name__)

# --- CORS Configuration ---
# Initialize CORS with default settings: Allows all origins, common methods/headers.
# For production, you might want to restrict origins:
# CORS(app, resources={r"/api/*": {"origins": "https://cinema-ghar-index.vercel.app"}})
CORS(app) # Apply CORS to the entire app

# --- Configuration (Copied from your script) ---
# ... (HEADERS, GENERATION_TIMEOUT, etc. remain the same) ...
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://google.com'
}
GENERATION_TIMEOUT = 40
POLL_INTERVAL = 5
REQUEST_TIMEOUT = 30

# --- Core GDFLIX Bypass Function (Copied from previous corrected version) ---
# --- (No changes needed inside get_gdflix_download_link itself) ---
def get_gdflix_download_link(start_url):
    # ... (Keep the full function from the previous answer here) ...
    # --- Session Setup ---
    session = requests.Session()
    session.headers.update(HEADERS)
    # --- Cloudscraper alternative code remains commented out unless needed ---
    logs = []
    try:
        # ... (All the detailed logging and scraping logic) ...
        logs.append(f"Processing GDFLIX URL: {start_url}")

        # Step 1 & 2: Fetch and parse page 1
        logs.append(f"Fetching initial URL: {start_url}")
        response1 = session.get(start_url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
        response1.raise_for_status() # Check for HTTP errors (4xx, 5xx)
        page1_url = response1.url # URL after potential redirects
        logs.append(f"Landed on URL after redirects: {page1_url}")
        logs.append(f"Response status code: {response1.status_code}")

        # --- Enhanced HTML Logging ---
        html_content_p1 = response1.text
        logs.append(f"--- Page 1 HTML Content Snippet (URL: {page1_url}) ---")
        logs.append(html_content_p1[:3000] + ('...' if len(html_content_p1) > 3000 else ''))
        logs.append(f"--- End Page 1 HTML Snippet ---")
        if "cloudflare" in html_content_p1.lower() or "checking your browser" in html_content_p1.lower() or "challenge-platform" in html_content_p1.lower():
             logs.append("WARNING: Potential Cloudflare challenge page detected! Standard requests might be blocked.")
        # --- End HTML Logging ---

        soup1 = BeautifulSoup(html_content_p1, PARSER)
        possible_tags_p1 = soup1.find_all(['a', 'button'])
        logs.append(f"Found {len(possible_tags_p1)} potential link/button tags on page 1.")

        # Step 3: Find Fast Cloud Download button
        fast_cloud_link_tag = None
        fast_cloud_pattern = re.compile(r'fast\s*cloud\s*(download|dl)', re.IGNORECASE)
        logs.append("Searching for 'Fast Cloud Download/DL' button text pattern...")
        for i, tag in enumerate(possible_tags_p1):
            tag_text = tag.get_text(strip=True)
            if fast_cloud_pattern.search(tag_text):
                fast_cloud_link_tag = tag
                logs.append(f"Success: Found potential primary target: <{tag.name}> with text '{tag_text}'")
                break # Found the first match

        # If Fast Cloud button was found
        if fast_cloud_link_tag:
            # ... (rest of the logic for page 2, generate, poll, etc.) ...
             # Steps 3a, 4, 5: Getting to page 2
            fast_cloud_href = fast_cloud_link_tag.get('href')
            if not fast_cloud_href and fast_cloud_link_tag.name == 'button':
                parent_form = fast_cloud_link_tag.find_parent('form')
                if parent_form: fast_cloud_href = parent_form.get('action')

            if not fast_cloud_href:
                logs.append(f"Error: Found '{fast_cloud_link_tag.get_text(strip=True)}' element but couldn't get href/action.")
                return None, logs

            # Ensure URL is absolute
            second_page_url_relative = fast_cloud_href
            second_page_url = urljoin(page1_url, second_page_url_relative)
            logs.append(f"Found intermediate link URL: {second_page_url}")
            time.sleep(1) # Small delay

            logs.append(f"Fetching second page URL (potentially with Generate button): {second_page_url}")
            fetch_headers_p2 = {'Referer': page1_url} # Referer should be the previous page
            response2 = session.get(second_page_url, timeout=REQUEST_TIMEOUT, headers=fetch_headers_p2, allow_redirects=True) # Follow redirects
            response2.raise_for_status()
            page2_url = response2.url # Update URL after potential redirects
            logs.append(f"Landed on second page: {page2_url} (Status: {response2.status_code})")
            # ... (rest of logic: check resume, check generate, post, poll...)

        # Step 3b: Fallback - PixeldrainDL on page 1
        else:
            # ... (Pixeldrain logic) ...
             logs.append("Info: 'Fast Cloud Download' button/pattern not found on first page. Checking for 'PixeldrainDL'...")
             # ... rest of pixeldrain check ...
             if not pixeldrain_link_tag: # Added check if pixeldrain was not found either
                 logs.append("Error: Neither 'Fast Cloud Download/DL' nor 'Pixeldrain(DL)' link/button found or processed successfully on the first page.")
                 return None, logs

    # ... (Exception handling remains the same) ...
    except requests.exceptions.Timeout as e:
        logs.append(f"Error: Request timed out: {e}")
        return None, logs
    # ... other specific exceptions ...
    except Exception as e:
        logs.append(f"FATAL: An unexpected error occurred in get_gdflix_download_link: {e}\n{traceback.format_exc()}")
        return None, logs

    # Should ideally return None, logs if no path leads to success
    return None, logs


# --- REMOVED CORS Helper Functions ---
# def _build_cors_preflight_response(): ...
# def _corsify_actual_response(response): ...

# --- Flask API Endpoint ---
# Removed 'OPTIONS' from methods, Flask-CORS handles it.
@app.route('/api/gdflix', methods=['POST'])
def gdflix_bypass_api():
    # No need for explicit OPTIONS check anymore
    # if request.method == 'OPTIONS':
    #     return _build_cors_preflight_response()

    # Only handle POST
    script_logs = [] # Initialize logs for this request
    result = {"success": False, "error": "Request processing failed", "finalUrl": None, "logs": script_logs}
    status_code = 500 # Default to internal server error

    try:
        # ... (JSON parsing and URL validation remain the same) ...
        try:
            data = request.get_json()
            # ... rest of validation ...
        except Exception as e:
             # ... error handling ...
             result["logs"] = script_logs # Make sure logs are included on early exit
             # No need to call _corsify_actual_response, Flask-CORS handles headers
             return jsonify(result), status_code

        # ... (URL validation) ...
        if not valid_url:
             # ... error handling ...
             result["logs"] = script_logs
             return jsonify(result), status_code

        # --- Perform Scraping ---
        script_logs.append(f"Starting GDFLIX bypass process for: {gdflix_url}")
        final_download_link, script_logs_from_func = get_gdflix_download_link(gdflix_url)
        script_logs.extend(script_logs_from_func)

        # --- Prepare Response based on script output ---
        if final_download_link:
            # ... (success case remains the same) ...
             script_logs.append("Bypass process completed successfully.")
             result["success"] = True
             result["finalUrl"] = final_download_link
             result["error"] = None
             status_code = 200
        else:
            # ... (failure case remains the same, including status_code = 200/422 logic) ...
             script_logs.append("Bypass process failed to find the final download link.")
             result["success"] = False
             # ... (extract error message) ...
             result["error"] = extracted_error[:250]
             status_code = 200 # Or 422 if preferred for predictable scraping failure

    except Exception as e:
        # ... (unexpected server error handling remains the same) ...
         print(f"FATAL API Handler Error: {e}", file=sys.stderr)
         print(traceback.format_exc(), file=sys.stderr)
         script_logs.append(f"FATAL API Handler Error: An unexpected server error occurred.")
         result["success"] = False
         result["error"] = "Internal server error processing the request."
         status_code = 500

    finally:
        # Ensure logs are always included in the final result
        result["logs"] = script_logs
        # REMOVED manual CORS call: Flask-CORS handles headers automatically
        # return _corsify_actual_response(jsonify(result)), status_code
        return jsonify(result), status_code

# --- Run Flask App (for local testing or basic deployment) ---
if __name__ == '__main__':
    # debug=True enables auto-reload and detailed errors, disable for production
    # host='0.0.0.0' makes it accessible on the network
    app.run(host='0.0.0.0', port=5001, debug=True) # Use a port Render expects or configure Render
