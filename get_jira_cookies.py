# ----------------------------------------------------------------------
# SCRIPT 1: get_jira_cookies.py
#
# PURPOSE:
# This script uses Selenium to open a browser window, waits for you to
# log in to Jira manually, and then saves your session cookies to a file.
# You only need to run this script once per session (i.e., when your
# old session expires).
#
# REQUIREMENTS:
# pip install selenium pickle
# You also need to download the appropriate WebDriver for your browser.
# For Chrome: https://googlechromelabs.github.io/chrome-for-testing/
# ----------------------------------------------------------------------
#    python3 -m venv path/to/venv
#    source path/to/venv/bin/activate
#    python3 -m pip install xyz
# wget https://storage.googleapis.com/chrome-for-testing-public/137.0.7151.70/linux64/chromedriver-linux64.zip
# unzip to same folder in chromedriver-linux64

import os
import time
import pickle
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# --- CONFIGURATION ---
# IMPORTANT: Update this path to where you have downloaded chromedriver
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHROME_DRIVER_PATH = os.path.join(SCRIPT_DIR, "chromedriver-linux64/chromedriver")
# The URL of your Jira instance's dashboard
JIRA_URL = 'https://MY_ORGANISATION_HERE.atlassian.net/jira/your-work'
# The file where cookies will be saved
SESSION_FILE = 'jira_session.pkl'

def get_and_save_session_data():
    """
    Opens a Chrome browser, waits for the user to log in, and saves
    both cookies and localStorage data.
    """
    print("-> Starting browser...")
    chrome_options = Options()
    service = Service(executable_path=CHROME_DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=chrome_options)

    print(f"-> Navigating to: {JIRA_URL}")
    driver.get(JIRA_URL)

    print("\n" + "="*50)
    print("!!! ACTION REQUIRED !!!")
    print("Please log in to Jira in the browser window that just opened.")
    print("Complete the entire login process, including any SSO or MFA.")
    input("Once you are fully logged in, press Enter here to continue...")
    print("="*50 + "\n")


    print("-> Capturing session data...")
    cookies = driver.get_cookies()
    
    # Execute JavaScript to get all localStorage items
    local_storage = driver.execute_script("return window.localStorage;")
    
    if not cookies and not local_storage:
        print("[ERROR] No cookies or localStorage data were captured. Did you log in successfully?")
        driver.quit()
        return

    # Bundle both cookies and localStorage into a single dictionary
    session_data = {
        'cookies': cookies,
        'local_storage': local_storage
    }

    print(f"-> Found {len(cookies)} cookies and {len(local_storage)} localStorage items.")
    print(f"-> Saving session bundle to '{SESSION_FILE}'...")
    with open(SESSION_FILE, 'wb') as f:
        pickle.dump(session_data, f)

    print(f"-> Session data saved successfully!")
    print("-> Closing browser.")
    driver.quit()

if __name__ == '__main__':
    get_and_save_session_data()