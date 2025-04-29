# gdflix_api/app.py

import requests
# import cloudscraper # Uncomment this if you need to bypass Cloudflare
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import re
import json
import traceback
import sys
from flask import Flask, request, jsonify, make_response # Import Flask components

# Try importing lxml, fall back to html.parser if not installed
try:
    from bs4 import BeautifulSoup
    PARSER = "lxml"
    LXML_AVAILABLE = True
except ImportError:
    from bs4 import BeautifulSoup
    PARSER = "html.parser"
    # Print warning to stderr so it appears in Render/console logs
    print("Warning: lxml not found, using html.parser.", file=sys.stderr)

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Configuration ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://google.com' # Adding a generic referer
}
GENERATION_TIMEOUT = 40 # Seconds
POLL_INTERVAL = 5 # Seconds
REQUEST_TIMEOUT = 30 # Seconds

# --- Core GDFLIX Bypass Function ---
def get_gdflix_download_link(start_url):
    # --- Session Setup ---
    # Option 1: Standard requests (Default)
    session = requests.Session()
    session.headers.update(HEADERS)

    # Option 2: Cloudscraper (Uncomment below and comment out requests.Session() above if needed)
    # try:
    #     scraper = cloudscraper.create_scraper(
    #         browser={
    #             'browser': 'chrome', # Mimic Chrome
    #             'platform': 'windows',
    #             'mobile': False
    #         },
    #         delay=10 # Add a delay between requests if needed
    #     )
    #     scraper.headers.update(HEADERS) # Apply base headers
    #     # Use 'scraper.get' and 'scraper.post' instead of 'session.get/post' below
    #     session = scraper # Assign to session variable for compatibility, or rename all calls
    #     print("Using cloudscraper session.", file=sys.stderr)
    # except NameError:
    #     print("Error: cloudscraper not imported or installed. Falling back to requests.", file=sys.stderr)
    #     session = requests.Session()
    #     session.headers.update(HEADERS)
    # --- End Session Setup ---


    logs = [] # Collect logs for debugging

    try:
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
        # More flexible pattern: accounts for variations like "Fast Cloud DL", "FastCloud", etc.
        fast_cloud_pattern = re.compile(r'fast\s*cloud\s*(download|dl)', re.IGNORECASE)
        logs.append("Searching for 'Fast Cloud Download/DL' button text pattern...")
        for i, tag in enumerate(possible_tags_p1):
            tag_text = tag.get_text(strip=True)
            # Uncomment below for very detailed tag checking:
            # logs.append(f"  Checking tag {i} ({tag.name}): '{tag_text[:100]}'")
            if fast_cloud_pattern.search(tag_text):
                fast_cloud_link_tag = tag
                logs.append(f"Success: Found potential primary target: <{tag.name}> with text '{tag_text}'")
                break # Found the first match

        # If Fast Cloud button was found
        if fast_cloud_link_tag:
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
            # fetch_headers_p2.update(session.headers) # Headers are already in session
            response2 = session.get(second_page_url, timeout=REQUEST_TIMEOUT, headers=fetch_headers_p2, allow_redirects=True) # Follow redirects
            response2.raise_for_status()
            page2_url = response2.url # Update URL after potential redirects
            logs.append(f"Landed on second page: {page2_url} (Status: {response2.status_code})")

            # --- Added HTML Logging for Page 2 ---
            html_content_p2 = response2.text
            logs.append(f"--- Page 2 HTML Content Snippet (URL: {page2_url}) ---")
            logs.append(html_content_p2[:2000] + ('...' if len(html_content_p2) > 2000 else ''))
            logs.append(f"--- End Page 2 HTML Snippet ---")
            if "cloudflare" in html_content_p2.lower() or "checking your browser" in html_content_p2.lower():
                 logs.append("WARNING: Potential Cloudflare challenge page detected on Page 2!")
            # --- End HTML Logging ---

            soup2 = BeautifulSoup(html_content_p2, PARSER)
            possible_tags_p2 = soup2.find_all(['a', 'button'])
            logs.append(f"Found {len(possible_tags_p2)} potential link/button tags on page 2.")

            # Step 6: Find "Cloud Resume Download" button directly
            resume_link_tag = None
            resume_text_pattern = re.compile(r'cloud\s+resume\s+download', re.IGNORECASE)
            logs.append("Searching for 'Cloud Resume Download' button text pattern...")
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

                final_download_link = urljoin(page2_url, final_link_href) # Use page2_url as base
                logs.append(f"Success: Found final Cloud Resume link URL directly: {final_download_link}")
                return final_download_link, logs

            # Step 6b: If not found directly, check for "Generate Cloud Link" button
            else:
                logs.append("Info: 'Cloud Resume Download' not found directly. Checking for 'Generate Cloud Link' button...")
                generate_tag = None
                # Try finding by common ID first (more reliable)
                generate_tag_by_id = soup2.find('button', id='cloud')
                if generate_tag_by_id:
                    generate_tag = generate_tag_by_id
                    logs.append("Found 'Generate Cloud Link' button by ID 'cloud'.")
                else:
                    # Fallback to text search
                    generate_pattern = re.compile(r'generate\s+cloud\s+link', re.IGNORECASE)
                    logs.append("Searching for 'Generate Cloud Link' button text pattern...")
                    for tag in possible_tags_p2:
                        tag_text = tag.get_text(strip=True)
                        if generate_pattern.search(tag_text):
                            generate_tag = tag
                            logs.append(f"Found 'Generate Cloud Link' button by text: <{tag.name}> with text '{tag_text}'")
                            break

                # If Generate button is found, mimic the POST request
                if generate_tag:
                    logs.append("Info: Attempting to mimic the JavaScript POST request for generation...")

                    # Data might vary, inspect network tab in browser if this fails
                    # Common keys: 'action', 'key', 'action_token', sometimes '_token' or others
                    # Try to find hidden inputs in the form containing the button
                    post_data = {}
                    parent_form = generate_tag.find_parent('form')
                    if parent_form:
                        hidden_inputs = parent_form.find_all('input', {'type': 'hidden'})
                        for input_tag in hidden_inputs:
                            name = input_tag.get('name')
                            value = input_tag.get('value', '') # Default to empty string if no value
                            if name:
                                post_data[name] = value
                                logs.append(f"  Found hidden input: name='{name}', value='{value}'")

                    # Add common default values if not found in form (these might need adjustment)
                    if 'action' not in post_data: post_data['action'] = 'cloud'
                    if 'key' not in post_data: post_data['key'] = '08df4425e31c4330a1a0a3cefc45c19e84d0a192' # Example key, likely dynamic
                    if 'action_token' not in post_data: post_data['action_token'] = '' # Often empty or generated

                    logs.append(f"POST Data to send: {post_data}")

                    parsed_uri = urlparse(page2_url); hostname = parsed_uri.netloc
                    post_headers = {
                        'x-token': hostname,
                        'Referer': page2_url, # Referer is the current page
                        'Accept': 'application/json, text/javascript, */*; q=0.01', # Mimic XHR request
                        'X-Requested-With': 'XMLHttpRequest' # Often required for AJAX endpoints
                        }
                    # post_headers.update(session.headers) # Base headers already in session

                    logs.append(f"Info: Sending POST request to {page2_url}...")
                    page3_url = None # URL to poll
                    try:
                        post_response = session.post(page2_url, data=post_data, headers=post_headers, timeout=REQUEST_TIMEOUT)
                        logs.append(f"Info: POST response Status Code: {post_response.status_code}")
                        post_response.raise_for_status() # Check for HTTP errors on POST

                        try:
                            response_data = post_response.json()
                            logs.append(f"Info: POST response JSON: {json.dumps(response_data)}") # Log the full JSON
                            # Check multiple possible keys for the URL to poll/visit
                            if isinstance(response_data, dict):
                                if response_data.get('visit_url'): page3_url = urljoin(page2_url, response_data['visit_url'])
                                elif response_data.get('url'): page3_url = urljoin(page2_url, response_data['url'])
                                elif response_data.get('error'):
                                    error_msg = response_data.get('message', 'Unknown error from POST response')
                                    logs.append(f"Error from POST request JSON: {error_msg}")
                                    return None, logs
                                else:
                                     logs.append("Warning: POST response JSON received, but no known URL key ('visit_url', 'url') found.")
                                     # Attempt to find any URL-like string in the response values
                                     for key, value in response_data.items():
                                         if isinstance(value, str) and ('http://' in value or 'https://' in value or '/' in value):
                                             potential_url = urljoin(page2_url, value)
                                             if urlparse(potential_url).netloc: # Basic check if it looks like a valid URL
                                                 logs.append(f"Info: Found potential fallback URL in JSON value: {potential_url} (from key '{key}')")
                                                 page3_url = potential_url
                                                 break # Use the first one found

                            else:
                                logs.append(f"Error: POST response was JSON but not a dictionary: {response_data}")
                                return None, logs

                            if page3_url:
                                logs.append(f"Info: POST successful. Will poll/visit new URL: {page3_url}")
                            else:
                                logs.append("Error: POST response JSON did not contain a recognizable URL to proceed.")
                                return None, logs


                        except json.JSONDecodeError:
                            logs.append(f"Error: Failed to decode JSON response from POST. Status: {post_response.status_code}")
                            post_text_snippet = post_response.text[:500]
                            logs.append(f"Response text snippet: {post_text_snippet}")
                            if "cloudflare" in post_text_snippet.lower() or "captcha" in post_text_snippet.lower():
                                logs.append("Hint: Cloudflare/Captcha challenge likely blocked the POST request.")
                            return None, logs
                        except requests.exceptions.HTTPError as post_http_err:
                            logs.append(f"Error: HTTP Error during POST request: {post_http_err}")
                            logs.append(f"POST Response text snippet: {post_response.text[:500]}")
                            return None, logs

                    except requests.exceptions.RequestException as post_err:
                        logs.append(f"Error: Network or other error during POST request: {post_err}")
                        return None, logs

                    # If POST was successful and we have page3_url, START POLLING/VISITING
                    if page3_url:
                        logs.append(f"Info: Starting polling/checking loop for {page3_url}...")
                        start_time = time.time()
                        final_download_link = None # Reset before loop

                        while time.time() - start_time < GENERATION_TIMEOUT:
                            elapsed = time.time() - start_time
                            logs.append(f"Info: Checking {page3_url} (Elapsed: {elapsed:.1f}s / {GENERATION_TIMEOUT}s)")
                            try:
                                # Use page2_url as referer initially? Or page3_url? Let's try page3_url.
                                poll_headers = {'Referer': page3_url}
                                # poll_headers.update(session.headers) # Session headers are included automatically
                                poll_response = session.get(page3_url, timeout=REQUEST_TIMEOUT, headers=poll_headers, allow_redirects=True) # Follow redirects
                                poll_landed_url = poll_response.url # URL after potential redirects on poll
                                logs.append(f"  Poll landed on: {poll_landed_url} (Status: {poll_response.status_code})")

                                # Check status code only after trying to parse, some sites show button even on non-200
                                # if poll_response.status_code != 200:
                                #    logs.append(f"  Warning: Polling status {poll_response.status_code}. Will continue check.")

                                poll_soup = BeautifulSoup(poll_response.text, PARSER)
                                polled_resume_tag = None
                                logs.append("  Searching for 'Cloud Resume Download' on polled page...")
                                for tag in poll_soup.find_all(['a', 'button']):
                                    tag_text = tag.get_text(strip=True)
                                    if resume_text_pattern.search(tag_text):
                                        polled_resume_tag = tag
                                        logs.append(f"  Success: Found 'Cloud Resume Download' after polling/visiting! <{tag.name}> text='{tag_text}'")
                                        break

                                if polled_resume_tag:
                                    final_link_href = polled_resume_tag.get('href')
                                    if not final_link_href and polled_resume_tag.name == 'button':
                                        parent_form = polled_resume_tag.find_parent('form')
                                        if parent_form: final_link_href = parent_form.get('action')

                                    if not final_link_href:
                                        logs.append("  Error: Found polled 'Cloud Resume' element but no href/action.")
                                        # Don't return None immediately, maybe it appears later? Let timeout handle it.
                                        # return None, logs # Or maybe break the loop? Let's continue polling for now.
                                    else:
                                        # Use the URL where the resume button was ACTUALLY found as base
                                        final_download_link = urljoin(poll_landed_url, final_link_href)
                                        logs.append(f"Success: Found final Cloud Resume link URL after polling: {final_download_link}")
                                        return final_download_link, logs # <<< SUCCESS AFTER POLLING >>>

                            except requests.exceptions.RequestException as poll_err:
                                logs.append(f"  Warning: Error during polling request: {poll_err}. Will retry.")
                            except Exception as parse_err:
                                logs.append(f"  Warning: Error parsing polled page: {parse_err}. Will retry.")

                            # If button not found or error occurred, wait and loop
                            wait_time = min(POLL_INTERVAL, GENERATION_TIMEOUT - (time.time() - start_time))
                            if wait_time <= 0:
                                break # Exit loop if timeout reached
                            logs.append(f"  Button not found yet. Waiting {wait_time:.1f}s before next check...")
                            time.sleep(wait_time)
                            # Loop continues

                        # Polling Timeout Reached
                        logs.append(f"Error: Link generation timed out after {GENERATION_TIMEOUT}s. Final button not found at {page3_url}.")
                        return None, logs

                    # else: page3_url was None after POST, error already logged and returned None above

                else: # Generate button wasn't found at all on page 2
                    logs.append("Error: Neither 'Cloud Resume Download' nor 'Generate Cloud Link' button/pattern found on the second page.")
                    body_tag_p2 = soup2.find('body')
                    logs.append(f"Body snippet (page 2):\n{str(body_tag_p2)[:1000]}" if body_tag_p2 else response2.text[:1000])
                    return None, logs

        # Step 3b: Fallback - PixeldrainDL on page 1 (if Fast Cloud wasn't found initially)
        else:
            logs.append("Info: 'Fast Cloud Download' button/pattern not found on first page. Checking for 'PixeldrainDL'...")
            pixeldrain_link_tag = None
            pixeldrain_pattern = re.compile(r'pixeldrain\s*(dl)?', re.IGNORECASE) # Match "Pixeldrain" or "Pixeldrain DL"
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
                    pixeldrain_full_url = urljoin(page1_url, pixeldrain_href) # Use page1_url as base
                    logs.append(f"Success: Found Pixeldrain link URL: {pixeldrain_full_url}")
                    # Assuming pixeldrain link is the final link for this path
                    return pixeldrain_full_url, logs
                else:
                    logs.append(f"Error: Found Pixeldrain element '{pixeldrain_link_tag.get_text(strip=True)}' but couldn't get href/action.")
                    return None, logs # Explicit fail if href missing

            # If Pixeldrain also fails or not found
            logs.append("Error: Neither 'Fast Cloud Download/DL' nor 'Pixeldrain(DL)' link/button found or processed successfully on the first page.")
            # Body snippet already logged earlier if Fast Cloud failed
            return None, logs

    except requests.exceptions.Timeout as e:
        logs.append(f"Error: Request timed out: {e}")
        return None, logs
    except requests.exceptions.HTTPError as e:
        logs.append(f"Error: HTTP Error fetching page: {e.response.status_code} {e.response.reason} for {e.request.url}")
        try:
            logs.append(f"  Response text snippet: {e.response.text[:500]}")
        except Exception:
            logs.append("  Could not read response text.")
        return None, logs
    except requests.exceptions.RequestException as e:
        logs.append(f"Error: Network or Request error: {e}")
        return None, logs
    except Exception as e:
        logs.append(f"FATAL: An unexpected error occurred in get_gdflix_download_link: {e}\n{traceback.format_exc()}")
        return None, logs

