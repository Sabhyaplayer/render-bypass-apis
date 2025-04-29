# gdflix_api/app.py

import requests
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

# --- Configuration (Copied from your script) ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
}
GENERATION_TIMEOUT = 40
POLL_INTERVAL = 5
REQUEST_TIMEOUT = 30 # General request timeout

# --- Core GDFLIX Bypass Function (Copied from your script) ---
def get_gdflix_download_link(start_url):
    session = requests.Session()
    session.headers.update(HEADERS)
    logs = [] # Collect logs for debugging

    try:
        # Step 1 & 2: Fetch and parse page 1
        logs.append(f"Fetching initial URL: {start_url}")
        response1 = session.get(start_url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
        response1.raise_for_status()
        page1_url = response1.url
        logs.append(f"Redirected to: {page1_url}")
        soup1 = BeautifulSoup(response1.text, PARSER)
        possible_tags_p1 = soup1.find_all(['a', 'button'])

        # Step 3: Find Fast Cloud
        fast_cloud_link_tag = None
        fast_cloud_pattern = re.compile(r'fast\s+cloud\s+download', re.IGNORECASE)
        for tag in possible_tags_p1:
            if fast_cloud_pattern.search(tag.get_text(strip=True)):
                fast_cloud_link_tag = tag
                logs.append(f"Found primary target: {tag.name}...")
                break

        # If Fast Cloud Found
        if fast_cloud_link_tag:
            # Steps 3a, 4, 5: Getting to page 2
            fast_cloud_href = fast_cloud_link_tag.get('href')
            if not fast_cloud_href and fast_cloud_link_tag.name == 'button':
                parent_form = fast_cloud_link_tag.find_parent('form')
                if parent_form: fast_cloud_href = parent_form.get('action')
            if not fast_cloud_href:
                logs.append("Error: Found 'Fast Cloud Download' but couldn't get URL.")
                return None, logs

            second_page_url = urljoin(page1_url, fast_cloud_href)
            logs.append(f"Found Fast Cloud link URL: {second_page_url}")
            time.sleep(1)

            logs.append(f"Fetching second page URL (page with Generate button): {second_page_url}")
            fetch_headers_p2 = {'Referer': page1_url}
            response2 = session.get(second_page_url, timeout=REQUEST_TIMEOUT, headers=fetch_headers_p2, allow_redirects=True) # Allow redirects here too
            response2.raise_for_status()
            page2_url = response2.url # Update URL after potential redirects
            logs.append(f"Landed on second page: {page2_url}")
            soup2 = BeautifulSoup(response2.text, PARSER)
            possible_tags_p2 = soup2.find_all(['a', 'button'])

            # Step 6: Find Cloud Resume Download
            resume_link_tag = None
            resume_text_pattern = re.compile(r'cloud\s+resume\s+download', re.IGNORECASE)
            for tag in possible_tags_p2:
                 if resume_text_pattern.search(tag.get_text(strip=True)):
                    resume_link_tag = tag
                    logs.append(f"Found final link tag directly: {tag.name}...")
                    break

            # Step 6a: If found directly
            if resume_link_tag:
                final_link_href = resume_link_tag.get('href')
                if not final_link_href and resume_link_tag.name == 'button':
                     parent_form = resume_link_tag.find_parent('form')
                     if parent_form: final_link_href = parent_form.get('action')
                if not final_link_href:
                    logs.append("Error: Found 'Cloud Resume' but no href/action.")
                    return None, logs
                final_download_link = urljoin(page2_url, final_link_href)
                logs.append(f"Found final Cloud Resume link URL: {final_download_link}")
                return final_download_link, logs

            # Step 6b: If not found directly, check for Generate button
            else:
                logs.append("Info: 'Cloud Resume Download' not found directly. Checking for 'Generate Cloud Link' button...")
                generate_tag = soup2.find('button', id='cloud')
                if not generate_tag: # Fallback search by text
                    generate_pattern = re.compile(r'generate\s+cloud\s+link', re.IGNORECASE)
                    for tag in possible_tags_p2:
                        if generate_pattern.search(tag.get_text(strip=True)): generate_tag = tag; break

                # If Generate button is found, MIMIC THE POST REQUEST
                if generate_tag:
                    logs.append(f"Found 'Generate Cloud Link' button: {generate_tag.name} id='{generate_tag.get('id', 'N/A')}'")
                    logs.append("Info: Attempting to mimic the JavaScript POST request...")

                    # Data extracted from browser analysis
                    post_data = {'action': 'cloud','key': '08df4425e31c4330a1a0a3cefc45c19e84d0a192','action_token': ''}
                    parsed_uri = urlparse(page2_url); hostname = parsed_uri.netloc
                    post_headers = {'x-token': hostname,'Referer': page2_url}
                    post_headers.update(session.headers) # Include session headers like User-Agent

                    logs.append(f"Info: Sending POST request to {page2_url}...")
                    page3_url = None
                    try:
                        post_response = session.post(page2_url, data=post_data, headers=post_headers, timeout=REQUEST_TIMEOUT)
                        post_response.raise_for_status()
                        try:
                            response_data = post_response.json()
                            logs.append(f"Info: POST response JSON: {response_data}")
                            # Check multiple possible keys for the URL
                            if response_data.get('visit_url'): page3_url = urljoin(page2_url, response_data['visit_url'])
                            elif response_data.get('url'): page3_url = urljoin(page2_url, response_data['url'])
                            elif response_data.get('error'):
                                logs.append(f"Error from POST request: {response_data.get('message', 'Unknown error')}")
                                return None, logs
                            else:
                                logs.append("Error: POST response JSON format unknown.")
                                return None, logs

                            if page3_url:
                                logs.append(f"Info: POST successful. Need to poll new URL: {page3_url}")

                        except json.JSONDecodeError:
                            logs.append(f"Error: Failed to decode JSON response from POST. Status: {post_response.status_code}")
                            logs.append(f"Response text (first 500 chars): {post_response.text[:500]}")
                            if "cloudflare" in post_response.text.lower() or "captcha" in post_response.text.lower():
                                logs.append("Hint: Cloudflare/Captcha challenge likely blocked the request.")
                            return None, logs
                    except requests.exceptions.RequestException as post_err:
                        logs.append(f"Error during POST request: {post_err}")
                        return None, logs

                    # If POST was successful and we have page3_url, START POLLING
                    if page3_url:
                        logs.append(f"Info: Starting polling loop for {page3_url}...")
                        start_time = time.time()
                        while time.time() - start_time < GENERATION_TIMEOUT:
                            wait_time = min(POLL_INTERVAL, GENERATION_TIMEOUT - (time.time() - start_time))
                            if wait_time <= 0: break
                            logs.append(f"Info: Waiting {wait_time:.1f}s before checking {page3_url}...")
                            time.sleep(wait_time)
                            try:
                                poll_headers = {'Referer': page2_url} # Keep referer as page 2? Or page3? Try page3.
                                poll_response = session.get(page3_url, timeout=REQUEST_TIMEOUT, headers={'Referer': page3_url}, allow_redirects=True) # Follow redirects
                                poll_landed_url = poll_response.url # URL after polling redirects

                                if poll_response.status_code != 200:
                                    logs.append(f"Warning: Polling status {poll_response.status_code}")
                                    continue # Keep polling

                                poll_soup = BeautifulSoup(poll_response.text, PARSER)
                                polled_resume_tag = None
                                for tag in poll_soup.find_all(['a', 'button']):
                                    if resume_text_pattern.search(tag.get_text(strip=True)):
                                        polled_resume_tag = tag
                                        logs.append(f"\nSuccess: Found 'Cloud Resume Download' after polling!")
                                        break

                                if polled_resume_tag:
                                    final_link_href = polled_resume_tag.get('href')
                                    if not final_link_href and polled_resume_tag.name == 'button':
                                        parent_form = polled_resume_tag.find_parent('form')
                                        if parent_form: final_link_href = parent_form.get('action')
                                    if not final_link_href:
                                        logs.append("Error: Found polled 'Cloud Resume' but no href/action.")
                                        return None, logs
                                    # Use the URL where the resume button was found as base
                                    final_download_link = urljoin(poll_landed_url, final_link_href)
                                    logs.append(f"Found final Cloud Resume link URL after polling: {final_download_link}")
                                    return final_download_link, logs # SUCCESS

                            except requests.exceptions.RequestException as poll_err:
                                logs.append(f"Warning: Error during polling request: {poll_err}. Will retry.")
                            except Exception as parse_err:
                                logs.append(f"Warning: Error parsing polled page: {parse_err}. Will retry.")
                            # If button not found, loop continues

                        # Polling Timeout
                        logs.append(f"Error: Link is generating. Try again after a few minutes. (Timeout: {GENERATION_TIMEOUT}s)")
                        return None, logs
                    # else: POST failed, already returned None above

                else: # Generate button wasn't found at all on page 2
                    logs.append("Error: Neither 'Cloud Resume Download' nor 'Generate Cloud Link' button found on the second page.")
                    body_tag_p2 = soup2.find('body')
                    logs.append(f"Body snippet:\n{str(body_tag_p2)[:1000]}" if body_tag_p2 else response2.text[:1000])
                    return None, logs

        # Step 3b: Fallback - PixeldrainDL on page 1
        else:
            logs.append("Info: 'Fast Cloud Download' not found. Checking for 'PixeldrainDL'...")
            pixeldrain_link_tag = None
            pixeldrain_pattern = re.compile(r'pixeldrain\s*dl', re.IGNORECASE)
            for tag in possible_tags_p1:
                if pixeldrain_pattern.search(tag.get_text(strip=True)):
                    pixeldrain_link_tag = tag
                    logs.append(f"Found fallback tag: {tag.name}...")
                    break
            if pixeldrain_link_tag:
                pixeldrain_href = pixeldrain_link_tag.get('href')
                if not pixeldrain_href and pixeldrain_link_tag.name == 'button':
                    parent_form = pixeldrain_link_tag.find_parent('form')
                    if parent_form: pixeldrain_href = parent_form.get('action')
                if pixeldrain_href:
                    pixeldrain_full_url = urljoin(page1_url, pixeldrain_href)
                    logs.append(f"Found Pixeldrain link URL: {pixeldrain_full_url}")
                    # Assuming pixeldrain link is the final link for this path
                    return pixeldrain_full_url, logs
                else:
                    logs.append("Error: Found Pixeldrain element but couldn't get href/action.")
            # If Pixeldrain also fails or not found
            logs.append("Error: Neither 'Fast Cloud Download' nor 'PixeldrainDL' link found/processed on the first page.")
            return None, logs

    except requests.exceptions.Timeout as e:
        logs.append(f"Error: Request timed out. {e}")
        return None, logs
    except requests.exceptions.RequestException as e:
        logs.append(f"Error during requests: {e}")
        return None, logs
    except Exception as e:
        logs.append(f"An unexpected error occurred: {e}\n{traceback.format_exc()}")
        return None, logs

# --- CORS Helper Functions ---
def _build_cors_preflight_response():
    response = make_response()
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type")
    response.headers.add("Access-Control-Allow-Methods", "POST, OPTIONS")
    return response

def _corsify_actual_response(response):
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response

# --- Flask API Endpoint ---
@app.route('/api/gdflix', methods=['POST', 'OPTIONS'])
def gdflix_bypass_api():
    if request.method == 'OPTIONS':
        return _build_cors_preflight_response()

    elif request.method == 'POST':
        final_download_link = None
        script_logs = [] # Collect logs from the function
        result = {"success": False, "error": "Request processing failed", "finalUrl": None, "logs": script_logs}
        status_code = 500 # Default to server error

        try:
            # Get JSON data
            try:
                data = request.get_json()
                if not data:
                    raise ValueError("No JSON data received")
                gdflix_url = data.get('gdflixUrl') # Changed key
                script_logs.append("Received JSON POST body.")
            except Exception as e:
                script_logs.append(f"Error: Could not parse JSON request body: {e}")
                result["error"] = "Invalid or missing JSON in request body"
                return _corsify_actual_response(jsonify(result)), 400

            # Validate URL
            if not gdflix_url or not isinstance(gdflix_url, str):
                script_logs.append("Error: gdflixUrl missing or invalid in request.")
                result["error"] = "Missing or invalid gdflixUrl in request body"
                return _corsify_actual_response(jsonify(result)), 400

            script_logs.append(f"Processing URL: {gdflix_url}")
            parsed_start_url = urlparse(gdflix_url)
            if not parsed_start_url.scheme or not parsed_start_url.netloc:
                 script_logs.append(f"Error: Invalid URL format: {gdflix_url}")
                 result["error"] = f"Invalid URL format provided: {gdflix_url}"
                 return _corsify_actual_response(jsonify(result)), 400

            # Perform Scraping
            final_download_link, script_logs_from_func = get_gdflix_download_link(gdflix_url)
            script_logs.extend(script_logs_from_func) # Add logs from the function

            # Prepare Response based on script output
            if final_download_link:
                result["success"] = True
                result["finalUrl"] = final_download_link
                result["error"] = None
                status_code = 200
            else:
                result["success"] = False
                # Extract error from logs if script returned None
                failure_indicators = ["Error:", "FATAL ERROR", "FAILED", "Could not find", "timed out"]
                extracted_error = "GDFLIX Extraction Failed (Check logs)" # Default error
                for log_entry in reversed(script_logs): # Check recent logs first
                    if any(indicator in log_entry for indicator in failure_indicators):
                         # Try to get the part after "Error:"
                         parts = log_entry.split("Error:", 1)
                         if len(parts) > 1:
                             extracted_error = parts[1].strip()
                         else: # Otherwise just take the whole log entry
                             extracted_error = log_entry.strip()
                         break # Stop after finding the first error indicator
                result["error"] = extracted_error[:150] # Limit error length
                status_code = 500 # Indicate backend failure


        except Exception as e:
            # Catch unexpected errors in the Flask handler itself
            print(f"FATAL Handler Error: {e}", file=sys.stderr)
            script_logs.append(f"FATAL Handler Error: {e}\n{traceback.format_exc()}")
            result["success"] = False
            result["error"] = "Internal server error processing request."
            status_code = 500

        finally:
            # Ensure logs are included and send response
            result["logs"] = script_logs
            return _corsify_actual_response(jsonify(result)), status_code
    else:
        # Method Not Allowed
        return jsonify({"error": "Method Not Allowed"}), 405

# --- Run Flask App (for local testing) ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True) # Use a different port locally if needed