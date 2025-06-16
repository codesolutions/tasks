import os
import pickle
import requests
import time
from config_manager import config, t

# Attempt to import Selenium, but allow the app to run without it.
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# --- JIRA INTEGRATION ---
def get_and_save_jira_session(permanent_notifications_ref):
    """Opens Chrome via Selenium for login and saves session cookies."""
    if not SELENIUM_AVAILABLE:
        permanent_notifications_ref.append("ERROR: Selenium library not found. Please run 'pip install selenium'.")
        return False

    jira_url = config.get("JIRA_URL")
    driver_path = config.get("CHROME_DRIVER_PATH")
    session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.get("JIRA_SESSION_FILE"))

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
        
        # Use a copy to avoid modification during iteration
        for p_notif in permanent_notifications_ref[:]:
            if p_notif == t('jira_login_prompt'):
                permanent_notifications_ref.remove(p_notif)
        
        driver.quit()
        return True
    except Exception as e:
        print(f"[ERROR] Failed to get Jira session. Check CHROME_DRIVER_PATH.")
        print(f"   Details: {e}")
        return False

def get_jira_issue_details(issue_id, permanent_notifications_ref):
    """Fetches details for a specific Jira issue using a saved session."""
    session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.get("JIRA_SESSION_FILE"))
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

def fetch_and_cache_jira_data(issue_id, jira_cache, jira_cache_lock, permanent_notifications_ref, force=False):
    """Wrapper to fetch Jira data and store it in the global cache."""
    with jira_cache_lock:
        now = time.time()
        if not force and issue_id in jira_cache and (now - jira_cache[issue_id].get('timestamp', 0) < 300):
            return
        
        issue_data, remotelink_data = get_jira_issue_details(issue_id, permanent_notifications_ref)
        if issue_data:
            jira_cache[issue_id] = {'data': issue_data, 'remotelinks': remotelink_data, 'timestamp': now}

def jira_data_poller(app_data, data_lock, jira_cache, jira_cache_lock, permanent_notifications_ref):
    """Background thread to periodically refresh Jira data for all known tickets."""
    while True:
        with data_lock:
            all_ticket_ids = {tid for p in app_data.get("sub_tasks", {}).values() for tid in p}
        for ticket_id in all_ticket_ids:
            fetch_and_cache_jira_data(ticket_id, jira_cache, jira_cache_lock, permanent_notifications_ref)
        time.sleep(300)
