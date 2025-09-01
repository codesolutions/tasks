import os
import pickle
import requests
import time
import copy
import threading
import logging
import sys
import queue
import re

from . import config_manager
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

jira_request_queue = queue.Queue()
jira_in_flight = set() # To track tasks currently in the queue or being fetched

jira_cache = {}
jira_cache_lock = threading.Lock()
config_manager.load_config()
config = config_manager.config

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
JIRA_CACHE_FILE = os.path.join(SCRIPT_DIR, "jira_cache.pkl")

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False



def get_and_save_web_session(service_name, login_url, session_file, driver_path, permanent_notifications_ref):
    """
    Handles interactive browser login to capture session cookies for a given service.
    
    Args:
        service_name (str): The name of the service (e.g., "Jira", "Trello").
        login_url (str): The URL to open for the user to log in.
        session_file (str): The path to save the session cookie file.
        driver_path (str): Path to the chromedriver executable.
        permanent_notifications_ref (list): A reference to the list of permanent notifications.

    Returns:
        bool: True if the session was captured successfully, False otherwise.
    """

    print(f"\n--- {service_name} Login Process ---")

    print(f"\n--- {login_url} login url debug ---")
    print(f"\n--- {driver_path} driver path ---")
    time.sleep(5)

    if not SELENIUM_AVAILABLE:
        permanent_notifications_ref.append(f"ERROR: Selenium library not found for {service_name} login.")
        return False

    if not login_url or not os.path.exists(driver_path):
        permanent_notifications_ref.append(f"ERROR: URL or CHROME_DRIVER_PATH is invalid for {service_name} in config.json")
        return False

    print(f"\n--- {service_name} Login Process ---")
    print("-> Starting browser...")
    try:
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(service=service, options=Options())
        driver.get(login_url)

        print("\n" + "="*50)
        print(f"!!! ACTION REQUIRED: {service_name} !!!")
        print(f"A browser window has been opened. Please log in to {service_name}.")
        print("Complete the entire login process, including any SSO or MFA.")
        input(f"===> Once you are fully logged in to {service_name}, press Enter here to continue...")
        print("="*50 + "\n")

        print("-> Capturing session data...")
        cookies = driver.get_cookies()
        if not cookies:
            print(f"[ERROR] No cookies were captured for {service_name}. Did you log in successfully?")
            driver.quit()
            return False

        full_session_path = os.path.join(SCRIPT_DIR, session_file)
        with open(full_session_path, 'wb') as f:
            pickle.dump(cookies, f)
        print(f"-> {service_name} session data saved successfully to '{full_session_path}'!")

        if f"{t('jira_login_prompt', service=service_name)}" in permanent_notifications_ref:
            permanent_notifications_ref.remove(f"{t('jira_login_prompt', service=service_name)}")

        driver.quit()
        return True
    except Exception as e:
        print(f"[ERROR] Failed to get {service_name} session. Check CHROME_DRIVER_PATH.")
        print(f"   Details: {e}")
        return False






def load_jira_cache():
    """Loads the Jira cache from a file on startup and returns it."""
    try:
        with open(JIRA_CACHE_FILE, 'rb') as f:
            return pickle.load(f)
    except (FileNotFoundError, EOFError, pickle.UnpicklingError):
        # File doesn't exist or is empty/corrupt, start with an empty cache.
        return {}

def save_jira_cache(cache_to_save, lock_to_use):
    """Saves the provided cache object to a file using the provided lock."""
    with lock_to_use:
        try:
            with open(JIRA_CACHE_FILE, 'wb') as f:
                pickle.dump(cache_to_save, f)
        except IOError:
            logging.info(f"File save failed: {JIRA_CACHE_FILE}")
            pass

def get_trello_card_details(card_id, permanent_notifications_ref):
    
    global config
    logging.info(f"Get trello card {card_id}")
    session_file = os.path.join(SCRIPT_DIR, config.get("TRELLO_SESSION_FILE"))
    jira_base_url = config.get("TRELLO_URL")

    if not os.path.exists(session_file):
        if f"{t('jira_login_prompt', service='Trello')}" not in permanent_notifications_ref: permanent_notifications_ref.append(f"{t('jira_login_prompt', service='Trello')}")
        return None

    session = requests.Session()
    try:
        with open(session_file, 'rb') as f:
            for cookie in pickle.load(f):
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
    except Exception:
        if t('jira_session_error') not in permanent_notifications_ref: permanent_notifications_ref.append(t('jira_session_error'))
        logging.info(f"{t('jira_session_error')}")
        return None


    issue_url = f'{jira_base_url}/1/cards/{card_id}?fields=id&actions=commentCard%2CcopyCommentCard%2CcreateCard%2CcreateInboxCard%2CcopyCard%2CcopyInboxCard&actions_display=true&action_reactions=true&actions_limit=50'

    
    test = session.get('https://trello.com/u/u/boards')
    if test.status_code != 200:
        if f"{t('jira_login_prompt', service='Trello')}" not in permanent_notifications_ref: 
            permanent_notifications_ref.append(f"{t('jira_login_prompt', service='Trello')}")

    try:
        issue_response = session.get(issue_url, timeout=15)
        issue_response.raise_for_status()
        issue_data = issue_response.json()
        return issue_data
    except requests.exceptions.HTTPError as e:
        logging.error(f"Failed to get: {issue_url}")
        # msg = t('jira_auth_error') if e.response.status_code in [401, 403] else t('jira_http_error', status=e.response.status_code)
        # if msg not in permanent_notifications_ref: permanent_notifications_ref.append(msg)
    except requests.exceptions.RequestException as e:
        msg = t('jira_generic_error', e=str(e))
        logging.error(f"Failed to get: {issue_url} with error: {msg}")
        # if msg not in permanent_notifications_ref: permanent_notifications_ref.append(msg)
    return None

