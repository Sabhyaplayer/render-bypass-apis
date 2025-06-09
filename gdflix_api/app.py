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
import threading # For self-ping
import logging # For better logging

# Try importing lxml, fall back to html.parser if not installed
try:
    import lxml
    PARSER = "lxml"
    LXML_AVAILABLE = True
except ImportError:
    PARSER = "html.parser"
    print("Warning: lxml not found, using html.parser.", file=sys.stderr)

# --- Flask App Initialization ---
app = Flask(__name__)

# --- CORS Configuration ---
CORS(app)

# --- Basic Logging Configuration ---
if not app.debug:
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    stream_handler.setFormatter(formatter)
    app.logger.addHandler(stream_handler)
    app.logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- Configuration ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://google.com'
}
GENERATION_TIMEOUT = 40
POLL_INTERVAL = 5
REQUEST_TIMEOUT = 30
MAX_REDIRECT_HOPS = 5

# --- Self-Ping Configuration ---
# MODIFIED LINE: Changed from 10 * 60 to 48
SELF_PING_INTERVAL_SECONDS = 48  # 48 seconds
PING_REQUEST_TIMEOUT = 20

# --- Core GDFLIX Bypass Function ---
def get_gdflix_download_link(start_url):
    session = requests.Session()
    session.headers.update(HEADERS)
    logs = []
    current_url = start_url
    hops_count = 0
    landed_url = None
    html_content = None
    # Helper variables for Drivebot path to avoid NameError if path isn't fully taken
    page2_drivebot_url = None
    html_content_p2_drivebot = None # To store HTML of index server page for debugging
    page3_drivebot_url = None
    html_content_p3_drivebot = None # To store HTML of generate link page for debugging


    try:
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

        page1_url = landed_url
        logs.append(f"--- Final Content Page HTML Snippet (URL: {page1_url}) ---")
        logs.append(html_content[:3000] + ('...' if len(html_content) > 3000 else ''))
        logs.append(f"--- End Final Content Page HTML Snippet ---")

        if "cloudflare" in html_content.lower() or "checking your browser" in html_content.lower() or "challenge-platform" in html_content.lower():
             logs.append("WARNING: Potential Cloudflare challenge page detected on final content page!")

        soup1 = BeautifulSoup(html_content, PARSER)
        possible_tags_p1 = soup1.find_all(['a', 'button'])
        logs.append(f"Found {len(possible_tags_p1)} potential link/button tags on final content page ({page1_url}).")

        # --- PRIORITY 1: Look for "PixeldrainDL" or "Pixeldrain" ---
        logs.append("Searching for 'PixeldrainDL' or 'Pixeldrain' button text pattern on final content page...")
        pixeldrain_link_tag = None
        pixeldrain_pattern = re.compile(r'pixeldrain\s*(dl)?', re.IGNORECASE)
        for tag in possible_tags_p1:
             tag_text = tag.get_text(strip=True)
             if pixeldrain_pattern.search(tag_text):
                pixeldrain_link_tag = tag
                logs.append(f"  Success: Found potential Pixeldrain tag: <{tag.name}> with text '{tag_text}'")
                break
        
        if pixeldrain_link_tag:
             pixeldrain_href = pixeldrain_link_tag.get('href')
             if not pixeldrain_href and pixeldrain_link_tag.name == 'button':
                 parent_form = pixeldrain_link_tag.find_parent('form')
                 if parent_form: 
                     pixeldrain_href = parent_form.get('action')
                     logs.append(f"    Extracted href from parent form action: {pixeldrain_href}")

             if pixeldrain_href:
                 pixeldrain_full_url = urljoin(page1_url, pixeldrain_href)
                 logs.append(f"Success: Found Pixeldrain link URL: {pixeldrain_full_url}")
                 return pixeldrain_full_url, logs
             else:
                 logs.append(f"  Info: Found Pixeldrain element ('{pixeldrain_link_tag.get_text(strip=True)}') but couldn't get href/action. Trying next priority.")
        else:
            logs.append("Info: 'PixeldrainDL' or 'Pixeldrain' button/pattern not found. Trying next priority.")


        # --- PRIORITY 2: Look for "CLOUD DOWNLOAD [R2]" ---
        logs.append("Searching for 'CLOUD DOWNLOAD [R2]' button text pattern on final content page...")
        cloud_r2_link_tag = None
        cloud_r2_pattern = re.compile(r'cloud\s+download\s+\[R2\]', re.IGNORECASE)
        for tag in possible_tags_p1:
            tag_text = tag.get_text(strip=True)
            if cloud_r2_pattern.search(tag_text):
                cloud_r2_link_tag = tag
                logs.append(f"  Success: Found potential 'CLOUD DOWNLOAD [R2]' tag: <{tag.name}> with text '{tag_text}'")
                break
        
        if cloud_r2_link_tag:
            cloud_r2_href = cloud_r2_link_tag.get('href')
            if not cloud_r2_href and cloud_r2_link_tag.name == 'button':
                parent_form = cloud_r2_link_tag.find_parent('form')
                if parent_form: 
                    cloud_r2_href = parent_form.get('action')
                    logs.append(f"    Extracted href from parent form action: {cloud_r2_href}")
            
            if cloud_r2_href:
                final_download_link = urljoin(page1_url, cloud_r2_href)
                logs.append(f"Success: Found R2 download link: {final_download_link}")
                return final_download_link, logs
            else:
                logs.append(f"  Info: Found 'CLOUD DOWNLOAD [R2]' element ('{cloud_r2_link_tag.get_text(strip=True)}') but couldn't get href/action. Trying next priority.")
        else:
            logs.append("Info: 'CLOUD DOWNLOAD [R2]' button/pattern not found. Trying next priority.")
        

        # --- PRIORITY 3: Look for "Fast Cloud Download" ---
        logs.append("Searching for 'Fast Cloud Download/DL' button text pattern on final content page...")
        fast_cloud_link_tag = None
        fast_cloud_pattern = re.compile(r'fast\s*cloud\s*(download|dl)', re.IGNORECASE)
        for tag in possible_tags_p1:
            tag_text = tag.get_text(strip=True)
            if fast_cloud_pattern.search(tag_text):
                fast_cloud_link_tag = tag
                logs.append(f"  Success: Found potential 'Fast Cloud Download' tag: <{tag.name}> with text '{tag_text}'")
                break

        if fast_cloud_link_tag:
            fast_cloud_href = fast_cloud_link_tag.get('href')
            if not fast_cloud_href and fast_cloud_link_tag.name == 'button':
                parent_form = fast_cloud_link_tag.find_parent('form')
                if parent_form: 
                    fast_cloud_href = parent_form.get('action')
                    logs.append(f"    Extracted href from parent form action: {fast_cloud_href}")

            if not fast_cloud_href:
                logs.append(f"  Error: Found '{fast_cloud_link_tag.get_text(strip=True)}' (Fast Cloud) element but couldn't get href/action. Trying next priority.")
            else:
                # --- Start of Fast Cloud multi-step logic ---
                intermediate_url = urljoin(page1_url, fast_cloud_href)
                logs.append(f"Found intermediate link URL (from Fast Cloud button): {intermediate_url}")
                time.sleep(1) 

                logs.append(f"Fetching intermediate page URL (potentially with Generate button): {intermediate_url}")
                fetch_headers_p2 = {'Referer': page1_url}
                response_intermediate = session.get(intermediate_url, timeout=REQUEST_TIMEOUT, headers=fetch_headers_p2, allow_redirects=True)
                response_intermediate.raise_for_status()
                page2_url = response_intermediate.url 
                html_content_p2 = response_intermediate.text
                logs.append(f"Landed on intermediate page: {page2_url} (Status: {response_intermediate.status_code})")

                logs.append(f"--- Intermediate Page HTML Content Snippet (URL: {page2_url}) ---")
                logs.append(html_content_p2[:2000] + ('...' if len(html_content_p2) > 2000 else ''))
                logs.append(f"--- End Intermediate Page HTML Snippet ---")
                if "cloudflare" in html_content_p2.lower() or "checking your browser" in html_content_p2.lower():
                     logs.append("WARNING: Potential Cloudflare challenge page detected on Intermediate Page!")

                soup2 = BeautifulSoup(html_content_p2, PARSER)
                possible_tags_p2 = soup2.find_all(['a', 'button'])
                logs.append(f"Found {len(possible_tags_p2)} potential link/button tags on intermediate page ({page2_url}).")

                resume_link_tag = None
                resume_text_pattern = re.compile(r'cloud\s+resume\s+download', re.IGNORECASE)
                logs.append("Searching for 'Cloud Resume Download' button text pattern on intermediate page...")
                for tag in possible_tags_p2:
                     tag_text = tag.get_text(strip=True)
                     if resume_text_pattern.search(tag_text):
                        resume_link_tag = tag
                        logs.append(f"Success: Found final link tag directly: <{tag.name}> with text '{tag_text}'")
                        break

                if resume_link_tag:
                    final_link_href = resume_link_tag.get('href')
                    if not final_link_href and resume_link_tag.name == 'button':
                         parent_form = resume_link_tag.find_parent('form')
                         if parent_form: final_link_href = parent_form.get('action')

                    if not final_link_href:
                        logs.append(f"Error: Found '{resume_link_tag.get_text(strip=True)}' but no href/action.")
                        return None, logs 

                    final_download_link = urljoin(page2_url, final_link_href)
                    logs.append(f"Success: Found final Cloud Resume link URL directly: {final_download_link}")
                    return final_download_link, logs
                else:
                    logs.append("Info: 'Cloud Resume Download' not found directly. Checking for 'Generate Cloud Link' button...")
                    generate_tag = None
                    generate_tag_by_id = soup2.find('button', id='cloud')
                    if generate_tag_by_id:
                        logs.append("  Found 'Generate Cloud Link' button by id='cloud'.")
                        generate_tag = generate_tag_by_id
                    else:
                        logs.append("  Button with id='cloud' not found. Searching by text pattern 'generate cloud link'...")
                        generate_pattern = re.compile(r'generate\s+cloud\s+link', re.IGNORECASE)
                        for tag in possible_tags_p2:
                            tag_text = tag.get_text(strip=True)
                            if generate_pattern.search(tag_text):
                                generate_tag = tag
                                logs.append(f"  Success: Found potential generate tag by text: <{tag.name}> with text '{tag_text}'")
                                break
                    
                    if generate_tag:
                        logs.append(f"Found 'Generate Cloud Link' button: <{generate_tag.name}> id='{generate_tag.get('id', 'N/A')}'")
                        logs.append("Attempting to mimic the JavaScript POST request...")

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
                            btn_name = generate_tag.get('name')
                            btn_value = generate_tag.get('value')
                            if btn_name and generate_tag.name == 'button': 
                                 post_data[btn_name] = btn_value if btn_value is not None else ''
                                 logs.append(f"    Added button data: name='{btn_name}', value='{btn_value}'")

                        default_post_data = {'action': 'cloud', 'key': '08df4425e31c4330a1a0a3cefc45c19e84d0a192', 'action_token': ''}
                        final_post_data = {**default_post_data, **post_data}
                        if 'action' not in final_post_data: final_post_data['action'] = 'cloud'
                        logs.append(f"  Final POST data payload: {final_post_data}")

                        parsed_uri = urlparse(page2_url)
                        hostname = parsed_uri.netloc
                        post_headers = {
                            'Referer': page2_url,
                            'x-token': hostname,
                            'Accept': 'application/json, text/javascript, */*; q=0.01',
                            'X-Requested-With': 'XMLHttpRequest',
                        }
                        logs.append(f"  POST headers (excluding session defaults): {post_headers}")

                        logs.append(f"Sending POST request to: {page2_url}")
                        page3_fc_url = None # Differentiate from drivebot's page3_url
                        try:
                            post_response = session.post(page2_url, data=final_post_data, headers=post_headers, timeout=REQUEST_TIMEOUT)
                            logs.append(f"  POST response status: {post_response.status_code}")
                            content_type = post_response.headers.get('Content-Type', '').lower()
                            response_text = post_response.text
                            extracted_poll_url = False

                            if 'application/json' in content_type:
                                try:
                                    response_data = post_response.json()
                                    logs.append(f"  POST response JSON (from header): {response_data}")
                                    if post_response.status_code == 200 and not response_data.get('error'):
                                         poll_url_relative = response_data.get('visit_url') or response_data.get('url')
                                         if poll_url_relative:
                                             page3_fc_url = urljoin(page2_url, poll_url_relative)
                                             logs.append(f"  POST successful. Extracted polling URL: {page3_fc_url}")
                                             extracted_poll_url = True
                                         else:
                                             logs.append("  Error: POST success status but no 'visit_url' or 'url' key found in JSON.")
                                    elif response_data.get('error'):
                                         error_msg = response_data.get('message', 'Unknown error from server POST response')
                                         logs.append(f"  Error from POST JSON response: {error_msg} (Status: {post_response.status_code})")
                                    else:
                                         logs.append(f"  Error: POST returned status {post_response.status_code} with JSON, but format unclear.")
                                         logs.append(f"  Response JSON: {response_data}")
                                except json.JSONDecodeError:
                                    logs.append(f"  Error: Failed to decode JSON response, though Content-Type was JSON.")
                                    logs.append(f"  Response text (first 500 chars): {response_text[:500]}")
                            elif post_response.status_code == 200:
                                logs.append(f"  Info: POST Content-Type is '{content_type}', not JSON. Status 200 received. Attempting to parse body as JSON anyway...")
                                try:
                                    response_data = json.loads(response_text)
                                    logs.append(f"  Success: Parsed response body as JSON despite incorrect Content-Type.")
                                    logs.append(f"  Parsed JSON data: {response_data}")
                                    if not response_data.get('error'):
                                        poll_url_relative = response_data.get('visit_url') or response_data.get('url')
                                        if poll_url_relative:
                                            page3_fc_url = urljoin(page2_url, poll_url_relative)
                                            logs.append(f"  Extracted polling URL from parsed text: {page3_fc_url}")
                                            extracted_poll_url = True
                                        else:
                                            logs.append("  Error: Parsed JSON successfully but no 'visit_url' or 'url' key found.")
                                    elif response_data.get('error'):
                                        error_msg = response_data.get('message', 'Unknown error in parsed JSON')
                                        logs.append(f"  Error found in parsed JSON: {error_msg}")
                                    else:
                                        logs.append("  Warning: Parsed JSON but structure is unexpected (no error/url keys).")
                                except json.JSONDecodeError:
                                    logs.append(f"  Error: Failed to decode potentially JSON response body (Content-Type was '{content_type}', Status 200).")
                                    logs.append(f"  Response text (first 500 chars): {response_text[:500]}")
                            else:
                                 logs.append(f"  Error: POST response status was {post_response.status_code} or Content-Type '{content_type}' was unexpected.")
                                 if not response_text.strip(): logs.append("  Response body was empty.")
                                 else: logs.append(f"  Response text (first 500 chars): {response_text[:500]}")
                                 if "cloudflare" in response_text.lower() or "captcha" in response_text.lower():
                                     logs.append("  Hint: Cloudflare/Captcha challenge likely blocked the POST request.")

                            if not extracted_poll_url:
                                 logs.append(f"  Error: Failed to obtain a valid polling URL from the POST response.")
                                 try:
                                     if post_response.status_code != 200: post_response.raise_for_status()
                                 except requests.exceptions.HTTPError as http_err: logs.append(f"  HTTP Error details: {http_err}")
                                 return None, logs 
                        except requests.exceptions.RequestException as post_err:
                            logs.append(f"  Error during POST request network operation: {post_err}")
                            return None, logs 

                        if page3_fc_url:
                            logs.append(f"Starting polling loop for {page3_fc_url}...")
                            start_time = time.time()
                            while time.time() - start_time < GENERATION_TIMEOUT:
                                elapsed_time = time.time() - start_time
                                remaining_time = GENERATION_TIMEOUT - elapsed_time
                                wait_time = min(POLL_INTERVAL, remaining_time)
                                if wait_time <= 0: break

                                logs.append(f"  Polling: Waiting {wait_time:.1f}s before checking {page3_fc_url}...")
                                time.sleep(wait_time)
                                poll_landed_url = None
                                try:
                                    poll_headers = {'Referer': page3_fc_url} 
                                    poll_response = session.get(page3_fc_url, timeout=REQUEST_TIMEOUT, headers=poll_headers, allow_redirects=True)
                                    poll_landed_url = poll_response.url
                                    poll_status = poll_response.status_code
                                    poll_html = poll_response.text
                                    logs.append(f"  Polling: GET {page3_fc_url} -> Status {poll_status}, Landed on {poll_landed_url}")

                                    if poll_status != 200:
                                        logs.append(f"  Warning: Polling status {poll_status}, continuing poll loop.")
                                        continue

                                    poll_soup = BeautifulSoup(poll_html, PARSER)
                                    polled_resume_tag = None
                                    for tag in poll_soup.find_all(['a', 'button']):
                                        if resume_text_pattern.search(tag.get_text(strip=True)): 
                                            polled_resume_tag = tag
                                            logs.append(f"    Success: Found 'Cloud Resume Download' after polling on {poll_landed_url}!")
                                            break
                                    
                                    if polled_resume_tag:
                                        final_link_href = polled_resume_tag.get('href')
                                        if not final_link_href and polled_resume_tag.name == 'button':
                                            parent_form_poll = polled_resume_tag.find_parent('form')
                                            if parent_form_poll: final_link_href = parent_form_poll.get('action')

                                        if not final_link_href:
                                            logs.append(f"    Error: Found polled '{polled_resume_tag.get_text(strip=True)}' element but no href/action.")
                                            return None, logs

                                        final_download_link = urljoin(poll_landed_url, final_link_href)
                                        logs.append(f"Success: Found final Cloud Resume link URL after polling: {final_download_link}")
                                        return final_download_link, logs
                                except requests.exceptions.Timeout:
                                     logs.append(f"  Warning: Timeout during polling request to {page3_fc_url}. Will retry.")
                                except requests.exceptions.RequestException as poll_err:
                                     logs.append(f"  Warning: Network error during polling request: {poll_err}. Will retry.")
                                except Exception as parse_err:
                                     logs.append(f"  Warning: Error parsing polled page {poll_landed_url or page3_fc_url}: {parse_err}. Will retry.")

                            logs.append(f"Error: Link generation timed out after {GENERATION_TIMEOUT}s of polling {page3_fc_url}.")
                            return None, logs 
                    else: 
                        logs.append("Error: Neither 'Cloud Resume Download' nor 'Generate Cloud Link' button/pattern found on the intermediate page (Fast Cloud path).")
                        body_tag_p2 = soup2.find('body')
                        logs.append("--- Intermediate Page Body Snippet (Fast Cloud - for debugging why buttons were missed) ---")
                        logs.append(str(body_tag_p2)[:1000] + '...' if body_tag_p2 else html_content_p2[:1000] + '...')
                        logs.append("--- End Intermediate Page Body Snippet (Fast Cloud) ---")
                        return None, logs 
        else: 
            logs.append("Info: 'Fast Cloud Download/DL' button/pattern not found. Trying next priority.")

        # --- PRIORITY 4: Look for "DRIVEBOT" ---
        logs.append("Searching for 'DRIVEBOT' button text pattern on final content page (Priority 4)...")
        drivebot_initial_tag = None
        drivebot_initial_pattern = re.compile(r'DRIVEBOT', re.IGNORECASE)
        for tag in possible_tags_p1: 
            tag_text = tag.get_text(strip=True)
            if drivebot_initial_pattern.search(tag_text):
                drivebot_initial_tag = tag
                logs.append(f"  Success: Found potential DRIVEBOT tag: <{tag.name}> with text '{tag_text}'")
                break

        if drivebot_initial_tag:
            drivebot_initial_href = drivebot_initial_tag.get('href')
            if not drivebot_initial_href and drivebot_initial_tag.name == 'button':
                parent_form_db_init = drivebot_initial_tag.find_parent('form')
                if parent_form_db_init:
                    drivebot_initial_href = parent_form_db_init.get('action')
                    logs.append(f"    Extracted href from parent form action: {drivebot_initial_href}")

            if drivebot_initial_href:
                drivebot_step1_url = urljoin(page1_url, drivebot_initial_href)
                logs.append(f"  Following DRIVEBOT link to (Index Server Page): {drivebot_step1_url}")
                time.sleep(1)

                try:
                    response_drivebot_s1 = session.get(drivebot_step1_url, timeout=REQUEST_TIMEOUT, headers={'Referer': page1_url}, allow_redirects=True)
                    response_drivebot_s1.raise_for_status()
                    page2_drivebot_url = response_drivebot_s1.url 
                    html_content_p2_drivebot = response_drivebot_s1.text 
                    logs.append(f"  Landed on DRIVEBOT Index Server page: {page2_drivebot_url} (Status: {response_drivebot_s1.status_code})")
                    
                    soup_p2_drivebot = BeautifulSoup(html_content_p2_drivebot, PARSER)
                    drivebot_server_choice_tag = None
                    drivebot_server_pattern = re.compile(r'DRIVEBOT\s*1(?:\s*\[R1\])?', re.IGNORECASE)
                    generic_drivebot_server_pattern = re.compile(r'DRIVEBOT', re.IGNORECASE) 
                    
                    possible_server_tags = soup_p2_drivebot.find_all(['a', 'button'])
                    for tag in possible_server_tags:
                        tag_text = tag.get_text(strip=True)
                        if drivebot_server_pattern.search(tag_text):
                            drivebot_server_choice_tag = tag
                            logs.append(f"    Found preferred DRIVEBOT 1 server choice: <{tag.name}> '{tag_text}'")
                            break
                    
                    if not drivebot_server_choice_tag:
                        logs.append("    Preferred DRIVEBOT 1 not found, looking for any DRIVEBOT server link on Index Page.")
                        for tag in possible_server_tags:
                            tag_text = tag.get_text(strip=True)
                            if generic_drivebot_server_pattern.search(tag_text):
                                drivebot_server_choice_tag = tag
                                logs.append(f"    Found generic DRIVEBOT server choice: <{tag.name}> '{tag_text}'")
                                break
                    
                    if drivebot_server_choice_tag:
                        drivebot_server_next_url = None
                        drivebot_server_payload = {} 
                        drivebot_server_method = 'GET' 

                        if drivebot_server_choice_tag.name == 'a' and drivebot_server_choice_tag.get('href'):
                            drivebot_server_next_url = urljoin(page2_drivebot_url, drivebot_server_choice_tag.get('href'))
                            logs.append(f"    DRIVEBOT server choice is an <a> tag. URL: {drivebot_server_next_url}")
                            drivebot_server_method = 'GET'
                        elif drivebot_server_choice_tag.name == 'button' or (drivebot_server_choice_tag.name == 'input' and drivebot_server_choice_tag.get('type') in ['submit', 'button']):
                            parent_form_db_s2 = drivebot_server_choice_tag.find_parent('form')
                            if parent_form_db_s2:
                                logs.append(f"    DRIVEBOT server choice <{drivebot_server_choice_tag.name}> is in a form.")
                                form_action = parent_form_db_s2.get('action')
                                drivebot_server_next_url = urljoin(page2_drivebot_url, form_action if form_action else page2_drivebot_url)

                                drivebot_server_method = parent_form_db_s2.get('method', 'GET').upper()
                                logs.append(f"      Form method: {drivebot_server_method}, Action URL: {drivebot_server_next_url}")

                                for input_tag_s2 in parent_form_db_s2.find_all('input'):
                                    name = input_tag_s2.get('name')
                                    value = input_tag_s2.get('value')
                                    if name: 
                                        drivebot_server_payload[name] = value if value is not None else ''
                                        logs.append(f"        Extracted form input: name='{name}', value='{value}'")
                                
                                btn_name = drivebot_server_choice_tag.get('name')
                                btn_value = drivebot_server_choice_tag.get('value')
                                if btn_name and drivebot_server_choice_tag.name in ['button', 'input']: 
                                    drivebot_server_payload[btn_name] = btn_value if btn_value is not None else ''
                                    logs.append(f"        Added button data: name='{btn_name}', value='{btn_value}'")
                            else:
                                logs.append(f"    Error: DRIVEBOT server choice <{drivebot_server_choice_tag.name}> found, but not within a <form>. Cannot determine action.")
                                if html_content_p2_drivebot: 
                                    logs.append(f"--- BEGIN HTML of DRIVEBOT Index Server Page ({page2_drivebot_url}) for missing form ---")
                                    logs.append(html_content_p2_drivebot) 
                                    logs.append(f"--- END HTML of DRIVEBOT Index Server Page ---")
                                else:
                                    logs.append(f"    Debug: html_content_p2_drivebot was not available for logging.")
                        else: 
                            logs.append(f"    Warning: DRIVEBOT server choice tag <{drivebot_server_choice_tag.name}> type unhandled or lacks href. Attempting to find parent form action if any.")
                            parent_form_db_s2_fallback = drivebot_server_choice_tag.find_parent('form')
                            if parent_form_db_s2_fallback:
                                form_action_fallback = parent_form_db_s2_fallback.get('action')
                                drivebot_server_next_url = urljoin(page2_drivebot_url, form_action_fallback if form_action_fallback else page2_drivebot_url)
                                drivebot_server_method = parent_form_db_s2_fallback.get('method', 'GET').upper()
                                logs.append(f"      Fallback: Found parent form. Method: {drivebot_server_method}, Action URL: {drivebot_server_next_url}")
                        
                        if drivebot_server_next_url:
                            logs.append(f"    Proceeding to DRIVEBOT Generate Link Page. Method: {drivebot_server_method}, URL: {drivebot_server_next_url}, Payload: {drivebot_server_payload}")
                            time.sleep(1)
                            
                            response_drivebot_s2 = None
                            request_headers_s2 = {'Referer': page2_drivebot_url}
                            if drivebot_server_method == 'POST':
                                response_drivebot_s2 = session.post(drivebot_server_next_url, data=drivebot_server_payload, timeout=REQUEST_TIMEOUT, headers=request_headers_s2, allow_redirects=True)
                            else: # GET
                                response_drivebot_s2 = session.get(drivebot_server_next_url, params=drivebot_server_payload, timeout=REQUEST_TIMEOUT, headers=request_headers_s2, allow_redirects=True)
                            
                            response_drivebot_s2.raise_for_status()
                            page3_drivebot_url = response_drivebot_s2.url 
                            html_content_p3_drivebot = response_drivebot_s2.text 
                            logs.append(f"    Landed on DRIVEBOT Generate Link page: {page3_drivebot_url} (Status: {response_drivebot_s2.status_code})")
                            
                            soup_p3_drivebot = BeautifulSoup(html_content_p3_drivebot, PARSER)
                            generate_link_button = None
                            generate_link_pattern = re.compile(r'Generate Link', re.IGNORECASE)
                            possible_gen_tags = soup_p3_drivebot.find_all(['a', 'button', 'input'])
                            for tag in possible_gen_tags:
                                text_content = ""
                                if tag.name == 'input':
                                    if tag.get('type') in ['button', 'submit']:
                                        text_content = tag.get('value', '')
                                else: 
                                    text_content = tag.get_text(strip=True)
                                
                                if generate_link_pattern.search(text_content):
                                    generate_link_button = tag
                                    logs.append(f"      Found 'Generate Link' element: <{tag.name}> '{text_content}'")
                                    break
                            
                            if generate_link_button:
                                post_url_generate = page3_drivebot_url 
                                post_data_generate = {}
                                http_method_generate = 'POST' 

                                parent_form_generate = generate_link_button.find_parent('form')
                                if parent_form_generate:
                                    logs.append("      'Generate Link' element is in a form. Extracting details.")
                                    form_action_gen = parent_form_generate.get('action')
                                    post_url_generate = urljoin(page3_drivebot_url, form_action_gen if form_action_gen else page3_drivebot_url)
                                    logs.append(f"        Form action URL: {post_url_generate}")
                                    
                                    http_method_generate = parent_form_generate.get('method', 'POST').upper()
                                    logs.append(f"        Form method: {http_method_generate}")

                                    for input_tag_gen in parent_form_generate.find_all('input'):
                                        name = input_tag_gen.get('name')
                                        value = input_tag_gen.get('value')
                                        if name:
                                            post_data_generate[name] = value if value is not None else ''
                                            logs.append(f"          Extracted form input: name='{name}', value='{value}'")
                                    
                                    if generate_link_button.name in ['input', 'button'] and generate_link_button.get('name'):
                                        btn_name_gen = generate_link_button.get('name')
                                        btn_value_gen = generate_link_button.get('value', '') 
                                        post_data_generate[btn_name_gen] = btn_value_gen
                                        logs.append(f"          Added button data: name='{btn_name_gen}', value='{btn_value_gen}'")
                                
                                elif generate_link_button.name == 'a' and generate_link_button.get('href') and generate_link_button.get('href').strip() not in ['#', 'javascript:void(0);', '']:
                                    post_url_generate = urljoin(page3_drivebot_url, generate_link_button.get('href'))
                                    http_method_generate = 'GET' 
                                    logs.append(f"      'Generate Link' is an <a> tag with href. Using GET to: {post_url_generate}")
                                else: 
                                    logs.append("      'Generate Link' element not in a form and not a direct <a> link. Assuming POST to current page. This might need JS analysis if it fails.")
                                    post_url_generate = page3_drivebot_url 
                                
                                generate_headers = {
                                    'Referer': page3_drivebot_url,
                                    'X-Requested-With': 'XMLHttpRequest', 
                                    'Accept': '*/*' 
                                }
                                
                                response_generate = None
                                if http_method_generate == 'POST':
                                    logs.append(f"      Sending POST request to: {post_url_generate} with data: {post_data_generate}")
                                    response_generate = session.post(post_url_generate, data=post_data_generate, headers=generate_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
                                else: # GET
                                    logs.append(f"      Sending GET request to: {post_url_generate} with params: {post_data_generate}") 
                                    response_generate = session.get(post_url_generate, params=post_data_generate, headers=generate_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)

                                response_generate.raise_for_status()
                                page4_drivebot_url = response_generate.url 
                                html_content_p4_drivebot = response_generate.text
                                logs.append(f"      Landed on/Received content from 'Generate Link' action: {page4_drivebot_url} (Status: {response_generate.status_code})")
                                
                                soup_p4_drivebot = BeautifulSoup(html_content_p4_drivebot, PARSER)
                                final_dl_link = None
                                
                                link_input_tag = soup_p4_drivebot.find('input', {'value': re.compile(r'https?://[^\s"\']*\.gdindex\.lol[^\s"\']*')})
                                if link_input_tag and link_input_tag.get('value'):
                                    final_dl_link = link_input_tag.get('value').strip()
                                    logs.append(f"Success: Found final Drivebot download link in input field: {final_dl_link}")
                                
                                if not final_dl_link:
                                    link_anchor_tag = soup_p4_drivebot.find('a', {'href': re.compile(r'https?://[^\s"\']*\.gdindex\.lol[^\s"\']*')})
                                    if link_anchor_tag and link_anchor_tag.get('href'):
                                        final_dl_link = link_anchor_tag.get('href').strip()
                                        logs.append(f"Success: Found final Drivebot download link in <a> tag: {final_dl_link}")

                                if final_dl_link:
                                    return final_dl_link, logs 
                                else:
                                    logs.append("        Error: Could not find the final gdindex.lol link in the response after 'Generate Link' action.")
                                    if html_content_p4_drivebot:
                                        logs.append(f"--- Drivebot Page 4 HTML Snippet (link not found) ---")
                                        logs.append(html_content_p4_drivebot[:2000] + ('...' if len(html_content_p4_drivebot) > 2000 else ''))
                                        logs.append(f"--- End Drivebot Page 4 HTML Snippet ---")
                            else:
                                logs.append("    Error: 'Generate Link' button/element not found on Drivebot page 3.")
                                if html_content_p3_drivebot:
                                    logs.append(f"--- Drivebot Page 3 HTML Snippet ('Generate Link' not found) ---")
                                    logs.append(html_content_p3_drivebot[:2000] + ('...' if len(html_content_p3_drivebot) > 2000 else ''))
                                    logs.append(f"--- End Drivebot Page 3 HTML Snippet ---")
                        else:
                            logs.append("  Error: Could not determine next URL or method for DRIVEBOT server choice on Index page.")
                    else:
                        logs.append("  Error: Could not find a DRIVEBOT server choice button/link on Index page.")
                        if html_content_p2_drivebot:
                            logs.append(f"--- Drivebot Index Server Page HTML Snippet (server choice not found) ---")
                            logs.append(html_content_p2_drivebot[:2000] + ('...' if len(html_content_p2_drivebot) > 2000 else ''))
                            logs.append(f"--- End Drivebot Index Server Page HTML Snippet ---")

                except requests.exceptions.RequestException as e_db_process:
                    current_step_url_for_error = "unknown_drivebot_step"
                    if 'page3_drivebot_url' in locals() and page3_drivebot_url: current_step_url_for_error = page3_drivebot_url
                    elif 'page2_drivebot_url' in locals() and page2_drivebot_url: current_step_url_for_error = page2_drivebot_url
                    elif 'drivebot_step1_url' in locals() and drivebot_step1_url: current_step_url_for_error = drivebot_step1_url
                    logs.append(f"  Error during DRIVEBOT multi-step process (around URL {current_step_url_for_error}): {e_db_process}")
            else:
                logs.append("  Info: Found DRIVEBOT element on initial page but couldn't get href/action. Trying next priority (or ending).")
        else:
            logs.append("Info: 'DRIVEBOT' button/pattern not found on initial page.")
        

        logs.append("Error: All prioritized search attempts (Pixeldrain, R2, Fast Cloud, Drivebot) failed to yield a download link.")

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

    return None, logs

# --- Flask API Endpoint ---
@app.route('/api/gdflix', methods=['POST'])
def gdflix_bypass_api():
    script_logs = []
    result = {"success": False, "error": "Request processing failed", "finalUrl": None, "logs": script_logs}
    status_code = 500
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
            status_code = 200
        else:
            script_logs.append("Bypass process failed to find the final download link.")
            result["success"] = False
            failure_indicators = [
                "Error:", "FATAL:", "FAILED", "timed out", "neither", "blocked", 
                "exceeded maximum", "all prioritized search attempts", 
                "could not find the final gdindex.lol link", 
                "'Generate Link' button/element not found",
                "Could not determine next URL or method for DRIVEBOT server choice",
                "Could not find a DRIVEBOT server choice button/link",
                "DRIVEBOT server choice <.+> found, but not within a <form>" 
            ]
            extracted_error = "GDFLIX Extraction Failed (Check logs for details)"
            timeout_occurred = False 

            last_error_log = ""
            for log_entry in reversed(script_logs):
                log_entry_lower = log_entry.lower()
                if "link generation timed out" in log_entry_lower: 
                    last_error_log = log_entry
                    timeout_occurred = True
                    break 
                
                if any( (indicator.startswith("DRIVEBOT server choice <.+>") and re.search(indicator, log_entry, re.IGNORECASE)) or 
                         (not indicator.startswith("DRIVEBOT server choice <.+>") and indicator.lower() in log_entry_lower)
                         for indicator in failure_indicators):
                    if not last_error_log or len(log_entry) > len(last_error_log): 
                         last_error_log = log_entry
            
            if timeout_occurred: 
                extracted_error = "Link generation (FastCloud) timed out, please try again."
            elif last_error_log:
                parts = re.split(r'(?:Error|FATAL|Info|Warning):\s*', last_error_log, maxsplit=1, flags=re.IGNORECASE)
                extracted_error = (parts[-1] if len(parts) > 1 else last_error_log).strip()

                if "Neither 'Cloud Resume Download' nor 'Generate Cloud Link'" in extracted_error:
                     extracted_error = "Could not find required buttons on intermediate page (FastCloud)."
                elif "Exceeded maximum redirect hops" in extracted_error:
                       extracted_error = "Too many redirects encountered."
                elif "Failed to obtain a valid polling URL" in extracted_error: 
                       extracted_error = "Failed to initiate link generation process (FastCloud)."
                elif "Failed to retrieve final page content" in extracted_error:
                     extracted_error = "Could not load initial page content."
                elif "could not find the final gdindex.lol link" in extracted_error.lower(): 
                    extracted_error = "Failed to extract link after Drivebot generation step."
                elif "'Generate Link' button/element not found" in extracted_error: 
                    extracted_error = "Drivebot 'Generate Link' button missing."
                elif "DRIVEBOT server choice <.+> found, but not within a <form>" in extracted_error or \
                     "Could not determine next URL or method for DRIVEBOT server choice" in extracted_error or \
                     "Could not find a DRIVEBOT server choice button/link" in extracted_error : 
                    extracted_error = "Failed at Drivebot server selection step (button not in form or action unclear)."
                elif "All prioritized search attempts" in extracted_error:
                    extracted_error = "No supported download buttons found on the page."
            else: 
                 extracted_error = "Extraction failed. See logs for details."

            result["error"] = extracted_error[:250] 
            status_code = 200 

    except Exception as e:
        app.logger.error(f"FATAL API Handler Error: {e}", exc_info=True)
        script_logs.append(f"FATAL API Handler Error: An unexpected server error occurred.")
        result["success"] = False
        result["error"] = "Internal server error processing the request."
        status_code = 500

    finally:
        result["logs"] = script_logs
        response = make_response(jsonify(result), status_code)
        return response

# --- Self-Ping Endpoint ---
@app.route('/ping', methods=['GET'])
def ping_service():
    app.logger.info("Ping endpoint called successfully.")
    return "pong", 200

# --- Self-Ping Background Task ---
def self_ping_task():
    render_external_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_external_url:
        app.logger.warning("RENDER_EXTERNAL_URL environment variable not found. Self-ping task will not run.")
        return
    ping_url = f"{render_external_url}/ping"
    # The log message will now reflect the new SELF_PING_INTERVAL_SECONDS value
    app.logger.info(f"Self-ping task started. Will ping {ping_url} every {SELF_PING_INTERVAL_SECONDS} seconds.")

    while True:
        time.sleep(SELF_PING_INTERVAL_SECONDS) 
        try:
            app.logger.info(f"Self-ping: Sending GET request to {ping_url}")
            response = requests.get(ping_url, timeout=PING_REQUEST_TIMEOUT)
            if response.status_code == 200:
                app.logger.info(f"Self-ping successful (status {response.status_code}).")
            else:
                app.logger.warning(f"Self-ping to {ping_url} received non-200 status: {response.status_code}")
        except requests.exceptions.Timeout:
            app.logger.warning(f"Self-ping to {ping_url} timed out after {PING_REQUEST_TIMEOUT}s.")
        except requests.exceptions.RequestException as e:
            app.logger.error(f"Self-ping to {ping_url} failed: {e}")
        except Exception as e:
            app.logger.error(f"Unexpected error in self_ping_task: {e}", exc_info=True)

# --- Run Flask App ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    if os.environ.get("RENDER_EXTERNAL_URL"):
        ping_thread = threading.Thread(target=self_ping_task, daemon=True)
        ping_thread.start()
        app.logger.info("Self-ping thread initiated.")
    else:
        app.logger.info("Self-ping not started (RENDER_EXTERNAL_URL not found - likely local development).")

    app.logger.info(f"Starting Flask server on host 0.0.0.0, port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
