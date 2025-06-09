# hubcloud_api/app.py

import requests
import time
import re
from urllib.parse import urljoin, urlparse, unquote
import traceback
import sys
import json
import os
from flask import Flask, request, jsonify, make_response # Import Flask components
import threading # For self-ping
import logging # For better logging

# Try importing lxml, fall back to html.parser if not installed
try:
    from bs4 import BeautifulSoup
    PARSER = "lxml"
    LXML_AVAILABLE = True
except ImportError:
    from bs4 import BeautifulSoup
    PARSER = "html.parser"
    LXML_AVAILABLE = False
    # Print warning to stderr so it appears in Render/console logs
    print("Warning: lxml not found, using html.parser.", file=sys.stderr)

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Basic Logging Configuration ---
# Configure logging to see messages from self-pinger and app
if not app.debug: # Avoid duplicate handlers if Flask's debug mode is on
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    stream_handler.setFormatter(formatter)

    app.logger.addHandler(stream_handler)
    app.logger.setLevel(logging.INFO)
    # Also configure root logger if other libraries use it
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


# --- Configuration ---
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
}
REQUEST_TIMEOUT = 30 # In seconds
DRIVE_PREFERRED_BUTTON_TEXTS = [
    r'Download\s*\[FSL Server\]',
    r'Download\s*File\s*\[\s*\d+(\.\d+)?\s*(GB|MB)\s*\]',
    r'Download\s*\[PixelServer\s*:\s*\d+\]',
    r'Download\s*\[Server\s*:\s*\d+Gbps\]'
]
DRIVE_FINAL_LINK_HINTS = ['r2.dev', 'fsl.pub', '/dl/', '.cdn.', 'storage.', 'pixeldrain.com/api/file/']
DRIVE_INTERMEDIATE_DOMAINS = [
    'gamerxyt.com', 'adf.ly', 'linkvertise.com', 'tinyurl.com',
    'cdn.ampproject.org', 'bloggingvector.shop', 'newssongs.co.in',
]

# --- Self-Ping Configuration (MODIFIED FOR AGGRESSIVE PING) ---
SELF_PING_INTERVAL_SECONDS = 45  # Ping every 45 seconds to keep it hot
PING_REQUEST_TIMEOUT = 20 # Timeout for the self-ping request itself


# --- Helper Functions (No changes needed below) ---
def drive_is_intermediate_link(url):
    if not url or not isinstance(url, str) or not url.startswith('http'): return False
    try:
        domain = urlparse(url).netloc.lower()
        return any(domain == intermediate or domain.endswith('.' + intermediate) for intermediate in DRIVE_INTERMEDIATE_DOMAINS)
    except Exception: return False

def drive_extract_final_download_link(soup, base_url, log_entries):
    direct_link = None
    found_link = False
    log_entries.append("(drive) Searching for preferred button text...")
    for pattern in DRIVE_PREFERRED_BUTTON_TEXTS:
        try:
            potential_matches = soup.find_all(['a', 'button'], string=re.compile(pattern, re.IGNORECASE))
            for match in potential_matches:
                href = None
                if match.name == 'a': href = match.get('href')
                elif match.name == 'button':
                    link_inside = match.find('a', href=True)
                    if link_inside: href = link_inside.get('href')
                    else:
                        onclick_attr = match.get('onclick')
                        if onclick_attr and 'window.location' in onclick_attr:
                            href_match = re.search(r"window\.location(?:.href)?\s*=\s*['\"]([^'\"]+)['\"]", onclick_attr)
                            if href_match: href = href_match.group(1)
                if href and isinstance(href, str) and href.strip() and not href.startswith(('#', 'javascript:')):
                    temp_link = urljoin(base_url, href.strip())
                    if any(hint in temp_link for hint in DRIVE_FINAL_LINK_HINTS) and not drive_is_intermediate_link(temp_link):
                        direct_link = temp_link
                        found_link = True
                        log_entries.append(f"(drive) Found via preferred text '{pattern}': {direct_link}")
                        break
                    else: log_entries.append(f"(drive) Found preferred text '{pattern}' but resolved href '{temp_link}' doesn't look final or is intermediate.")
            if found_link: break
        except Exception as e:
            log_entries.append(f"(drive) Error during preferred text search for pattern '{pattern}': {e}")
            continue

    if not found_link:
        log_entries.append("(drive) Preferred text not found/yielded final link. Searching for links with FINAL_LINK_HINTS...")
        potential_links = soup.find_all('a', href=True)
        for link_tag in potential_links:
            href = link_tag.get('href', '')
            if href and isinstance(href, str):
               href = href.strip()
               if href and any(hint in href for hint in DRIVE_FINAL_LINK_HINTS):
                   abs_href = urljoin(base_url, href)
                   if not drive_is_intermediate_link(abs_href):
                        direct_link = abs_href
                        found_link = True
                        log_entries.append(f"(drive) Found plausible final link via hint in href: {direct_link}")
                        break

    if direct_link:
         if drive_is_intermediate_link(direct_link):
              log_entries.append(f"(drive) Warning: Link '{direct_link}' looked final but resolved to an intermediate domain. Discarding.")
              return None
         return direct_link
    else:
        log_entries.append("(drive) No final-looking download link found by drive methods.")
        return None