def get_jira_issue_details(issue_id, permanent_notifications_ref):
    global config
    logging.info(f"Get jira issue {issue_id}")
    session_file = os.path.join(SCRIPT_DIR, config.get("JIRA_SESSION_FILE"))
    jira_base_url = config.get("JIRA_URL")

    if not os.path.exists(session_file):
        if f"{t('jira_login_prompt', service='Jira')}" not in permanent_notifications_ref: permanent_notifications_ref.append(f"{t('jira_login_prompt', service='Jira')}")
        return None, None

    session = requests.Session()
    try:
        with open(session_file, 'rb') as f:
            for cookie in pickle.load(f):
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
    except Exception:
        if t('jira_session_error') not in permanent_notifications_ref: permanent_notifications_ref.append(t('jira_session_error'))
        logging.info(f"{t('jira_session_error')}")
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
        logging.error(f"Failed to get: {issue_url}")
        msg = t('jira_auth_error') if e.response.status_code in [401, 403] else t('jira_http_error', status=e.response.status_code)
        if msg not in permanent_notifications_ref: permanent_notifications_ref.append(msg)
        if f"{t('jira_login_prompt', service='Jira')}" not in permanent_notifications_ref: permanent_notifications_ref.append(f"{t('jira_login_prompt', service='Jira')}")
    except requests.exceptions.RequestException as e:
        msg = t('jira_generic_error', e=str(e))
        if msg not in permanent_notifications_ref: permanent_notifications_ref.append(msg)
        if f"{t('jira_login_prompt', service='Jira')}" not in permanent_notifications_ref: permanent_notifications_ref.append(f"{t('jira_login_prompt', service='Jira')}")
    return None, None

def get_trello_id(data):
    jira_description = data.get('fields', {}).get('description', "")
    trello_id = ""
    if (jira_description and isinstance(jira_description, str)):
        # API v2 way, no objects
        pattern = r"(https://trello\.com/c/[^]]+)"
        match = re.search(pattern, jira_description)
        if match:
            trello_link = match.group(0)
            trello_id = trello_link.split('/')[-1]
    return trello_id

def jira_queue_worker(stop_event, permanent_notifications_ref, cache_ref, lock_ref):
    """
    Worker thread that processes Jira data requests from a queue, acting on a shared cache.
    """
    while not stop_event.is_set():
        try:
            issue_id = jira_request_queue.get(timeout=1)

            # Fetch new data
            issue_data, remotelink_data = get_jira_issue_details(issue_id, permanent_notifications_ref)
            
            #logging.debug(get_trello_card_details("TEAYSza2", permanent_notifications_ref))

            # If data was fetched successfully, update the SHARED cache
            if issue_data:
                trello_id = get_trello_id(issue_data)
                trello_data = {}
                if trello_id != "":
                    trello_data = get_trello_card_details(trello_id, permanent_notifications_ref)

                with lock_ref: # Use the passed-in lock
                    # Use the passed-in cache reference
                    cache_ref[issue_id] = {
                        'data': issue_data,
                        'trello_data': trello_data,
                        'remotelinks': remotelink_data,
                        'timestamp': time.time()
                    }
                # Save the updated shared cache to the file
                save_jira_cache(cache_ref, lock_ref)

            # Task is done, remove from the in-flight set so it can be re-queued in the future if needed
            if issue_id in jira_in_flight:
                jira_in_flight.remove(issue_id)

            jira_request_queue.task_done()

        except queue.Empty:
            # This is expected when the queue is empty, just loop again
            continue
        except Exception as e:
            logging.error(f"An error occurred in the Jira queue worker: {e}")
            # Ensure we remove from in-flight even if there was an error
            if 'issue_id' in locals() and issue_id in jira_in_flight:
                jira_in_flight.remove(issue_id)
