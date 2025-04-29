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
# Initialize CORS for the entire app. Allows all origins by default.
# For production restrict origins: CORS(app, resources={r"/api/*": {"origins": "YOUR_FRONTEND_URL"}})
CORS(app)

# --- Configuration ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://google.com' # Generic referer
}
GENERATION_TIMEOUT = 40 # Seconds for polling
POLL_INTERVAL = 5 # Seconds between poll checks
REQUEST_TIMEOUT = 30 # Seconds for general HTTP requests
MAX_REDIRECT_HOPS = 5 # Max secondary HTML/JS redirects to follow

# --- Core GDFLIX Bypass Function (with Redirect Loop) ---
def get_gdflix_download_link(start_url):
    # --- Session Setup ---
    # Option 1: Standard requests (Default)
    session = requests.Session()
    session.headers.update(HEADERS)

    # Option 2: Cloudscraper (Uncomment below and comment out requests.Session() above if needed)
    # try:
    #     scraper = cloudscraper.create_scraper(...) # Configure as needed
    #     scraper.headers.update(HEADERS)
    #     session = scraper
    #     print("Using cloudscraper session.", file=sys.stderr)
    # except NameError:
    #     print("Error: cloudscraper not imported/installed. Falling back to requests.", file=sys.stderr)
    #     session = requests.Session()
    #     session.headers.update(HEADERS)
    # --- End Session Setup ---

    logs = []
    current_url = start_url
    hops_count = 0
    landed_url = None
    html_content = None

    try:
        # --- Loop to follow HTTP (via allow_redirects) and secondary HTML/JS redirects ---
        while hops_count < MAX_REDIRECT_HOPS:
            logs.append(f"[Hop {hops_count}] Fetching/Checking URL: {current_url}")

            try:
                response = session.get(current_url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                logs.append(f"  Error fetching {current_url}: {e}")
                return None, logs # Can't proceed if fetch fails

            # URL after standard HTTP (3xx) redirects
            landed_url = response.url
            html_content = response.text
            status_code = response.status_code
            logs.append(f"  Landed on: {landed_url} (Status: {status_code})")

            # --- Check HTML content for secondary redirects ---
            next_hop_url = None
            is_secondary_redirect = False

            # Check 1: Meta Refresh Tag
            meta_match = re.search(r'<meta\s+http-equiv="refresh"\s+content="[^"]*url=([^"]+)"', html_content, re.IGNORECASE)
            if meta_match:
                extracted_url = meta_match.group(1).strip().split(';')[0] # Get URL part before potential ;
                potential_next = urljoin(landed_url, extracted_url)
                if potential_next.split('#')[0] != landed_url.split('#')[0]: # Compare without fragment
                    next_hop_url = potential_next
                    logs.append(f"  Detected META refresh redirect to: {next_hop_url}")
                    is_secondary_redirect = True

            # Check 2: JavaScript location.replace (if meta not found)
            if not is_secondary_redirect:
                js_match = re.search(r"location\.replace\(['\"]([^'\"]+)['\"]", html_content, re.IGNORECASE)
                if js_match:
                    extracted_url = js_match.group(1).strip().split('+document.location.hash')[0].strip("'\" ")
                    potential_next = urljoin(landed_url, extracted_url)
                    if potential_next.split('#')[0] != landed_url.split('#')[0]:
                        next_hop_url = potential_next
                        logs.append(f"  Detected JS location.replace redirect to: {next_hop_url}")
                        is_secondary_redirect = True

            # --- Decide whether to follow the secondary redirect ---
            if is_secondary_redirect and next_hop_url:
                logs.append(f"  Following secondary redirect...")
                current_url = next_hop_url # Update URL for the next loop iteration
                hops_count += 1
                time.sleep(0.5) # Small delay before next fetch
            else:
                # No more secondary redirects found, this is the target page
                logs.append(f"  No further actionable secondary redirect found. Proceeding with content analysis.")
                break # Exit the redirect loop

        # --- Check if loop terminated due to max hops ---
        if hops_count >= MAX_REDIRECT_HOPS:
            logs.append(f"Error: Exceeded maximum redirect hops ({MAX_REDIRECT_HOPS}). Stuck at {landed_url}")
            return None, logs

        # --- We now have the final landed_url and html_content ---
        if not landed_url or not html_content:
             logs.append("Error: Failed to retrieve final page content after redirect checks.")
             return None, logs

        page1_url = landed_url # This is the base URL for subsequent steps

        logs.append(f"--- Final Content Page HTML Snippet (URL: {page1_url}) ---")
        logs.append(html_content[:3000] + ('...' if len(html_content) > 3000 else ''))
        logs.append(f"--- End Final Content Page HTML Snippet ---")

        if "cloudflare" in html_content.lower() or "checking your browser" in html_content.lower() or "challenge-platform" in html_content.lower():
             logs.append("WARNING: Potential Cloudflare challenge page detected on final content page!")

        # Parse the FINAL HTML content
        soup1 = BeautifulSoup(html_content, PARSER)
        possible_tags_p1 = soup1.find_all(['a', 'button'])
        logs.append(f"Found {len(possible_tags_p1)} potential link/button tags on final content page ({page1_url}).")

        # --- Step 3: Find Fast Cloud Download button on FINAL page ---
        fast_cloud_link_tag = None
        fast_cloud_pattern = re.compile(r'fast\s*cloud\s*(download|dl)', re.IGNORECASE)
        logs.append("Searching for 'Fast Cloud Download/DL' button text pattern on final content page...")
        for i, tag in enumerate(possible_tags_p1):
            tag_text = tag.get_text(strip=True)
            if fast_cloud_pattern.search(tag_text):
                fast_cloud_link_tag = tag
                logs.append(f"Success: Found potential primary target: <{tag.name}> with text '{tag_text}'")
                break

        # --- If Fast Cloud button WAS found ---
        if fast_cloud_link_tag:
            # Steps 3a, 4, 5: Getting to page 2 (intermediate page)
            fast_cloud_href = fast_cloud_link_tag.get('href')
            if not fast_cloud_href and fast_cloud_link_tag.name == 'button':
                parent_form = fast_cloud_link_tag.find_parent('form')
                if parent_form: fast_cloud_href = parent_form.get('action')

            if not fast_cloud_href:
                logs.append(f"Error: Found '{fast_cloud_link_tag.get_text(strip=True)}' element but couldn't get href/action.")
                return None, logs

            intermediate_url = urljoin(page1_url, fast_cloud_href) # Use final landed URL as base
            logs.append(f"Found intermediate link URL (from Fast Cloud button): {intermediate_url}")
            time.sleep(1) # Small delay

            logs.append(f"Fetching intermediate page URL (potentially with Generate button): {intermediate_url}")
            fetch_headers_p2 = {'Referer': page1_url} # Referer is the page where Fast Cloud was found
            response_intermediate = session.get(intermediate_url, timeout=REQUEST_TIMEOUT, headers=fetch_headers_p2, allow_redirects=True)
            response_intermediate.raise_for_status()
            page2_url = response_intermediate.url # URL after redirects for the intermediate page
            html_content_p2 = response_intermediate.text
            logs.append(f"Landed on intermediate page: {page2_url} (Status: {response_intermediate.status_code})")

            # --- Added HTML Logging for Page 2 ---
            logs.append(f"--- Intermediate Page HTML Content Snippet (URL: {page2_url}) ---")
            logs.append(html_content_p2[:2000] + ('...' if len(html_content_p2) > 2000 else ''))
            logs.append(f"--- End Intermediate Page HTML Snippet ---")
            if "cloudflare" in html_content_p2.lower() or "checking your browser" in html_content_p2.lower():
                 logs.append("WARNING: Potential Cloudflare challenge page detected on Intermediate Page!")
            # --- End HTML Logging ---

            soup2 = BeautifulSoup(html_content_p2, PARSER)
            possible_tags_p2 = soup2.find_all(['a', 'button'])
            logs.append(f"Found {len(possible_tags_p2)} potential link/button tags on intermediate page.")

            # --- Step 6: Find "Cloud Resume Download" button directly on intermediate page ---
            resume_link_tag = None
            resume_text_pattern = re.compile(r'cloud\s+resume\s+download', re.IGNORECASE)
            logs.append("Searching for 'Cloud Resume Download' button text pattern on intermediate page...")
            for tag in possible_tags_p2:
                 tag_text = tag.get_text(strip=True)
                 if resume_text_pattern.search(tag_text):
                    resume_link_tag = tag
                    logs.append(f"Success: Found final link tag directly: <{tag.name}> with text '{tag_text}'")
                    break

            # Step 6a: If final link found directly
            if resume_link_tag:
                final_link_href = resume_link_tag.get('href')
                if not final_link_href and resume_link_tag.name == 'button':
                     parent_form = resume_link_tag.find_parent('form')
                     if parent_form: final_link_href = parent_form.get('action')

                if not final_link_href:
                    logs.append(f"Error: Found '{resume_link_tag.get_text(strip=True)}' but no href/action.")
                    return None, logs

                final_download_link = urljoin(page2_url, final_link_href) # Use intermediate page URL as base
                logs.append(f"Success: Found final Cloud Resume link URL directly: {final_download_link}")
                return final_download_link, logs

            # Step 6b: If not found directly, check for "Generate Cloud Link" button
            else:
                logs.append("Info: 'Cloud Resume Download' not found directly. Checking for 'Generate Cloud Link' button...")
                # ... (Rest of the logic for finding Generate button, POSTing, and Polling) ...
                # ... (This part seemed okay before, ensure urljoin uses page2_url as base) ...
                generate_tag = None
                generate_tag_by_id = soup2.find('button', id='cloud')
                # ... (find by text fallback) ...
                if generate_tag:
                    # ... (extract post_data from form) ...
                    # ... (set post_headers, using page2_url as referer) ...
                    # ... (session.post to page2_url) ...
                    # ... (handle post_response JSON to get page3_url) ...
                     try:
                        post_response = session.post(page2_url, data=post_data, headers=post_headers, timeout=REQUEST_TIMEOUT)
                        # ... handle response ...
                        if page3_url:
                            # ... Start polling loop ...
                            start_time = time.time()
                            while time.time() - start_time < GENERATION_TIMEOUT:
                                # ... poll page3_url ...
                                poll_response = session.get(page3_url, timeout=REQUEST_TIMEOUT, headers={'Referer': page3_url}, allow_redirects=True)
                                poll_landed_url = poll_response.url
                                poll_soup = BeautifulSoup(poll_response.text, PARSER)
                                # ... search for resume button in poll_soup ...
                                if polled_resume_tag:
                                    # ... extract href, urljoin with poll_landed_url ...
                                    final_download_link = urljoin(poll_landed_url, final_link_href)
                                    logs.append(f"Success: Found final Cloud Resume link URL after polling: {final_download_link}")
                                    return final_download_link, logs
                                # ... wait for POLL_INTERVAL ...
                            # Polling Timeout
                            logs.append(f"Error: Link generation timed out after {GENERATION_TIMEOUT}s.")
                            return None, logs
                        else: # page3_url not found in POST response
                            return None, logs # Error already logged during POST handling
                     except requests.exceptions.RequestException as post_err:
                         # ... log post error ...
                         return None, logs

                else: # Generate button wasn't found on intermediate page
                    logs.append("Error: Neither 'Cloud Resume Download' nor 'Generate Cloud Link' button/pattern found on the intermediate page.")
                    return None, logs

        # --- Fallback: If Fast Cloud button was NOT found on final page ---
        else:
            logs.append("Info: 'Fast Cloud Download' button/pattern not found on final content page. Checking for 'PixeldrainDL'...")
            pixeldrain_link_tag = None
            pixeldrain_pattern = re.compile(r'pixeldrain\s*(dl)?', re.IGNORECASE)
            for tag in possible_tags_p1: # Search on the final content page soup (soup1)
                 tag_text = tag.get_text(strip=True)
                 if pixeldrain_pattern.search(tag_text):
                    pixeldrain_link_tag = tag
                    logs.append(f"Success: Found fallback Pixeldrain tag: <{tag.name}> with text '{tag_text}'")
                    break
            if pixeldrain_link_tag:
                 pixeldrain_href = pixeldrain_link_tag.get('href')
                 if not pixeldrain_href and pixeldrain_link_tag.name == 'button':
                     parent_form = pixeldrain_link_tag.find_parent('form')
                     if parent_form: pixeldrain_href = parent_form.get('action')

                 if pixeldrain_href:
                     pixeldrain_full_url = urljoin(page1_url, pixeldrain_href) # Use final landed URL as base
                     logs.append(f"Success: Found Pixeldrain link URL: {pixeldrain_full_url}")
                     return pixeldrain_full_url, logs
                 else:
                     logs.append(f"Error: Found Pixeldrain element but couldn't get href/action.")
                     return None, logs # Fail if href missing
            else:
                # If Pixeldrain also fails
                logs.append("Error: Neither 'Fast Cloud Download/DL' nor 'Pixeldrain(DL)' link/button found on the final content page.")
                return None, logs

    # --- Exception Handling ---
    except requests.exceptions.Timeout as e:
        logs.append(f"Error: Request timed out: {e}")
        return None, logs
    except requests.exceptions.HTTPError as e:
        logs.append(f"Error: HTTP Error: {e.response.status_code} {e.response.reason} for {e.request.url}")
        try: logs.append(f"  Response text snippet: {e.response.text[:500]}")
        except Exception: pass
        return None, logs
    except requests.exceptions.RequestException as e:
        logs.append(f"Error: Network or Request error: {e}")
        return None, logs
    except Exception as e:
        logs.append(f"FATAL: An unexpected error occurred in get_gdflix_download_link: {e}\n{traceback.format_exc()}")
        return None, logs

    # Fallback if no successful path found
    logs.append("Error: Reached end of function without finding a download link.")
    return None, logs


# --- Flask API Endpoint ---
@app.route('/api/gdflix', methods=['POST']) # Only POST is needed now
def gdflix_bypass_api():
    script_logs = []
    result = {"success": False, "error": "Request processing failed", "finalUrl": None, "logs": script_logs}
    status_code = 500 # Default

    try:
        # --- Get JSON data ---
        gdflix_url = None
        try:
            data = request.get_json()
            if not data: raise ValueError("No JSON data received")
            gdflix_url = data.get('gdflixUrl')
            if not gdflix_url: raise ValueError("Missing 'gdflixUrl' key")
            script_logs.append(f"Received JSON POST body with gdflixUrl: {gdflix_url}")
        except Exception as e:
            script_logs.append(f"Error: Could not parse JSON request body or missing key: {e}")
            result["error"] = "Invalid or missing JSON (expected {'gdflixUrl': '...' })"
            status_code = 400
            result["logs"] = script_logs
            return jsonify(result), status_code

        # --- Validate URL format ---
        try:
            parsed_start_url = urlparse(gdflix_url)
            if not parsed_start_url.scheme or not parsed_start_url.netloc: raise ValueError("Invalid scheme/netloc")
            script_logs.append(f"URL format appears valid: {gdflix_url}")
        except Exception as e:
            script_logs.append(f"Error: Invalid URL format: {gdflix_url} ({e})")
            result["error"] = f"Invalid URL format provided: {gdflix_url}"
            status_code = 400
            result["logs"] = script_logs
            return jsonify(result), status_code

        # --- Perform Scraping ---
        script_logs.append(f"Starting GDFLIX bypass process for: {gdflix_url}")
        final_download_link, script_logs_from_func = get_gdflix_download_link(gdflix_url)
        script_logs.extend(script_logs_from_func)

        # --- Prepare Response ---
        if final_download_link:
            script_logs.append("Bypass process completed successfully.")
            result["success"] = True
            result["finalUrl"] = final_download_link
            result["error"] = None
            status_code = 200 # OK
        else:
            script_logs.append("Bypass process failed to find the final download link.")
            result["success"] = False
            # Extract error from logs
            failure_indicators = ["Error:", "FATAL:", "FAILED", "timed out", "neither", "blocked", "exceeded maximum"]
            extracted_error = "GDFLIX Extraction Failed (Check logs)"
            for log_entry in reversed(script_logs):
                log_entry_lower = log_entry.lower()
                if any(indicator.lower() in log_entry_lower for indicator in failure_indicators):
                     parts = re.split(r'(?:Error|FATAL|Info|Warning):\s*', log_entry, maxsplit=1, flags=re.IGNORECASE)
                     extracted_error = (parts[-1] if len(parts) > 1 else log_entry).strip()
                     break
            result["error"] = extracted_error[:250]
            # Use 200 OK for predictable scraping failure, client checks "success" flag
            status_code = 200
            # Or use 422 if you prefer: status_code = 422

    except Exception as e:
        # Catch unexpected errors in the Flask handler itself
        print(f"FATAL API Handler Error: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        script_logs.append(f"FATAL API Handler Error: An unexpected server error occurred.")
        result["success"] = False
        result["error"] = "Internal server error processing the request."
        status_code = 500 # Use 500 for unexpected errors

    finally:
        # Ensure logs are always included
        result["logs"] = script_logs
        # Flask-CORS handles headers automatically
        return jsonify(result), status_code

# --- Run Flask App (for local testing) ---
if __name__ == '__main__':
    # Set debug=False for production!
    # Render uses its own process manager (like Gunicorn) and sets PORT env var.
    app.run(host='0.0.0.0', port=5001, debug=True)