# --- Core Function for 'drive' links ---
def handle_drive_link(session, hubcloud_url):
    current_url = hubcloud_url
    log_entries = []
    final_link = None
    try:
        log_entries.append(f"Processing Drive Link: {current_url}")
        initial_headers = DEFAULT_HEADERS.copy(); initial_headers['Referer'] = 'https://google.com/'
        response_get = session.get(current_url, headers=initial_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response_get.raise_for_status()
        session.headers.update(DEFAULT_HEADERS); session.headers['Referer'] = response_get.url
        soup_get = BeautifulSoup(response_get.text, PARSER)
        current_url = response_get.url
        log_entries.append(f"(drive) Initial page fetched (Status: {response_get.status_code}, URL: {current_url})")

        form_data = {}
        log_entries.append("(drive) Searching for POST form data...")
        form = soup_get.find('form', {'method': re.compile('post', re.IGNORECASE)})
        if form:
            inputs = form.find_all('input', {'type': 'hidden'})
            for input_tag in inputs:
                name = input_tag.get('name'); value = input_tag.get('value')
                if name and value is not None: form_data[name] = value
            log_entries.append(f"(drive) Found form data: {form_data}")

        if 'op' not in form_data or 'id' not in form_data:
            log_entries.append("(drive) Form data incomplete, searching scripts...")
            scripts = soup_get.find_all('script')
            script_content = "\n".join([script.string for script in scripts if script.string])
            op_match = re.search(r'["\']op["\']\s*[:=]\s*["\']([^"\']+?)["\']', script_content)
            id_match = re.search(r'["\'](id|file_id)["\']\s*[:=]\s*["\']([^"\']+?)["\']', script_content)
            rand_match = re.search(r'["\']rand["\']\s*[:=]\s*["\']([^"\']+?)["\']', script_content)
            if op_match and 'op' not in form_data: form_data['op'] = op_match.group(1)
            if id_match and 'id' not in form_data: form_data['id'] = id_match.group(2)
            if rand_match and 'rand' not in form_data: form_data['rand'] = rand_match.group(1)
            if 'op' not in form_data or form_data.get('op') in ['download0', '']: form_data['op'] = 'download1'
            if 'id' not in form_data:
                try:
                    parsed_url = urlparse(current_url)
                    path_parts = unquote(parsed_url.path).strip('/').split('/')
                    potential_id = None
                    if len(path_parts) >= 2 and path_parts[0] == 'drive': potential_id = path_parts[1]
                    elif len(path_parts) >= 1 and path_parts[0]: potential_id = path_parts[0]
                    if potential_id:
                        form_data['id'] = potential_id
                        log_entries.append(f"(drive) Extracted 'id' from URL path: {form_data['id']}")
                except Exception as e: log_entries.append(f"(drive) Error extracting 'id' from URL path: {e}")

        if 'op' not in form_data or 'id' not in form_data:
            log_entries.append("Error: Could not find required 'op' and 'id' data for POST.")
            return None, log_entries

        log_entries.append(f"(drive) Using POST data: {form_data}")
        post_url = current_url
        session.headers['Referer'] = current_url
        response_post1 = session.post(post_url, data=form_data, timeout=REQUEST_TIMEOUT + 15, allow_redirects=True)
        response_post1.raise_for_status()
        soup_post1 = BeautifulSoup(response_post1.text, PARSER)
        current_url = response_post1.url
        session.headers['Referer'] = current_url
        log_entries.append(f"(drive) POST request successful (Status: {response_post1.status_code}, Landed on URL: {current_url})")

        log_entries.append(f"(drive) Analyzing response from {current_url}...")
        final_link = drive_extract_final_download_link(soup_post1, current_url, log_entries)
        if final_link:
            log_entries.append(f"(drive) Found final link directly after first POST.")
            return final_link, log_entries

        intermediate_link = None
        potential_links = soup_post1.find_all('a', href=True)
        for link_tag in potential_links:
            href = link_tag.get('href', '')
            if href and isinstance(href, str):
                href = href.strip()
                if href and not href.startswith(('#', 'javascript:')):
                    abs_href = urljoin(current_url, href)
                    if drive_is_intermediate_link(abs_href):
                         intermediate_link = abs_href
                         log_entries.append(f"(drive) Found intermediate link to follow: {intermediate_link}")
                         break
        if intermediate_link:
            log_entries.append(f"(drive) Following intermediate link: {intermediate_link}")
            time.sleep(2)
            response_intermediate = session.get(intermediate_link, timeout=REQUEST_TIMEOUT + 30, allow_redirects=True)
            intermediate_final_url = response_intermediate.url
            session.headers['Referer'] = intermediate_final_url
            content_type = response_intermediate.headers.get('Content-Type', '').lower()
            if 'html' not in content_type:
                log_entries.append(f"(drive) Intermediate link response not HTML ({content_type}). Status: {response_intermediate.status_code}. URL: {intermediate_final_url}")
                if any(hint in intermediate_final_url for hint in DRIVE_FINAL_LINK_HINTS) and not drive_is_intermediate_link(intermediate_final_url):
                        log_entries.append(f"(drive) Intermediate GET redirected directly to final link.")
                        return intermediate_final_url, log_entries
                elif 'Location' in response_intermediate.headers:
                     final_redirect_url = urljoin(intermediate_link, response_intermediate.headers['Location'])
                     if any(hint in final_redirect_url for hint in DRIVE_FINAL_LINK_HINTS) and not drive_is_intermediate_link(final_redirect_url):
                          log_entries.append(f"(drive) Found final link via intermediate redirect header.")
                          return final_redirect_url, log_entries
                     else: log_entries.append(f"(drive) Intermediate redirect header doesn't look final: {final_redirect_url}")
                else:
                    try:
                        response_text = response_intermediate.text
                        url_matches = re.findall(r'https?://[^\s\'"<]+', response_text)
                        for url_match in url_matches:
                            if any(hint in url_match for hint in DRIVE_FINAL_LINK_HINTS[-3:]) and not drive_is_intermediate_link(url_match):
                                log_entries.append(f"(drive) Found plausible final link in non-HTML intermediate response.")
                                return url_match, log_entries
                        log_entries.append(f"(drive) No plausible final link found in non-HTML intermediate response body.")
                    except Exception as decode_err: log_entries.append(f"(drive) Failed to decode/search non-HTML intermediate response: {decode_err}")
                log_entries.append("Error: Intermediate link didn't yield a final file or recognizable redirect.")
                return None, log_entries
            response_intermediate.raise_for_status()
            soup_intermediate = BeautifulSoup(response_intermediate.text, PARSER)
            log_entries.append(f"(drive) Intermediate page fetched (Status: {response_intermediate.status_code}, Final URL: {intermediate_final_url})")
            final_link = drive_extract_final_download_link(soup_intermediate, intermediate_final_url, log_entries)
            if final_link:
                 log_entries.append(f"(drive) Found final link after following intermediate link.")
                 return final_link, log_entries
            else:
                 log_entries.append("Error: Could not find final link after following intermediate link.")
                 return None, log_entries
        else:
             log_entries.append("Error: No final link or recognized intermediate link found in the first POST response.")
             return None, log_entries
    except requests.exceptions.Timeout as e:
        log_entries.append(f"Error: Request timed out during process for {hubcloud_url}. Details: {e}")
        return None, log_entries
    except requests.exceptions.HTTPError as e:
         log_entries.append(f"Error: HTTP error occurred processing {hubcloud_url}. Status: {e.response.status_code}. URL: {e.request.url}. Details: {e}")
         return None, log_entries
    except requests.exceptions.RequestException as e:
        log_entries.append(f"Error: Network/Request error processing {hubcloud_url}. Details: {e}")
        return None, log_entries
    except Exception as e:
        log_entries.append(f"FATAL ERROR during drive link processing: {e}\n{traceback.format_exc()}")
        return None, log_entries

# --- Helper/Core Functions for 'video' links ---
def video_fetch_and_parse(session, url, referer=None, log_entries=None):
    if log_entries is None: log_entries = []
    log_entries.append(f"(video) Fetching: {url}")
    current_headers = session.headers.copy()
    if referer: current_headers['Referer'] = referer
    try:
        response = session.get(url, headers=current_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        raw_html = response.text
        session.headers['Referer'] = response.url
        log_entries.append(f"(video) Successfully fetched (Status: {response.status_code}, Landed on: {response.url})")
        soup = BeautifulSoup(raw_html, PARSER)
        return soup, raw_html, response.url, log_entries
    except requests.exceptions.Timeout:
        log_entries.append(f"Error: Request timed out ({REQUEST_TIMEOUT}s) for {url}")
        return None, None, url, log_entries
    except requests.exceptions.HTTPError as http_err:
        log_entries.append(f"Error: HTTP error {http_err.response.status_code} for {url}")
        return None, None, getattr(http_err.response, 'url', url), log_entries
    except requests.exceptions.RequestException as req_err:
        log_entries.append(f"Error: Request error for {url}: {req_err}")
        return None, None, url, log_entries
    except Exception as e:
        log_entries.append(f"Error: Unexpected error parsing {url}: {e}")
        return None, None, url, log_entries

def video_find_intermediate_link(soup, initial_url, log_entries):
    if not soup: return None, log_entries
    log_entries.append("(video) Searching for intermediate 'Generate...' link...")
    generate_link_tag = None
    search_text_pattern = 'Generate Direct Download Link'
    href_pattern = 'gamerxyt.com/hubcloud.php'
    found = False
    potential_containers = soup.find_all('div', class_=re.compile(r'vd|buttons', re.IGNORECASE))
    if not potential_containers: potential_containers = [soup]
    for container in potential_containers:
        generate_link_tag = container.find('a', string=lambda text: text and search_text_pattern in text.strip())
        if generate_link_tag: log_entries.append(f"(video) Found intermediate link by text: '{search_text_pattern}'"); found = True; break
        if not generate_link_tag:
             generate_link_tag = container.find('a', href=lambda href: href and href_pattern in href)
             if generate_link_tag: log_entries.append(f"(video) Found intermediate link by href pattern: '{href_pattern}'"); found = True; break
    if found and generate_link_tag and generate_link_tag.get('href'):
        intermediate_url = urljoin(initial_url, generate_link_tag.get('href').strip())
        log_entries.append(f"(video) Resolved intermediate link: {intermediate_url}")
        return intermediate_url, log_entries
    else:
        log_entries.append(f"Error: Could not find the intermediate 'Generate' <a> tag using text OR href search.")
        return None, log_entries

def video_find_final_download_link(soup, raw_html, intermediate_url, log_entries):
    if not soup: return None, log_entries
    log_entries.append("(video) Searching for final download link on intermediate page...")
    final_link_tag = None; link_type = "Unknown"
    search_priorities = [
        {'type': 'PixelDrain Button', 'tag': 'a', 'attrs': {'class': re.compile(r'btn-success', re.I)}, 'text_pattern': r'Download\s*\[PixelServer'},
        {'type': 'FSL Server Button', 'tag': 'a', 'attrs': {'class': re.compile(r'btn-success', re.I)}, 'text_pattern': r'Download\s*\[FSL Server'},
        {'type': 'Download File [Size] Button', 'tag': 'a', 'attrs': {'class': re.compile(r'btn-success', re.I)}, 'text_pattern': r'Download File\s*\['},
        {'type': 'Generic Download Button', 'tag': 'a', 'attrs': {'class': re.compile(r'btn', re.I)}, 'text_pattern': r'^Download( Now)?$'},
        {'type': 'Link with PixelDrain Hint', 'tag': 'a', 'attrs': {'href': re.compile(r'pixel', re.I)}},
        {'type': 'Link with FSL Hint', 'tag': 'a', 'attrs': {'href': re.compile(r'fsl\.pub', re.I)}}, ]
    for priority in search_priorities:
        link_type = priority['type']; log_entries.append(f"(video) Trying strategy: {link_type}")
        potential_tags = soup.find_all(priority['tag'], **priority.get('attrs', {}))
        for tag in potential_tags:
            if 'text_pattern' in priority:
                tag_text = tag.get_text(strip=True);
                if not re.search(priority['text_pattern'], tag_text, re.IGNORECASE): continue
            href_value = tag.get('href','').strip()
            if href_value and not href_value.startswith(('#', 'javascript:')): final_link_tag = tag; break
        if final_link_tag: log_entries.append(f"(video) Found potential tag via strategy: {link_type}"); break
    if final_link_tag:
        href_value = final_link_tag.get('href','').strip(); final_url = urljoin(intermediate_url, href_value)
        log_entries.append(f"(video) Resolved final link: {final_url}")
        if not urlparse(final_url).scheme or not urlparse(final_url).netloc:
             log_entries.append(f"Error: Resolved final URL '{final_url}' seems invalid."); return None, log_entries
        return final_url, log_entries
    else:
        log_entries.append("FAILED TO FIND VIDEO DOWNLOAD LINK"); log_entries.append("Could not find a usable download link.")
        return None, log_entries

def handle_video_link(session, hubcloud_url):
    final_link = None; log_entries = []
    try:
        log_entries.append(f"Processing Video Link: {hubcloud_url}"); session.headers.update(DEFAULT_HEADERS)
        initial_soup, _, initial_final_url, log_entries = video_fetch_and_parse(session, hubcloud_url, log_entries=log_entries)
        if not initial_soup: log_entries.append("Error: Failed to fetch or parse initial page."); return None, log_entries
        intermediate_link, log_entries = video_find_intermediate_link(initial_soup, initial_final_url, log_entries)
        if not intermediate_link: log_entries.append("Error: Could not find the intermediate link."); return None, log_entries
        time.sleep(1)
        intermediate_soup, intermediate_raw_html, intermediate_final_url, log_entries = video_fetch_and_parse(session, intermediate_link, referer=initial_final_url, log_entries=log_entries)
        if not intermediate_soup: log_entries.append("Error: Failed to fetch or parse intermediate page."); return None, log_entries
        final_link, log_entries = video_find_final_download_link(intermediate_soup, intermediate_raw_html, intermediate_final_url, log_entries)
    except Exception as e: log_entries.append(f"FATAL ERROR during video link processing: {e}\n{traceback.format_exc()}"); return None, log_entries
    return final_link, log_entries


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
@app.route('/api/hubcloud', methods=['POST', 'OPTIONS'])
def hubcloud_bypass_api():
    if request.method == 'OPTIONS':
        return _build_cors_preflight_response()

    elif request.method == 'POST':
        logs = []
        result = {"success": False, "error": "Request processing failed", "finalUrl": None, "logs": logs}
        hubcloud_url = None
        final_download_link = None
        status_code = 500

        try:
            try:
                data = request.get_json()
                if not data:
                    raise ValueError("No JSON data received")
                hubcloud_url = data.get('hubcloudUrl')
                logs.append("Received JSON POST body.")
            except Exception as e:
                logs.append(f"Error: Could not parse JSON request body: {e}")
                result["error"] = "Invalid or missing JSON in request body"
                status_code = 400
                return _corsify_actual_response(jsonify(result)), status_code

            if not hubcloud_url or not isinstance(hubcloud_url, str):
                logs.append("Error: hubcloudUrl missing or invalid in request.")
                result["error"] = "Missing or invalid hubcloudUrl in request body"
                status_code = 400
                return _corsify_actual_response(jsonify(result)), status_code

            logs.append(f"Processing URL: {hubcloud_url}")
            parsed_start_url = urlparse(hubcloud_url)
            if not parsed_start_url.scheme or not parsed_start_url.netloc:
                 logs.append(f"Error: Invalid URL format: {hubcloud_url}")
                 result["error"] = f"Invalid URL format provided: {hubcloud_url}"
                 status_code = 400
                 return _corsify_actual_response(jsonify(result)), status_code

            session = requests.Session()
            path = parsed_start_url.path.lower()

            if path.startswith('/drive/'):
                logs.append("Detected '/drive/' link type.")
                final_download_link, script_logs = handle_drive_link(session, hubcloud_url)
                logs.extend(script_logs)
            elif path.startswith('/video/'):
                 logs.append("Detected '/video/' link type.")
                 final_download_link, script_logs = handle_video_link(session, hubcloud_url)
                 logs.extend(script_logs)
            else:
                 error_msg = f"Unknown HubCloud URL type (path: {parsed_start_url.path})"
                 logs.append(f"Error: {error_msg}")
                 result["error"] = error_msg

            if final_download_link:
                result["success"] = True
                result["finalUrl"] = final_download_link
                result["error"] = None
                status_code = 200
            else:
                result["success"] = False
                if result.get("error", "Request processing failed") == "Request processing failed":
                     failure_indicators = ["Error:", "FATAL ERROR", "FAILED", "Could not find", "timed out"]
                     extracted_error = "Extraction Failed (Check logs)"
                     for log_entry in reversed(logs):
                        if any(indicator in log_entry for indicator in failure_indicators):
                             parts = log_entry.split(":", 1)
                             extracted_error = parts[-1].strip() if len(parts) > 1 else log_entry.strip()
                             break
                     result["error"] = extracted_error[:150]

        except Exception as e:
            app.logger.error(f"FATAL API Handler Error: {e}", exc_info=True)
            logs.append(f"FATAL API Handler Error: An unexpected server error occurred.")
            result["success"] = False
            result["error"] = "Internal server error processing request."
            status_code = 500

        finally:
            result["logs"] = logs
            return _corsify_actual_response(jsonify(result)), status_code
    else:
        return jsonify({"error": "Method Not Allowed"}), 405

# --- Self-Ping Endpoint ---
@app.route('/ping', methods=['GET'])
def ping_service():
    app.logger.info("HubCloud API Ping endpoint called successfully.")
    return "pong", 200

# --- Self-Ping Background Task ---
def self_ping_task():
    render_external_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_external_url:
        app.logger.warning("RENDER_EXTERNAL_URL environment variable not found. HubCloud self-ping task will not run.")
        return

    ping_url = f"{render_external_url}/ping"
    app.logger.info(f"HubCloud self-ping task started. Will ping {ping_url} every {SELF_PING_INTERVAL_SECONDS} seconds.")

    while True:
        time.sleep(SELF_PING_INTERVAL_SECONDS)
        try:
            app.logger.info(f"HubCloud self-ping: Sending GET request to {ping_url}")
            response = requests.get(ping_url, timeout=PING_REQUEST_TIMEOUT)
            if response.status_code == 200:
                app.logger.info(f"HubCloud self-ping successful (status {response.status_code}).")
            else:
                app.logger.warning(f"HubCloud self-ping to {ping_url} received non-200 status: {response.status_code}")
        except requests.exceptions.Timeout:
            app.logger.warning(f"HubCloud self-ping to {ping_url} timed out after {PING_REQUEST_TIMEOUT}s.")
        except requests.exceptions.RequestException as e:
            app.logger.error(f"HubCloud self-ping to {ping_url} failed: {e}")
        except Exception as e:
            app.logger.error(f"Unexpected error in HubCloud self_ping_task: {e}", exc_info=True)


# --- Run Flask App ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5002))

    if os.environ.get("RENDER_EXTERNAL_URL"):
        ping_thread = threading.Thread(target=self_ping_task, daemon=True)
        ping_thread.start()
        app.logger.info("HubCloud self-ping thread initiated.")
    else:
        app.logger.info("HubCloud self-ping not started (likely local development).")

    app.logger.info(f"Starting HubCloud Flask server on host 0.0.0.0, port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