# --- CORS Helper Functions ---
def _build_cors_preflight_response():
    response = make_response()
    # Allow requests from any origin for development/testing.
    # For production, restrict this to your frontend's domain.
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type")
    response.headers.add("Access-Control-Allow-Methods", "POST, OPTIONS")
    return response

def _corsify_actual_response(response):
    # Allow requests from any origin for development/testing.
    # For production, restrict this to your frontend's domain.
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response

# --- Flask API Endpoint ---
@app.route('/api/gdflix', methods=['POST', 'OPTIONS'])
def gdflix_bypass_api():
    if request.method == 'OPTIONS':
        # Handle CORS preflight request
        return _build_cors_preflight_response()

    elif request.method == 'POST':
        script_logs = [] # Initialize logs for this request
        result = {"success": False, "error": "Request processing failed", "finalUrl": None, "logs": script_logs}
        status_code = 500 # Default to internal server error, change on success or known failure

        try:
            # Get JSON data
            gdflix_url = None
            try:
                data = request.get_json()
                if not data:
                    raise ValueError("No JSON data received")
                gdflix_url = data.get('gdflixUrl') # Key used by frontend
                if gdflix_url:
                    script_logs.append(f"Received JSON POST body with gdflixUrl: {gdflix_url}")
                else:
                    raise ValueError("Missing 'gdflixUrl' key in JSON data")
            except Exception as e:
                script_logs.append(f"Error: Could not parse JSON request body or missing key: {e}")
                result["error"] = "Invalid or missing JSON in request body (expected {'gdflixUrl': '...' })"
                status_code = 400 # Bad Request
                # Need to return here, can't proceed without URL
                result["logs"] = script_logs
                return _corsify_actual_response(jsonify(result)), status_code

            # Validate URL format (basic check)
            try:
                parsed_start_url = urlparse(gdflix_url)
                if not parsed_start_url.scheme or not parsed_start_url.netloc:
                    raise ValueError("Invalid URL scheme or netloc")
                script_logs.append(f"URL format appears valid: {gdflix_url}")
            except (ValueError, TypeError) as e:
                script_logs.append(f"Error: Invalid URL format: {gdflix_url} ({e})")
                result["error"] = f"Invalid URL format provided: {gdflix_url}"
                status_code = 400 # Bad Request
                result["logs"] = script_logs
                return _corsify_actual_response(jsonify(result)), status_code

            # --- Perform Scraping ---
            script_logs.append(f"Starting GDFLIX bypass process for: {gdflix_url}")
            final_download_link, script_logs_from_func = get_gdflix_download_link(gdflix_url)
            script_logs.extend(script_logs_from_func) # Add logs from the scraping function

            # --- Prepare Response based on script output ---
            if final_download_link:
                script_logs.append("Bypass process completed successfully.")
                result["success"] = True
                result["finalUrl"] = final_download_link
                result["error"] = None
                status_code = 200 # OK
            else:
                # Scraping function returned None, indicating failure
                script_logs.append("Bypass process failed to find the final download link.")
                result["success"] = False
                # Extract the most relevant error from logs
                failure_indicators = ["Error:", "FATAL:", "FAILED", "Could not find", "timed out", "generating", "neither", "blocked"]
                extracted_error = "GDFLIX Extraction Failed (Check logs for details)" # Default error
                for log_entry in reversed(script_logs): # Check recent logs first
                    log_entry_lower = log_entry.lower() # Case-insensitive check
                    if any(indicator.lower() in log_entry_lower for indicator in failure_indicators):
                         # Try to split by common prefixes like 'Error:', 'Info:', 'Warning:'
                         parts = re.split(r'(?:Error|FATAL|Info|Warning):\s*', log_entry, maxsplit=1, flags=re.IGNORECASE)
                         if len(parts) > 1:
                             extracted_error = parts[-1].strip() # Take the part after the prefix
                         else:
                             extracted_error = log_entry.strip() # Otherwise take the whole line
                         break # Stop after finding the first likely error indicator
                result["error"] = extracted_error[:250] # Limit error message length

                # <<< CORRECTED STATUS CODE FOR SCRAPING FAILURE >>>
                # The API endpoint itself worked, but the scraping logic failed predictably.
                # Return 200 OK and let the client check the 'success' flag in the JSON.
                status_code = 200
                # Alternatively, use 422 Unprocessable Entity if the URL was valid
                # but the server couldn't process the bypass instructions for it.
                # status_code = 422

        except Exception as e:
            # Catch unexpected errors in the Flask handler itself (e.g., code errors here)
            print(f"FATAL API Handler Error: {e}", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr) # Print full traceback to server logs
            script_logs.append(f"FATAL API Handler Error: An unexpected server error occurred.")
            # Avoid sending detailed traceback to client for security
            result["success"] = False
            result["error"] = "Internal server error processing the request."
            status_code = 500 # Use 500 only for these unexpected server errors

        finally:
            # Ensure logs are always included in the final result and send response
            result["logs"] = script_logs
            return _corsify_actual_response(jsonify(result)), status_code
    else:
        # Method Not Allowed for other HTTP methods like GET, PUT, DELETE
        return jsonify({"error": "Method Not Allowed"}), 405

# --- Run Flask App (for local testing or basic deployment) ---
if __name__ == '__main__':
    # Render typically uses a production server like Gunicorn and sets the PORT env var.
    # This block is mainly for local development.
    # Use 'flask run --host=0.0.0.0 --port=5001' or similar in terminal.
    # The host='0.0.0.0' makes it accessible on your network.
    # debug=True provides auto-reload and more detailed errors during development.
    # DO NOT run with debug=True in production!
    app.run(host='0.0.0.0', port=5001, debug=True)
