import os
import pickle
import requests
import time
import copy
import threading
import logging
import sys

import inc.config_manager
from inc.helpers import get_jira_ticket_from_url, t

LOG_FILE = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)),
    "debug.log"
)

logging.basicConfig(filename=LOG_FILE,
                    filemode='a',
                    format='%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.DEBUG)

jira_cache = {}
jira_cache_lock = threading.Lock()
inc.config_manager.load_config()
config = inc.config_manager.config

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
JIRA_CACHE_FILE = os.path.join(SCRIPT_DIR, "jira_cache.pkl")

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# --- JIRA INTEGRATION ---
def get_and_save_jira_session(permanent_notifications_ref):
    global config
    if not SELENIUM_AVAILABLE:
        permanent_notifications_ref.append("ERROR: Selenium library not found. Please run 'pip install selenium'.")
        return False

    jira_url = config.get("JIRA_URL")
    driver_path = config.get("CHROME_DRIVER_PATH")
    session_file = os.path.join(SCRIPT_DIR, config.get("JIRA_SESSION_FILE"))

    if not jira_url or "YOUR_ORG" in jira_url or not os.path.exists(driver_path):
        permanent_notifications_ref.append("ERROR: JIRA_URL or CHROME_DRIVER_PATH is invalid in config.json")
        return False

    print("\n--- Jira Login Process ---")
    print("-> Starting browser...")
    try:
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(service=service, options=Options())
        driver.get(jira_url)

        print("\n" + "="*50)
        print("!!! ACTION REQUIRED !!!")
        print("A browser window has been opened. Please log in to Jira.")
        print("Complete the entire login process, including any SSO or MFA.")
        input("===> Once you are fully logged in, press Enter here to continue...")
        print("="*50 + "\n")

        print("-> Capturing session data...")
        cookies = driver.get_cookies()
        if not cookies:
            print("[ERROR] No cookies were captured. Did you log in successfully?")
            driver.quit()
            return False

        with open(session_file, 'wb') as f: pickle.dump(cookies, f)
        print(f"-> Session data saved successfully to '{session_file}'!")

        if t('jira_login_prompt') in permanent_notifications:
            permanent_notifications_ref.remove(t('jira_login_prompt'))

        driver.quit()
        return True
    except Exception as e:
        print(f"[ERROR] Failed to get Jira session. Check CHROME_DRIVER_PATH.")
        print(f"   Details: {e}")
        return False



# --- NEW: Functions to load and save the cache ---
def load_jira_cache():
    """Loads the Jira cache from a file on startup."""
    global jira_cache

    try:
        with open(JIRA_CACHE_FILE, 'rb') as f:
            jira_cache = pickle.load(f)
    except (FileNotFoundError, EOFError, pickle.UnpicklingError):
        # File doesn't exist or is empty/corrupt, start with an empty cache.
        jira_cache = {}

def save_jira_cache():
    global jira_cache
    """Saves the in-memory Jira cache to a file."""
    with jira_cache_lock:
        try:
            with open(JIRA_CACHE_FILE, 'wb') as f:
                pickle.dump(jira_cache, f)
        except IOError:
            logging.info(f"File save failed: {JIRA_CACHE_FILE}")
            # Handle cases where the file cannot be written
            pass

def get_jira_issue_details(issue_id, permanent_notifications_ref):
    global config
    logging.info(f"Get jira issue {issue_id}")
    session_file = os.path.join(SCRIPT_DIR, config.get("JIRA_SESSION_FILE"))
    jira_base_url = config.get("JIRA_URL")

    if not os.path.exists(session_file):
        if t('jira_login_prompt') not in permanent_notifications_ref: permanent_notifications_ref.append(t('jira_login_prompt'))
        return None, None

    session = requests.Session()
    try:
        with open(session_file, 'rb') as f:
            for cookie in pickle.load(f):
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
    except Exception:
        if t('jira_session_error') not in permanent_notifications_ref: permanent_notifications_ref.append(t('jira_session_error'))
        return None, None

    issue_url = f'{jira_base_url}/rest/api/2/issue/{issue_id}'
    remotelink_url = f'{jira_base_url}/rest/api/2/issue/{issue_id}/remotelink'
    try:
        issue_response = session.get(issue_url, timeout=15)
        issue_response.raise_for_status()
        issue_data = issue_response.json()

        remotelink_data = []
        try:
            remotelink_response = session.get(remotelink_url, timeout=15)
            if remotelink_response.ok: remotelink_data = remotelink_response.json()
        except requests.exceptions.RequestException: pass

        return issue_data, remotelink_data
    except requests.exceptions.HTTPError as e:
        msg = t('jira_auth_error') if e.response.status_code in [401, 403] else t('jira_http_error', status=e.response.status_code)
        if msg not in permanent_notifications_ref: permanent_notifications_ref.append(msg)
    except requests.exceptions.RequestException as e:
        msg = t('jira_generic_error', e=str(e))
        if msg not in permanent_notifications_ref: permanent_notifications_ref.append(msg)
    return None, None

def fetch_and_cache_jira_data(issue_id, permanent_notifications_ref, seconds=600):
    global config, jira_cache

    now = time.time()
    if issue_id in jira_cache and (now - jira_cache[issue_id].get('timestamp', 0) < seconds):
        return

    issue_data, remotelink_data = get_jira_issue_details(issue_id, permanent_notifications_ref)

    if issue_data:
        jira_cache[issue_id] = {'data': issue_data, 'remotelinks': remotelink_data, 'timestamp': now}
        save_jira_cache()

def jira_data_poller(data, data_lock, permanent_notifications_ref):

    data_copy = copy.deepcopy(data)

    while True:

        all_ticket_ids = {
            tid for project_tasks in data_copy.get("sub_tasks", {}).values()
            for tid, details in project_tasks.items() if not details.get('hidden')
        }
            #all_ticket_ids = {tid for p in data_copy.get("sub_tasks", {}).values() for tid in p}

        for url in all_ticket_ids:
            ticket_id = get_jira_ticket_from_url(url)
            if (ticket_id != url):
                if t('jira_login_prompt') not in permanent_notifications_ref and t('jira_session_error') not in permanent_notifications_ref:
                    fetch_and_cache_jira_data(ticket_id, permanent_notifications_ref)
                    time.sleep(1)

        #if data_changed:
        #    save_data(data_ref)
        time.sleep(600)