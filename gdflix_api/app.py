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
import os # Added for os.environ.get
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS # Import CORS

# Try importing lxml, fall back to html.parser if not installed
try:
    PARSER = "lxml"
    LXML_AVAILABLE = True
except ImportError:
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

# --- Core GDFLIX Bypass Function (Updated with New Priorities) ---
def get_gdflix_download_link(start_url):
    session = requests.Session()
    session.headers.update(HEADERS)
    logs = []
    current_url = start_url
    hops_count = 0
    landed_url_page1 = None
    html_content_p1 = None

    try:
        # --- Loop to follow HTTP and secondary HTML/JS redirects to get to Page 1 ---
        while hops_count < MAX_REDIRECT_HOPS:
            logs.append(f"[Hop {hops_count}] Fetching/Checking URL: {current_url}")
            try:
                response = session.get(current_url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                logs.append(f"  Error fetching {current_url}: {e}")
                return None, logs

            landed_url_page1 = response.url
            html_content_p1 = response.text
            status_code = response.status_code
            logs.append(f"  Landed on: {landed_url_page1} (Status: {status_code})")

            next_hop_url = None
            is_secondary_redirect = False
            meta_match = re.search(r'<meta\s+http-equiv="refresh"\s+content="[^"]*url=([^"]+)"', html_content_p1, re.IGNORECASE)
            if meta_match:
                extracted_url = meta_match.group(1).strip().split(';')[0]
                potential_next = urljoin(landed_url_page1, extracted_url)
                if potential_next.split('#')[0] != landed_url_page1.split('#')[0]:
                    next_hop_url = potential_next
                    logs.append(f"  Detected META refresh redirect to: {next_hop_url}")
                    is_secondary_redirect = True

            if not is_secondary_redirect:
                js_match = re.search(r"location\.replace\(['\"]([^'\"]+)['\"]", html_content_p1, re.IGNORECASE)
                if js_match:
                    extracted_url = js_match.group(1).strip().split('+document.location.hash')[0].strip("'\" ")
                    potential_next = urljoin(landed_url_page1, extracted_url)
                    if potential_next.split('#')[0] != landed_url_page1.split('#')[0]:
                        next_hop_url = potential_next
                        logs.append(f"  Detected JS location.replace redirect to: {next_hop_url}")
                        is_secondary_redirect = True

            if is_secondary_redirect and next_hop_url:
                logs.append(f"  Following secondary redirect...")
                current_url = next_hop_url
                hops_count += 1
                time.sleep(0.5)
            else:
                logs.append(f"  No further actionable secondary redirect found. Proceeding with Page 1 content analysis.")
                break

        if hops_count >= MAX_REDIRECT_HOPS:
            logs.append(f"Error: Exceeded maximum redirect hops ({MAX_REDIRECT_HOPS}). Stuck at {landed_url_page1}")
            return None, logs

        if not landed_url_page1 or not html_content_p1:
             logs.append("Error: Failed to retrieve final Page 1 content after redirect checks.")
             return None, logs

        page1_url = landed_url_page1
        logs.append(f"--- Final Content Page (Page 1) HTML Snippet (URL: {page1_url}) ---")
        logs.append(html_content_p1[:3000] + ('...' if len(html_content_p1) > 3000 else ''))
        logs.append(f"--- End Final Content Page (Page 1) HTML Snippet ---")

        if "cloudflare" in html_content_p1.lower() or "checking your browser" in html_content_p1.lower() or "challenge-platform" in html_content_p1.lower():
             logs.append("WARNING: Potential Cloudflare challenge page detected on Page 1!")

        soup_p1 = BeautifulSoup(html_content_p1, PARSER)
        possible_tags_p1 = soup_p1.find_all(['a', 'button'])
        logs.append(f"Found {len(possible_tags_p1)} potential link/button tags on Page 1 ({page1_url}).")

        # --- Define patterns ---
        cloud_r2_pattern = re.compile(r'cloud\s+download\s+\[R2\]', re.IGNORECASE)
        fast_cloud_pattern = re.compile(r'fast\s*cloud\s*(download|dl)', re.IGNORECASE)
        pixeldrain_pattern = re.compile(r'pixeldrain\s*(dl)?', re.IGNORECASE)
        resume_text_pattern = re.compile(r'cloud\s+resume\s+download', re.IGNORECASE)
        generate_text_pattern = re.compile(r'generate\s+cloud\s+link', re.IGNORECASE)

        # --- Helper function to process intermediate pages (Page 2) ---
        def process_intermediate_page(intermediate_url, referer_for_intermediate, source_button_text):
            logs.append(f"Fetching intermediate page from '{source_button_text}' (URL: {intermediate_url})")
            fetch_headers_p2 = {'Referer': referer_for_intermediate}
            page2_landed_url = None
            try:
                response_intermediate = session.get(intermediate_url, timeout=REQUEST_TIMEOUT, headers=fetch_headers_p2, allow_redirects=True)
                response_intermediate.raise_for_status()
            except requests.exceptions.RequestException as e:
                logs.append(f"  Error fetching intermediate page {intermediate_url}: {e}")
                return None # Return None for final_dl_link

            page2_landed_url = response_intermediate.url
            html_content_p2 = response_intermediate.text
            logs.append(f"Landed on intermediate page (from {source_button_text}): {page2_landed_url} (Status: {response_intermediate.status_code})")
            logs.append(f"--- Intermediate Page HTML (from {source_button_text}, URL: {page2_landed_url}) ---")
            logs.append(html_content_p2[:2000] + "...")
            logs.append(f"--- End Intermediate Page HTML ---")
            if "cloudflare" in html_content_p2.lower(): logs.append(f"WARNING: Cloudflare on Intermediate Page (from {source_button_text})!")

            soup_p2 = BeautifulSoup(html_content_p2, PARSER)
            possible_tags_p2 = soup_p2.find_all(['a', 'button'])

            # Check for "Cloud Resume Download"
            logs.append(f"Searching for 'Cloud Resume Download' on intermediate page from '{source_button_text}'...")
            for tag in possible_tags_p2:
                if resume_text_pattern.search(tag.get_text(strip=True)):
                    href = tag.get('href')
                    if not href and tag.name == 'button':
                        parent_form = tag.find_parent('form')
                        if parent_form: href = parent_form.get('action')
                    if href:
                        final_link = urljoin(page2_landed_url, href)
                        logs.append(f"Success: Found final 'Cloud Resume Download' link directly: {final_link}")
                        return final_link
                    else:
                        logs.append(f"Error: Found '{tag.get_text(strip=True)}' but no href/action.")
                        # Dont return, check for generate button

            # Check for "Generate Cloud Link"
            logs.append(f"Info: 'Cloud Resume Download' not found directly. Checking for 'Generate Cloud Link' (from '{source_button_text}' path)...")
            generate_tag = soup_p2.find('button', id='cloud')
            if not generate_tag:
                logs.append("  Button id='cloud' not found. Searching by text pattern...")
                for tag in possible_tags_p2:
                    if generate_text_pattern.search(tag.get_text(strip=True)):
                        generate_tag = tag
                        logs.append(f"  Success: Found generate tag by text: <{tag.name}> text '{tag.get_text(strip=True)}'")
                        break
            
            if generate_tag:
                logs.append(f"Found 'Generate Cloud Link' button: <{generate_tag.name}> id='{generate_tag.get('id', 'N/A')}'")
                post_data = {}
                parent_form = generate_tag.find_parent('form')
                if parent_form:
                    for input_tag in parent_form.find_all('input', type='hidden'):
                        name, value = input_tag.get('name'), input_tag.get('value')
                        if name: post_data[name] = value if value is not None else ''
                    btn_name, btn_value = generate_tag.get('name'), generate_tag.get('value')
                    if btn_name and generate_tag.name == 'button': post_data[btn_name] = btn_value if btn_value is not None else ''
                
                # Try to ensure 'action' and 'key' are in post_data, possibly from defaults or button attributes
                if 'action' not in post_data or not post_data.get('action'):
                    post_data['action'] = generate_tag.get('name') or 'cloud' # Use button name or default
                # Example key, script should ideally find this in hidden inputs if it's dynamic
                if 'key' not in post_data and '08df4425e31c4330a1a0a3cefc45c19e84d0a192' not in post_data.values(): # Avoid adding if similar key exists
                     if not any(len(v) == 40 and v.isalnum() for v in post_data.values()): # Avoid if a 40-char hex key is already there
                        pass # Let's rely on form extraction primarily for keys.
                        # post_data['key'] = '08df4425e31c4330a1a0a3cefc45c19e84d0a192' # Default if not found

                logs.append(f"  Final POST data: {post_data}")
                parsed_uri = urlparse(page2_landed_url)
                post_headers = {
                    'Referer': page2_landed_url, 'x-token': parsed_uri.netloc,
                    'Accept': 'application/json, text/javascript, */*; q=0.01',
                    'X-Requested-With': 'XMLHttpRequest'
                }
                page3_poll_url = None
                try:
                    post_response = session.post(page2_landed_url, data=post_data, headers=post_headers, timeout=REQUEST_TIMEOUT)
                    logs.append(f"  POST response status: {post_response.status_code}")
                    content_type = post_response.headers.get('Content-Type', '').lower()
                    response_text = post_response.text
                    
                    extracted_poll_url_flag = False
                    if 'application/json' in content_type:
                        try:
                            resp_data = post_response.json()
                            logs.append(f"  POST JSON response: {resp_data}")
                            if post_response.ok and not resp_data.get('error'):
                                url_key = resp_data.get('visit_url') or resp_data.get('url')
                                if url_key:
                                    page3_poll_url = urljoin(page2_landed_url, url_key)
                                    extracted_poll_url_flag = True
                                else: logs.append("  Error: JSON success but no 'visit_url'/'url'.")
                            elif resp_data.get('error'): logs.append(f"  Error in POST JSON: {resp_data.get('message', 'Unknown')}")
                        except json.JSONDecodeError: logs.append(f"  Error: Failed to decode JSON. Text: {response_text[:200]}")
                    elif post_response.ok: # Try parsing as JSON if status is OK but content-type isn't JSON
                        logs.append(f"  Info: POST Content-Type '{content_type}', not JSON. Status OK. Trying JSON parse...")
                        try:
                            resp_data = json.loads(response_text)
                            logs.append(f"  Parsed non-JSON as JSON: {resp_data}")
                            if not resp_data.get('error'):
                                url_key = resp_data.get('visit_url') or resp_data.get('url')
                                if url_key:
                                    page3_poll_url = urljoin(page2_landed_url, url_key)
                                    extracted_poll_url_flag = True
                                else: logs.append("  Error: Parsed JSON but no 'visit_url'/'url'.")
                            elif resp_data.get('error'): logs.append(f"  Error in parsed JSON: {resp_data.get('message', 'Unknown')}")
                        except json.JSONDecodeError: logs.append(f"  Error: Failed to parse non-JSON as JSON. Text: {response_text[:200]}")
                    else: # POST failed
                        logs.append(f"  Error: POST failed. Status: {post_response.status_code}. Text: {response_text[:200]}")
                        if "cloudflare" in response_text.lower(): logs.append("  Hint: Cloudflare/Captcha likely blocked POST.")

                    if not extracted_poll_url_flag:
                        logs.append("  Error: Could not get polling URL from POST.")
                        return None # Failed to get polling URL
                except requests.exceptions.RequestException as post_err:
                    logs.append(f"  Error during POST network op: {post_err}")
                    return None

                if page3_poll_url:
                    logs.append(f"Starting polling for {page3_poll_url}...")
                    start_time = time.time()
                    while time.time() - start_time < GENERATION_TIMEOUT:
                        time.sleep(POLL_INTERVAL)
                        logs.append(f"  Polling: GET {page3_poll_url}")
                        poll_landed_on = None
                        try:
                            poll_resp = session.get(page3_poll_url, timeout=REQUEST_TIMEOUT, headers={'Referer': page3_poll_url}, allow_redirects=True)
                            poll_landed_on = poll_resp.url
                            logs.append(f"  Polling status {poll_resp.status_code}, landed on {poll_landed_on}")
                            if not poll_resp.ok: continue

                            poll_soup = BeautifulSoup(poll_resp.text, PARSER)
                            for tag_poll in poll_soup.find_all(['a', 'button']):
                                if resume_text_pattern.search(tag_poll.get_text(strip=True)):
                                    href_poll = tag_poll.get('href')
                                    if not href_poll and tag_poll.name == 'button':
                                        form_poll = tag_poll.find_parent('form')
                                        if form_poll: href_poll = form_poll.get('action')
                                    if href_poll:
                                        final_link_poll = urljoin(poll_landed_on, href_poll)
                                        logs.append(f"Success: Found final 'Cloud Resume Download' after polling: {final_link_poll}")
                                        return final_link_poll
                                    else: logs.append(f"  Error: Found polled '{tag_poll.get_text(strip=True)}' but no href.")
                        except requests.exceptions.RequestException as poll_e:
                            logs.append(f"  Warning: Polling error: {poll_e}. Retrying.")
                        except Exception as parse_e:
                            logs.append(f"  Warning: Error parsing polled page {poll_landed_on or page3_poll_url}: {parse_e}. Retrying.")
                    logs.append(f"Error: Link generation timed out polling {page3_poll_url}.")
            else: # No Generate button
                logs.append(f"Info: Neither 'Cloud Resume Download' nor 'Generate Cloud Link' found on intermediate page from '{source_button_text}'.")
            
            return None # If generate path failed or no generate button

        # --- Main Logic: Search buttons on Page 1 by priority ---
        final_download_link = None

        # PRIORITY 1: "CLOUD DOWNLOAD [R2]"
        logs.append("Searching for 'CLOUD DOWNLOAD [R2]' on Page 1...")
        cloud_r2_tag_p1 = None
        for tag_p1 in possible_tags_p1:
            if cloud_r2_pattern.search(tag_p1.get_text(strip=True)):
                cloud_r2_tag_p1 = tag_p1
                logs.append(f"Success: Found 'CLOUD DOWNLOAD [R2]' target: <{tag_p1.name}> text '{tag_p1.get_text(strip=True)}'")
                break
        
        if cloud_r2_tag_p1:
            r2_href = cloud_r2_tag_p1.get('href')
            if not r2_href and cloud_r2_tag_p1.name == 'button':
                parent_form_r2 = cloud_r2_tag_p1.find_parent('form')
                if parent_form_r2: r2_href = parent_form_r2.get('action')
            
            if r2_href:
                r2_intermediate_url = urljoin(page1_url, r2_href)
                logs.append(f"Following 'CLOUD DOWNLOAD [R2]' link to: {r2_intermediate_url}")
                time.sleep(1)
                final_download_link = process_intermediate_page(r2_intermediate_url, page1_url, "CLOUD DOWNLOAD [R2]")
                if final_download_link:
                    return final_download_link, logs
                else:
                    logs.append("Info: 'CLOUD DOWNLOAD [R2]' path did not yield a final link. Proceeding to next priority.")
            else:
                logs.append(f"Error: Found '{cloud_r2_tag_p1.get_text(strip=True)}' but no href/action. Proceeding.")

        # PRIORITY 2: "Fast Cloud Download" (if R2 path failed or R2 button not found)
        if not final_download_link:
            logs.append("Searching for 'Fast Cloud Download/DL' on Page 1...")
            fast_cloud_tag_p1 = None
            for tag_p1 in possible_tags_p1: # Re-check all tags on page 1
                if fast_cloud_pattern.search(tag_p1.get_text(strip=True)):
                    fast_cloud_tag_p1 = tag_p1
                    logs.append(f"Success: Found 'Fast Cloud Download' target: <{tag_p1.name}> text '{tag_p1.get_text(strip=True)}'")
                    break
            
            if fast_cloud_tag_p1:
                fc_href = fast_cloud_tag_p1.get('href')
                if not fc_href and fast_cloud_tag_p1.name == 'button':
                    parent_form_fc = fast_cloud_tag_p1.find_parent('form')
                    if parent_form_fc: fc_href = parent_form_fc.get('action')

                if fc_href:
                    fc_intermediate_url = urljoin(page1_url, fc_href)
                    logs.append(f"Following 'Fast Cloud Download' link to: {fc_intermediate_url}")
                    time.sleep(1)
                    final_download_link = process_intermediate_page(fc_intermediate_url, page1_url, "Fast Cloud Download")
                    if final_download_link:
                        return final_download_link, logs
                    else:
                        logs.append("Info: 'Fast Cloud Download' path did not yield a final link. Proceeding to next priority.")
                else:
                    logs.append(f"Error: Found '{fast_cloud_tag_p1.get_text(strip=True)}' (Fast Cloud) but no href/action. Proceeding.")

        # PRIORITY 3: "Pixeldrain DL" (Fallback if R2 and Fast Cloud paths failed)
        if not final_download_link:
            logs.append("Searching for 'Pixeldrain DL' on Page 1 as fallback...")
            pixeldrain_tag_p1 = None
            for tag_p1 in possible_tags_p1:
                if pixeldrain_pattern.search(tag_p1.get_text(strip=True)):
                    pixeldrain_tag_p1 = tag_p1
                    logs.append(f"Success: Found 'Pixeldrain DL' fallback target: <{tag_p1.name}> text '{tag_p1.get_text(strip=True)}'")
                    break
            
            if pixeldrain_tag_p1:
                pd_href = pixeldrain_tag_p1.get('href')
                if not pd_href and pixeldrain_tag_p1.name == 'button':
                    parent_form_pd = pixeldrain_tag_p1.find_parent('form')
                    if parent_form_pd: pd_href = parent_form_pd.get('action')
                
                if pd_href:
                    final_download_link = urljoin(page1_url, pd_href)
                    logs.append(f"Success: Found Pixeldrain link URL directly: {final_download_link}")
                    return final_download_link, logs
                else:
                    logs.append(f"Error: Found Pixeldrain element but no href/action.")
        
        # If all paths failed
        if not final_download_link:
            logs.append("Error: All prioritized download paths (R2, Fast Cloud, Pixeldrain) failed or buttons not found.")
            return None, logs

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

    logs.append("Error: Reached end of function logic unexpectedly without returning a link or None explicitly from paths.")
    return None, logs


# --- Flask API Endpoint ---
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
            extracted_error = "GDFLIX Extraction Failed (Check logs for details)" 
            timeout_occurred = False

            last_error_log = ""
            for log_entry in reversed(script_logs):
                log_entry_lower = log_entry.lower()
                if "link generation timed out" in log_entry_lower:
                    last_error_log = log_entry
                    timeout_occurred = True
                    break
                elif any(indicator.lower() in log_entry_lower for indicator in failure_indicators):
                    if not last_error_log: # Prioritize timeout, but take first other error if no timeout
                        last_error_log = log_entry
            
            if timeout_occurred:
                extracted_error = "Link is generating, please try again after a few minutes."
                script_logs.append(f"API Info: Reporting timeout as '{extracted_error}'")
            elif last_error_log:
                parts = re.split(r'(?:Error|FATAL|Info|Warning):\s*', last_error_log, maxsplit=1, flags=re.IGNORECASE)
                extracted_error = (parts[-1] if len(parts) > 1 else last_error_log).strip()
                if "Neither 'Cloud Resume Download' nor 'Generate Cloud Link'" in extracted_error:
                     extracted_error = "Could not find required buttons on intermediate page."
                elif "Exceeded maximum redirect hops" in extracted_error:
                       extracted_error = "Too many redirects encountered."
                elif "Failed to obtain polling URL" in extracted_error or "Could not get polling URL" in extracted_error : # Added
                       extracted_error = "Failed to initiate link generation process."
                elif "Failed to retrieve final Page 1 content" in extracted_error: # Modified
                     extracted_error = "Could not load initial page content."
            
            result["error"] = extracted_error[:250]
            status_code = 200 

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
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
