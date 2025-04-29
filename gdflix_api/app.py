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
    # from bs4 import BeautifulSoup # Already imported above
    PARSER = "lxml"
    LXML_AVAILABLE = True
except ImportError:
    # from bs4 import BeautifulSoup # Already imported above
    PARSER = "html.parser"
    print("Warning: lxml not found, using html.parser.", file=sys.stderr)

# --- Flask App Initialization ---
app = Flask(__name__)

# --- CORS Configuration ---
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

# --- Core GDFLIX Bypass Function (with Redirect Loop and Fixed Generate Logic) ---
def get_gdflix_download_link(start_url):
    # --- Session Setup ---
    session = requests.Session()
    session.headers.update(HEADERS)
    # Cloudscraper logic remains commented out unless needed

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
                return None, logs

            landed_url = response.url
            html_content = response.text
            status_code = response.status_code
            logs.append(f"  Landed on: {landed_url} (Status: {status_code})")

            next_hop_url = None
            is_secondary_redirect = False
            meta_match = re.search(r'<meta\s+http-equiv="refresh"\s+content="[^"]*url=([^"]+)"', html_content, re.IGNORECASE)
            if meta_match:
                extracted_url = meta_match.group(1).strip().split(';')[0]
                potential_next = urljoin(landed_url, extracted_url)
                if potential_next.split('#')[0] != landed_url.split('#')[0]:
                    next_hop_url = potential_next
                    logs.append(f"  Detected META refresh redirect to: {next_hop_url}")
                    is_secondary_redirect = True

            if not is_secondary_redirect:
                js_match = re.search(r"location\.replace\(['\"]([^'\"]+)['\"]", html_content, re.IGNORECASE)
                if js_match:
                    extracted_url = js_match.group(1).strip().split('+document.location.hash')[0].strip("'\" ")
                    potential_next = urljoin(landed_url, extracted_url)
                    if potential_next.split('#')[0] != landed_url.split('#')[0]:
                        next_hop_url = potential_next
                        logs.append(f"  Detected JS location.replace redirect to: {next_hop_url}")
                        is_secondary_redirect = True

            if is_secondary_redirect and next_hop_url:
                logs.append(f"  Following secondary redirect...")
                current_url = next_hop_url
                hops_count += 1
                time.sleep(0.5)
            else:
                logs.append(f"  No further actionable secondary redirect found. Proceeding with content analysis.")
                break

        if hops_count >= MAX_REDIRECT_HOPS:
            logs.append(f"Error: Exceeded maximum redirect hops ({MAX_REDIRECT_HOPS}). Stuck at {landed_url}")
            return None, logs

        if not landed_url or not html_content:
             logs.append("Error: Failed to retrieve final page content after redirect checks.")
             return None, logs

        page1_url = landed_url # This is the final URL after redirects

        logs.append(f"--- Final Content Page HTML Snippet (URL: {page1_url}) ---")
        logs.append(html_content[:3000] + ('...' if len(html_content) > 3000 else ''))
        logs.append(f"--- End Final Content Page HTML Snippet ---")

        if "cloudflare" in html_content.lower() or "checking your browser" in html_content.lower() or "challenge-platform" in html_content.lower():
             logs.append("WARNING: Potential Cloudflare challenge page detected on final content page!")

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
            fast_cloud_href = fast_cloud_link_tag.get('href')
            if not fast_cloud_href and fast_cloud_link_tag.name == 'button':
                parent_form = fast_cloud_link_tag.find_parent('form')
                if parent_form: fast_cloud_href = parent_form.get('action')

            if not fast_cloud_href:
                logs.append(f"Error: Found '{fast_cloud_link_tag.get_text(strip=True)}' element but couldn't get href/action.")
                return None, logs

            intermediate_url = urljoin(page1_url, fast_cloud_href) # Use final landed URL as base
            logs.append(f"Found intermediate link URL (from Fast Cloud button): {intermediate_url}")
            time.sleep(1)

            logs.append(f"Fetching intermediate page URL (potentially with Generate button): {intermediate_url}")
            fetch_headers_p2 = {'Referer': page1_url}
            response_intermediate = session.get(intermediate_url, timeout=REQUEST_TIMEOUT, headers=fetch_headers_p2, allow_redirects=True)
            response_intermediate.raise_for_status()
            page2_url = response_intermediate.url # URL after redirects for the intermediate page
            html_content_p2 = response_intermediate.text
            logs.append(f"Landed on intermediate page: {page2_url} (Status: {response_intermediate.status_code})")

            logs.append(f"--- Intermediate Page HTML Content Snippet (URL: {page2_url}) ---")
            logs.append(html_content_p2[:2000] + ('...' if len(html_content_p2) > 2000 else ''))
            logs.append(f"--- End Intermediate Page HTML Snippet ---")
            if "cloudflare" in html_content_p2.lower() or "checking your browser" in html_content_p2.lower():
                 logs.append("WARNING: Potential Cloudflare challenge page detected on Intermediate Page!")

            soup2 = BeautifulSoup(html_content_p2, PARSER)
            possible_tags_p2 = soup2.find_all(['a', 'button']) # Re-find tags on page 2
            logs.append(f"Found {len(possible_tags_p2)} potential link/button tags on intermediate page ({page2_url}).") # Added URL

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

            # Step 6b: If not found directly, check for "Generate Cloud Link" button (FIXED LOGIC)
            else:
                logs.append("Info: 'Cloud Resume Download' not found directly. Checking for 'Generate Cloud Link' button...")
                generate_tag = None # Initialize

                # Try finding by ID first
                generate_tag_by_id = soup2.find('button', id='cloud')
                if generate_tag_by_id:
                    logs.append("  Found 'Generate Cloud Link' button by id='cloud'.")
                    generate_tag = generate_tag_by_id
                else:
                    # If not found by ID, try finding by text pattern (Fallback - using logic from old script)
                    logs.append("  Button with id='cloud' not found. Searching by text pattern 'generate cloud link'...")
                    generate_pattern = re.compile(r'generate\s+cloud\s+link', re.IGNORECASE)
                    # Use the previously found possible_tags_p2
                    for tag in possible_tags_p2:
                        tag_text = tag.get_text(strip=True)
                        if generate_pattern.search(tag_text):
                            generate_tag = tag
                            logs.append(f"  Success: Found potential generate tag by text: <{tag.name}> with text '{tag_text}'")
                            break # Stop after finding the first match by text

                # --- If Generate button IS found (either by ID or text) ---
                if generate_tag:
                    logs.append(f"Found 'Generate Cloud Link' button: <{generate_tag.name}> id='{generate_tag.get('id', 'N/A')}'")
                    logs.append("Attempting to mimic the JavaScript POST request...")

                    # --- Extract POST data and headers (using logic from OLD SCRIPT analysis) ---
                    post_data = {}
                    parent_form = generate_tag.find_parent('form')
                    if parent_form:
                        logs.append("  Found parent form for generate button. Extracting hidden inputs...")
                        for input_tag in parent_form.find_all('input', type='hidden'):
                            name = input_tag.get('name')
                            value = input_tag.get('value')
                            if name:
                                post_data[name] = value if value is not None else ''
                                logs.append(f"    Extracted hidden input: name='{name}', value='{value}'")
                        # Add button's name/value if it has them (less common for JS buttons)
                        btn_name = generate_tag.get('name')
                        btn_value = generate_tag.get('value')
                        if btn_name and generate_tag.name == 'button':
                             post_data[btn_name] = btn_value if btn_value is not None else ''
                             logs.append(f"    Added button data: name='{btn_name}', value='{btn_value}'")

                    # Fallback/Base using old script's hardcoded values
                    # IMPORTANT: The 'key' might change over time. This was from the old script.
                    default_post_data = {'action': 'cloud', 'key': '08df4425e31c4330a1a0a3cefc45c19e84d0a192', 'action_token': ''}
                    # Merge extracted data over defaults (form data takes precedence)
                    final_post_data = {**default_post_data, **post_data}
                    # Ensure 'action' key is present if not found in form
                    if 'action' not in final_post_data: final_post_data['action'] = 'cloud'
                    logs.append(f"  Final POST data payload: {final_post_data}")

                    parsed_uri = urlparse(page2_url)
                    hostname = parsed_uri.netloc
                    # Headers based on old script analysis + common AJAX headers
                    post_headers = {
                        'Referer': page2_url,
                        'x-token': hostname, # Crucial header from old script
                        'Accept': 'application/json, text/javascript, */*; q=0.01', # Mimic AJAX request
                        'X-Requested-With': 'XMLHttpRequest', # Mimic AJAX request
                        # User-Agent etc. are handled by the session
                    }
                    logs.append(f"  POST headers (excluding session defaults): {post_headers}")

                    logs.append(f"Sending POST request to: {page2_url}")
                    page3_url = None # URL for polling
                    try:
                        post_response = session.post(page2_url, data=final_post_data, headers=post_headers, timeout=REQUEST_TIMEOUT)
                        logs.append(f"  POST response status: {post_response.status_code}")
                        # Don't raise_for_status yet, check content type first
                        # post_response.raise_for_status()

                        content_type = post_response.headers.get('Content-Type', '').lower()
                        if 'application/json' in content_type:
                            try:
                                response_data = post_response.json()
                                logs.append(f"  POST response JSON: {response_data}")

                                # Check for success/error and extract polling URL
                                if post_response.status_code == 200 and not response_data.get('error'):
                                     poll_url_relative = response_data.get('visit_url') or response_data.get('url')
                                     if poll_url_relative:
                                         page3_url = urljoin(page2_url, poll_url_relative) # Use page2_url as base
                                         logs.append(f"  POST successful. Extracted polling URL: {page3_url}")
                                     else:
                                         logs.append("  Error: POST success status but no 'visit_url' or 'url' key found in JSON.")
                                         return None, logs
                                elif response_data.get('error'):
                                     error_msg = response_data.get('message', 'Unknown error from server POST response')
                                     logs.append(f"  Error from POST JSON response: {error_msg} (Status: {post_response.status_code})")
                                     return None, logs
                                else: # Non-200 status but JSON response
                                     logs.append(f"  Error: POST returned status {post_response.status_code} with JSON, but format unclear.")
                                     logs.append(f"  Response JSON: {response_data}")
                                     return None, logs

                            except json.JSONDecodeError:
                                logs.append(f"  Error: Failed to decode JSON response from POST, though Content-Type was JSON.")
                                logs.append(f"  Response text (first 500 chars): {post_response.text[:500]}")
                                return None, logs
                        else: # Content-Type is not JSON
                             logs.append(f"  Error: POST response Content-Type is not JSON ('{content_type}'). Status: {post_response.status_code}")
                             logs.append(f"  Response text (first 500 chars): {post_response.text[:500]}")
                             if "cloudflare" in post_response.text.lower() or "captcha" in post_response.text.lower():
                                 logs.append("  Hint: Cloudflare/Captcha challenge likely blocked the POST request.")
                             # Attempt to raise status for non-JSON errors like 403, 5xx etc.
                             try: post_response.raise_for_status()
                             except requests.exceptions.HTTPError as http_err: logs.append(f"  HTTP Error raised: {http_err}")
                             return None, logs

                    except requests.exceptions.RequestException as post_err:
                        logs.append(f"  Error during POST request network operation: {post_err}")
                        return None, logs

                    # --- If POST was successful and we have page3_url, START POLLING ---
                    if page3_url:
                        logs.append(f"Starting polling loop for {page3_url}...")
                        start_time = time.time()
                        while time.time() - start_time < GENERATION_TIMEOUT:
                            elapsed_time = time.time() - start_time
                            remaining_time = GENERATION_TIMEOUT - elapsed_time
                            wait_time = min(POLL_INTERVAL, remaining_time)
                            if wait_time <= 0: break

                            logs.append(f"  Polling: Waiting {wait_time:.1f}s before checking {page3_url}...")
                            time.sleep(wait_time)

                            poll_landed_url = None # Define scope outside try
                            try:
                                poll_headers = {'Referer': page3_url} # Referer is the polling page itself
                                poll_response = session.get(page3_url, timeout=REQUEST_TIMEOUT, headers=poll_headers, allow_redirects=True)
                                poll_landed_url = poll_response.url
                                poll_status = poll_response.status_code
                                poll_html = poll_response.text
                                logs.append(f"  Polling: GET {page3_url} -> Status {poll_status}, Landed on {poll_landed_url}")

                                if poll_status != 200:
                                    logs.append(f"  Warning: Polling status {poll_status}, continuing poll loop.")
                                    continue

                                poll_soup = BeautifulSoup(poll_html, PARSER)
                                polled_resume_tag = None
                                # Reuse the resume_text_pattern
                                for tag in poll_soup.find_all(['a', 'button']):
                                    if resume_text_pattern.search(tag.get_text(strip=True)):
                                        polled_resume_tag = tag
                                        logs.append(f"    Success: Found 'Cloud Resume Download' after polling on {poll_landed_url}!")
                                        break

                                if polled_resume_tag:
                                    final_link_href = polled_resume_tag.get('href')
                                    if not final_link_href and polled_resume_tag.name == 'button':
                                        parent_form = polled_resume_tag.find_parent('form')
                                        if parent_form: final_link_href = parent_form.get('action')

                                    if not final_link_href:
                                        logs.append(f"    Error: Found polled '{polled_resume_tag.get_text(strip=True)}' element but no href/action.")
                                        return None, logs # Fail if href missing after finding tag

                                    # IMPORTANT: Use poll_landed_url as the base for the final link
                                    final_download_link = urljoin(poll_landed_url, final_link_href)
                                    logs.append(f"Success: Found final Cloud Resume link URL after polling: {final_download_link}")
                                    return final_download_link, logs # <<< SUCCESS PATH

                                # If resume button not found on this poll iteration, log snippet if needed and loop continues
                                # logs.append(f"    'Cloud Resume Download' not yet found on {poll_landed_url}. Polling continues...")

                            except requests.exceptions.Timeout:
                                 logs.append(f"  Warning: Timeout during polling request to {page3_url}. Will retry.")
                            except requests.exceptions.RequestException as poll_err:
                                 logs.append(f"  Warning: Network error during polling request: {poll_err}. Will retry.")
                            except Exception as parse_err:
                                 logs.append(f"  Warning: Error parsing polled page {poll_landed_url or page3_url}: {parse_err}. Will retry.")
                            # Loop continues if no final link found yet and no fatal error

                        # --- Polling Timeout ---
                        logs.append(f"Error: Link generation timed out after {GENERATION_TIMEOUT}s of polling {page3_url}.")
                        return None, logs
                    # else: page3_url wasn't obtained from POST, error already logged and returned None

                else: # Generate button wasn't found on intermediate page (neither by ID nor text)
                    logs.append("Error: Neither 'Cloud Resume Download' nor 'Generate Cloud Link' button/pattern found on the intermediate page.")
                    # Log intermediate page snippet for debugging
                    body_tag_p2 = soup2.find('body')
                    logs.append("--- Intermediate Page Body Snippet (for debugging why buttons were missed) ---")
                    logs.append(str(body_tag_p2)[:1000] + '...' if body_tag_p2 else html_content_p2[:1000] + '...')
                    logs.append("--- End Intermediate Page Body Snippet ---")
                    return None, logs

        # --- Fallback: If Fast Cloud button was NOT found on final page (page1) ---
        else:
            logs.append("Info: 'Fast Cloud Download' button/pattern not found on final content page. Checking for 'PixeldrainDL'...")
            pixeldrain_link_tag = None
            pixeldrain_pattern = re.compile(r'pixeldrain\s*(dl)?', re.IGNORECASE)
            # Search on the final content page soup (soup1)
            for tag in possible_tags_p1:
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
                     # Use page1_url (final landed URL) as base
                     pixeldrain_full_url = urljoin(page1_url, pixeldrain_href)
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


