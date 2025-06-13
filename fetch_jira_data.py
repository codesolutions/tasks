# ----------------------------------------------------------------------
# SCRIPT 2: fetch_jira_data.py (Example Usage)
#
# PURPOSE:
# This script shows how to use the saved cookies with the `requests`
# library to make authenticated API calls to Jira without opening a browser.
# This is the part you would integrate into your main application logic.
#
# REQUIREMENTS:
# pip install requests
# ----------------------------------------------------------------------
import requests
import pickle
import os
import json

# --- CONFIGURATION ---
SESSION_FILE = 'jira_session.pkl'
JIRA_BASE_URL = 'https://MY_ORGANISATION_HERE.atlassian.net'

def get_jira_issue_details(issue_id):
    """
    Fetches details for a specific Jira issue using a saved session bundle.
    """
    if not os.path.exists(SESSION_FILE):
        print(f"[ERROR] Session file '{SESSION_FILE}' not found.")
        print("Please run the 'get_jira_cookies.py' script first to log in.")
        return None

    # Create a requests session
    session = requests.Session()

    # Load the session bundle from the file
    print(f"-> Loading session data from '{SESSION_FILE}'...")
    with open(SESSION_FILE, 'rb') as f:
        session_data = pickle.load(f)

    # 1. Add cookies to the session
    cookies = session_data.get('cookies', [])
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'])
    
    # 2. Find and add the Authorization token from localStorage
    local_storage = session_data.get('local_storage', {})
    
    # !!! IMPORTANT !!!
    # You need to find the correct key for the authorization token.
    # Open your browser's developer tools (F12), go to the "Application"
    # tab, and look at "Local Storage" for your Jira domain.
    # Common key names are 'token', 'access_token', 'jwt', or something
    # specific like 'jira.token.auth'.
    # Update the key name in the line below.
    auth_token_key = 'token' # <--- CHANGE THIS KEY IF NEEDED
    
    auth_token = local_storage.get(auth_token_key)

    if auth_token:
        print(f"-> Found authorization token in localStorage under key '{auth_token_key}'.")
        # Add the token to the session headers as a Bearer token.
        # Some apps may use a different scheme like 'Token' or 'JWT'.
        session.headers.update({'Authorization': f'Bearer {auth_token}'})
    else:
        print(f"[WARNING] Could not find an auth token with key '{auth_token_key}' in localStorage.")
        print("API calls may still fail if a header is required.")


    # The API endpoint for a specific issue
    api_url = f'{JIRA_BASE_URL}/rest/api/2/issue/{issue_id}'

    #### TODO: GET REMOTELINK WHERE `"globalId": "VF - Log Hours"` 
    api_url_remote = f'{JIRA_BASE_URL}/rest/api/2/issue/{issue_id}/remotelink'
    

    print(f"-> Making authenticated request to: {api_url}")
    try:
        response = session.get(api_url)
        response.raise_for_status()

        print("-> Successfully fetched data!")
        return response.json()

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401 or e.response.status_code == 403:
            print("[ERROR] Authentication failed (401/403). Your session may have expired.")
            print("Please run 'get_jira_cookies.py' again to get a new session.")
        else:
            print(f"[ERROR] An HTTP error occurred: {e}")
        return None
    except Exception as e:
        print(f"[ERROR] An unexpected error occurred: {e}")
        return None

if __name__ == '__main__':
    issue_data = get_jira_issue_details('XXXX-1111')

    if issue_data:
        summary = issue_data.get('fields', {}).get('summary')
        status = issue_data.get('fields', {}).get('status', {}).get('name')
        reporter = issue_data.get('fields', {}).get('reporter', {}).get('displayName')

        print("\n--- JIRA ISSUE DETAILS ---")
        print(f"  ID:       {issue_data.get('key')}")
        print(f"  Summary:  {summary}")
        print(f"  Status:   {status}")
        print(f"  Reporter: {reporter}")
        print("--------------------------")

  
