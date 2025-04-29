# gdflix_api/app.py

import requests
import cloudscraper # Enabled: Import cloudscraper
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
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36', # Updated UA
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://google.com' # Generic referer
}
GENERATION_TIMEOUT = 45 # Increased timeout slightly
POLL_INTERVAL = 5
REQUEST_TIMEOUT = 35 # Increased timeout slightly
MAX_REDIRECT_HOPS = 5

# --- Core GDFLIX Bypass Function (with Redirect Loop & Cloudscraper) ---
def get_gdflix_download_link(start_url):
    # --- Session Setup ---
    # Option 1: Standard requests (Commented out)
    # session = requests.Session()
    # session.headers.update(HEADERS)

    # Option 2: Cloudscraper (Enabled)
    try:
        # Create a Cloudscraper instance
        scraper = cloudscraper.create_scraper(
             browser={
                 'browser': 'chrome', # Mimic Chrome
                 'platform': 'windows', # Or 'linux' if your server is Linux
                 'mobile': False,
                 'desktop': True,
             },
             delay=5 # Add a small delay between requests (helps avoid rate limiting)
        )
        # Update scraper's headers with our base headers
        scraper.headers.update(HEADERS)
        # Use the scraper instance like a requests.Session object
        session = scraper
        print("Using cloudscraper session.", file=sys.stderr)
    except NameError:
        # Fallback if cloudscraper isn't installed (shouldn't happen if requirements met)
        print("Error: cloudscraper not imported or installed! Falling back to standard requests.", file=sys.stderr)
        session = requests.Session()
        session.headers.update(HEADERS)
    # --- End Session Setup ---

    logs = []
    current_url = start_url
    hops_count = 0
    landed_url = None
    html_content = None

    try:
        # --- Loop to follow HTTP and secondary redirects ---
        while hops_count < MAX_REDIRECT_HOPS:
            logs.append(f"[Hop {hops_count}] Fetching/Checking URL: {current_url}")

            try:
                # Use session (which is now cloudscraper)
                response = session.get(current_url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
                response.raise_for_status() # Check for HTTP errors
            except requests.exceptions.RequestException as e:
                 # Cloudscraper might raise specific errors too, handle generally
                 logs.append(f"  Error fetching {current_url}: {e}")
                 # Check if it's a Cloudflare challenge error Cloudscraper couldn't solve
                 if "Cloudflare protection" in str(e) or "JS challenge" in str(e):
                      logs.append("  Hint: Cloudscraper failed to solve Cloudflare challenge.")
                 return None, logs

            landed_url = response.url
            html_content = response.text
            status_code = response.status_code
            logs.append(f"  Landed on: {landed_url} (Status: {status_code})")

            # --- Check for secondary redirects in HTML ---
            next_hop_url = None
            is_secondary_redirect = False
            # (Meta Refresh and JS location.replace checks remain the same)
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

            # --- Follow secondary redirect or break loop ---
            if is_secondary_redirect and next_hop_url:
                logs.append(f"  Following secondary redirect...")
                current_url = next_hop_url
                hops_count += 1
                time.sleep(session.delay if hasattr(session, 'delay') else 1) # Use cloudscraper delay or default
            else:
                logs.append(f"  No further actionable secondary redirect found. Proceeding with content analysis.")
                break # Exit redirect loop

        # --- Check for max hops ---
        if hops_count >= MAX_REDIRECT_HOPS:
            logs.append(f"Error: Exceeded maximum redirect hops ({MAX_REDIRECT_HOPS}). Stuck at {landed_url}")
            return None, logs

        # --- Check if final content retrieved ---
        if not landed_url or not html_content:
             logs.append("Error: Failed to retrieve final page content after redirect checks.")
             return None, logs

        page1_url = landed_url # Final base URL

        logs.append(f"--- Final Content Page HTML Snippet (URL: {page1_url}) ---")
        logs.append(html_content[:3000] + ('...' if len(html_content) > 3000 else ''))
        logs.append(f"--- End Final Content Page HTML Snippet ---")

        # Cloudflare warning logic remains useful
        if "cloudflare" in html_content.lower() or "checking your browser" in html_content.lower() or "challenge-platform" in html_content.lower():
             logs.append("Info: Cloudflare keywords detected on final content page. (Cloudscraper may have handled it)")

        # --- Parse FINAL HTML ---
        soup1 = BeautifulSoup(html_content, PARSER)
        possible_tags_p1 = soup1.find_all(['a', 'button'])
        logs.append(f"Found {len(possible_tags_p1)} potential link/button tags on final content page ({page1_url}).")

        # --- Step 3: Find Fast Cloud Download button on FINAL page ---
        fast_cloud_link_tag = None
        fast_cloud_pattern = re.compile(r'fast\s*cloud\s*(download|dl)', re.IGNORECASE)
        logs.append("Searching for 'Fast Cloud Download/DL' button text pattern on final content page...")
        # (Search loop remains the same)
        for i, tag in enumerate(possible_tags_p1):
            tag_text = tag.get_text(strip=True)
            if fast_cloud_pattern.search(tag_text):
                fast_cloud_link_tag = tag
                logs.append(f"Success: Found potential primary target: <{tag.name}> with text '{tag_text}'")
                break

        # --- If Fast Cloud button WAS found ---
        if fast_cloud_link_tag:
            # Get intermediate page URL (page 2)
            fast_cloud_href = fast_cloud_link_tag.get('href')
            # (Logic to get href from form action if needed remains the same)
            if not fast_cloud_href and fast_cloud_link_tag.name == 'button':
                 parent_form = fast_cloud_link_tag.find_parent('form')
                 if parent_form: fast_cloud_href = parent_form.get('action')

            if not fast_cloud_href:
                logs.append(f"Error: Found '{fast_cloud_link_tag.get_text(strip=True)}' element but couldn't get href/action.")
                return None, logs

            intermediate_url = urljoin(page1_url, fast_cloud_href)
            logs.append(f"Found intermediate link URL (from Fast Cloud button): {intermediate_url}")
            time.sleep(1)

            # Fetch intermediate page (page 2)
            logs.append(f"Fetching intermediate page URL (potentially with Generate button): {intermediate_url}")
            fetch_headers_p2 = {'Referer': page1_url}
            try:
                # Use session (cloudscraper)
                response_intermediate = session.get(intermediate_url, timeout=REQUEST_TIMEOUT, headers=fetch_headers_p2, allow_redirects=True)
                response_intermediate.raise_for_status()
            except requests.exceptions.RequestException as e:
                logs.append(f"  Error fetching intermediate page {intermediate_url}: {e}")
                if "Cloudflare protection" in str(e) or "JS challenge" in str(e):
                    logs.append("  Hint: Cloudscraper failed to solve Cloudflare challenge on intermediate page.")
                return None, logs

            page2_url = response_intermediate.url
            html_content_p2 = response_intermediate.text
            logs.append(f"Landed on intermediate page: {page2_url} (Status: {response_intermediate.status_code})")

            # Log intermediate page HTML snippet
            logs.append(f"--- Intermediate Page HTML Content Snippet (URL: {page2_url}) ---")
            logs.append(html_content_p2[:2000] + ('...' if len(html_content_p2) > 2000 else ''))
            logs.append(f"--- End Intermediate Page HTML Snippet ---")
            if "cloudflare" in html_content_p2.lower() or "checking your browser" in html_content_p2.lower():
                 logs.append("Info: Cloudflare keywords detected on intermediate page. (Cloudscraper may have handled it)")

            # Parse intermediate page (page 2)
            soup2 = BeautifulSoup(html_content_p2, PARSER)
            possible_tags_p2 = soup2.find_all(['a', 'button'])
            logs.append(f"Found {len(possible_tags_p2)} potential link/button tags on intermediate page.")

            # --- Step 6: Find "Cloud Resume" or "Generate" on intermediate page ---
            resume_link_tag = None
            resume_text_pattern = re.compile(r'cloud\s+resume\s+download', re.IGNORECASE)
            logs.append("Searching for 'Cloud Resume Download' button text pattern on intermediate page...")
            # (Search loop remains the same)
            for tag in possible_tags_p2:
                 tag_text = tag.get_text(strip=True)
                 if resume_text_pattern.search(tag_text):
                    resume_link_tag = tag
                    logs.append(f"Success: Found final link tag directly: <{tag.name}> with text '{tag_text}'")
                    break

            # Step 6a: If Resume found directly
            if resume_link_tag:
                # (Logic to get href/action and return final link remains the same)
                final_link_href = resume_link_tag.get('href')
                if not final_link_href and resume_link_tag.name == 'button':
                     parent_form = resume_link_tag.find_parent('form')
                     if parent_form: final_link_href = parent_form.get('action')
                if not final_link_href:
                    logs.append(f"Error: Found '{resume_link_tag.get_text(strip=True)}' but no href/action.")
                    return None, logs
                final_download_link = urljoin(page2_url, final_link_href) # Base is intermediate URL
                logs.append(f"Success: Found final Cloud Resume link URL directly: {final_download_link}")
                return final_download_link, logs

            # Step 6b: If Resume not found, look for Generate button
            else:
                logs.append("Info: 'Cloud Resume Download' not found directly. Checking for 'Generate Cloud Link' button...")
                generate_tag = None
                generate_tag_by_id = soup2.find('button', id='cloud')
                if generate_tag_by_id:
                     generate_tag = generate_tag_by_id
                     logs.append("Found 'Generate Cloud Link' button by ID 'cloud'.")
                else:
                    generate_pattern = re.compile(r'generate\s+cloud\s+link', re.IGNORECASE)
                    logs.append("Searching for 'Generate Cloud Link' button text pattern...")
                    for tag in possible_tags_p2:
                        if generate_pattern.search(tag.get_text(strip=True)):
                            generate_tag = tag
                            logs.append(f"Found 'Generate Cloud Link' button by text: <{tag.name}>...")
                            break

                # If Generate button found -> POST -> Poll
                if generate_tag:
                    logs.append("Info: Attempting to mimic the JavaScript POST request for generation...")
                    # (Extract post_data logic remains the same)
                    post_data = {}
                    parent_form = generate_tag.find_parent('form')
                    # ... find hidden inputs ...
                    if parent_form:
                         hidden_inputs = parent_form.find_all('input', {'type': 'hidden'})
                         for input_tag in hidden_inputs:
                             name = input_tag.get('name')
                             value = input_tag.get('value', '')
                             if name:
                                 post_data[name] = value
                                 logs.append(f"  Found hidden input: name='{name}', value='{value}'")
                    # ... add defaults if needed ...
                    if 'action' not in post_data: post_data['action'] = 'cloud'
                    # Key might change, check browser dev tools if POST fails
                    # if 'key' not in post_data: post_data['key'] = '...'
                    # if 'action_token' not in post_data: post_data['action_token'] = ''

                    logs.append(f"POST Data to send: {post_data}")
                    parsed_uri = urlparse(page2_url); hostname = parsed_uri.netloc
                    post_headers = { # Headers for XHR request
                        'x-token': hostname,
                        'Referer': page2_url,
                        'Accept': 'application/json, text/javascript, */*; q=0.01',
                        'X-Requested-With': 'XMLHttpRequest'
                        }

                    logs.append(f"Info: Sending POST request to {page2_url}...")
                    page3_url = None # URL to poll
                    try:
                        # Use session (cloudscraper) for POST
                        post_response = session.post(page2_url, data=post_data, headers=post_headers, timeout=REQUEST_TIMEOUT)
                        logs.append(f"Info: POST response Status Code: {post_response.status_code}")
                        post_response.raise_for_status() # Check POST status

                        # (JSON handling logic remains the same)
                        try:
                            response_data = post_response.json()
                            # ... check for visit_url, url, error ...
                            if isinstance(response_data, dict):
                                if response_data.get('visit_url'): page3_url = urljoin(page2_url, response_data['visit_url'])
                                elif response_data.get('url'): page3_url = urljoin(page2_url, response_data['url'])
                                # ... error handling ...
                                elif response_data.get('error'):
                                    # ... log error ...
                                    return None, logs
                                # ... fallback url finding ...
                                else: # No known keys, try finding any value that looks like a relative/absolute URL
                                    for value in response_data.values():
                                        if isinstance(value, str) and (value.startswith('/') or '://' in value):
                                            potential_url = urljoin(page2_url, value)
                                            if urlparse(potential_url).netloc:
                                                logs.append(f"Info: Found potential fallback URL in JSON: {potential_url}")
                                                page3_url = potential_url
                                                break
                            else: # JSON not a dict
                                logs.append(f"Error: POST response JSON was not a dictionary: {response_data}")
                                return None, logs

                            if page3_url: logs.append(f"Info: POST successful. Will poll/visit new URL: {page3_url}")
                            else:
                                logs.append("Error: POST response did not contain a recognizable URL.")
                                return None, logs

                        except json.JSONDecodeError:
                            # ... log JSON decode error ...
                            return None, logs
                        except requests.exceptions.HTTPError as post_http_err:
                             # ... log HTTP error ...
                             return None, logs

                    except requests.exceptions.RequestException as post_err:
                        # ... log post network error ...
                         if "Cloudflare protection" in str(post_err) or "JS challenge" in str(post_err):
                              logs.append("  Hint: Cloudscraper failed to solve Cloudflare challenge on POST request.")
                         return None, logs

                    # Start POLLING if POST was successful
                    if page3_url:
                        logs.append(f"Info: Starting polling/checking loop for {page3_url}...")
                        start_time = time.time()
                        # (Polling loop remains the same, using session.get)
                        while time.time() - start_time < GENERATION_TIMEOUT:
                            logs.append(f"Info: Checking {page3_url} (Elapsed: {time.time() - start_time:.1f}s / {GENERATION_TIMEOUT}s)")
                            try:
                                poll_headers = {'Referer': page3_url} # Referer is the polling page itself
                                poll_response = session.get(page3_url, timeout=REQUEST_TIMEOUT, headers=poll_headers, allow_redirects=True)
                                poll_landed_url = poll_response.url
                                # ... check poll status ...
                                poll_soup = BeautifulSoup(poll_response.text, PARSER)
                                # ... search for resume button ...
                                if polled_resume_tag:
                                    # ... get href, urljoin with poll_landed_url ...
                                    final_download_link = urljoin(poll_landed_url, final_link_href)
                                    # ... return success ...
                                    logs.append(f"Success: Found final Cloud Resume link URL after polling: {final_download_link}")
                                    return final_download_link, logs
                            # ... handle poll exceptions ...
                            except requests.exceptions.RequestException as poll_err:
                                 logs.append(f"  Warning: Error during polling request: {poll_err}. Will retry.")
                                 if "Cloudflare protection" in str(poll_err):
                                      logs.append("  Hint: Cloudscraper failed on polling request.")
                            # ... wait POLL_INTERVAL ...
                            wait_time = min(POLL_INTERVAL, GENERATION_TIMEOUT - (time.time() - start_time))
                            if wait_time <= 0: break
                            logs.append(f"  Button not found yet. Waiting {wait_time:.1f}s...")
                            time.sleep(wait_time)

                        # Polling Timeout
                        logs.append(f"Error: Link generation timed out after {GENERATION_TIMEOUT}s.")
                        return None, logs
                    # else: page3_url was None after POST

                else: # Generate button wasn't found on intermediate page
                    logs.append("Error: Neither 'Cloud Resume Download' nor 'Generate Cloud Link' button/pattern found on the intermediate page.")
                    return None, logs

        # --- Fallback: If Fast Cloud button was NOT found on final page ---
        else:
            logs.append("Info: 'Fast Cloud Download' button/pattern not found on final content page. Checking for 'PixeldrainDL'...")
            # (Pixeldrain logic remains the same, operating on soup1/page1_url)
            pixeldrain_link_tag = None
            pixeldrain_pattern = re.compile(r'pixeldrain\s*(dl)?', re.IGNORECASE)
            # ... search loop ...
            if pixeldrain_link_tag:
                 # ... get href ...
                 if pixeldrain_href:
                      pixeldrain_full_url = urljoin(page1_url, pixeldrain_href)
                      logs.append(f"Success: Found Pixeldrain link URL: {pixeldrain_full_url}")
                      return pixeldrain_full_url, logs
                 else: # href missing
                      logs.append(f"Error: Found Pixeldrain element but couldn't get href/action.")
                      return None, logs
            else: # Pixeldrain not found either
                logs.append("Error: Neither 'Fast Cloud Download/DL' nor 'Pixeldrain(DL)' link/button found on the final content page.")
                return None, logs

    # --- Exception Handling ---
    # (General exception handling remains the same)
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
        if "Cloudflare protection" in str(e) or "JS challenge" in str(e):
            logs.append("  Hint: Cloudscraper may have failed the challenge.")
        return None, logs
    except Exception as e:
        logs.append(f"FATAL: An unexpected error occurred in get_gdflix_download_link: {e}\n{traceback.format_exc()}")
        return None, logs

    # Fallback if no successful path found
    logs.append("Error: Reached end of function without finding a download link.")
    return None, logs


# --- Flask API Endpoint ---
# (This remains identical to the previous version - no changes needed here)
@app.route('/api/gdflix', methods=['POST'])
def gdflix_bypass_api():
    script_logs = []
    result = {"success": False, "error": "Request processing failed", "finalUrl": None, "logs": script_logs}
    status_code = 500 # Default

    try:
        # Get JSON data & validate URL
        gdflix_url = None
        try:
            data = request.get_json()
            # ... validation ...
        except Exception as e:
            # ... handle bad request ...
            return jsonify(result), status_code # Return early

        # Perform Scraping using the updated function
        script_logs.append(f"Starting GDFLIX bypass process for: {gdflix_url}")
        final_download_link, script_logs_from_func = get_gdflix_download_link(gdflix_url)
        script_logs.extend(script_logs_from_func)

        # Prepare Response
        if final_download_link:
            # ... success case ...
            status_code = 200
        else:
            # ... failure case, extract error, set status_code = 200 ...
            status_code = 200 # Or 422

    except Exception as e:
        # ... handle unexpected handler error, status_code = 500 ...
        status_code = 500

    finally:
        result["logs"] = script_logs
        return jsonify(result), status_code

# --- Run Flask App (for local testing) ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True) # Set debug=False in production