# --- Flask API Endpoint (No changes needed here) ---
@app.route('/api/gdflix', methods=['POST'])
def gdflix_bypass_api():
    script_logs = []
    result = {"success": False, "error": "Request processing failed", "finalUrl": None, "logs": script_logs}
    status_code = 500 # Default

    try:
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

        script_logs.append(f"Starting GDFLIX bypass process for: {gdflix_url}")
        final_download_link, script_logs_from_func = get_gdflix_download_link(gdflix_url)
        script_logs.extend(script_logs_from_func)

        if final_download_link:
            script_logs.append("Bypass process completed successfully.")
            result["success"] = True
            result["finalUrl"] = final_download_link
            result["error"] = None
            status_code = 200 # OK
        else:
            script_logs.append("Bypass process failed to find the final download link.")
            result["success"] = False
            failure_indicators = ["Error:", "FATAL:", "FAILED", "timed out", "neither", "blocked", "exceeded maximum"]
            extracted_error = "GDFLIX Extraction Failed (Check logs)"
            for log_entry in reversed(script_logs):
                log_entry_lower = log_entry.lower()
                if any(indicator.lower() in log_entry_lower for indicator in failure_indicators):
                     # Try to get text after the indicator word
                     parts = re.split(r'(?:Error|FATAL|Info|Warning):\s*', log_entry, maxsplit=1, flags=re.IGNORECASE)
                     extracted_error = (parts[-1] if len(parts) > 1 else log_entry).strip()
                     # Prioritize specific known failure messages
                     if "Neither 'Cloud Resume Download' nor 'Generate Cloud Link'" in extracted_error:
                         extracted_error = "Could not find required buttons on intermediate page."
                     elif "Link generation timed out" in extracted_error:
                          extracted_error = "Link generation process timed out."
                     elif "Exceeded maximum redirect hops" in extracted_error:
                           extracted_error = "Too many redirects encountered."
                     break
            result["error"] = extracted_error[:250] # Limit error length
            status_code = 200 # Use 200 OK even for failure, client checks "success"

    except Exception as e:
        print(f"FATAL API Handler Error: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        script_logs.append(f"FATAL API Handler Error: An unexpected server error occurred.")
        result["success"] = False
        result["error"] = "Internal server error processing the request."
        status_code = 500

    finally:
        result["logs"] = script_logs
        return jsonify(result), status_code

# --- Run Flask App ---
if __name__ == '__main__':
    # Use Gunicorn or similar in production, not Flask's built-in server with debug=True
    # Render uses PORT env var. For local testing, 5001 is fine.
    # Set debug=False for production deployment
    port = int(os.environ.get("PORT", 5001)) # Use PORT from env var if available
    app.run(host='0.0.0.0', port=port, debug=False) # Set debug=False
