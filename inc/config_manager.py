import json
import os
import time

# This dictionary will be populated by load_config and used by other modules
config = {}
# This dictionary will be populated by load_translations
STRINGS = {}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def load_config():
    """Loads config.json, creating a default one if it doesn't exist."""
    global config
    config_path = os.path.join(SCRIPT_DIR, "../config.json")

    default_config = {
        "API_TOKEN": "PASTE_YOUR_GITHUB_PAT_HERE",
        "GITHUB_ORG": "owner",
        "GITHUB_USERNAME": "your-github-username",
        "LANGUAGE": "fi",
        "NOTIFICATION_WINDOW_TITLE": "TODAYTASKS",
        "BROWSER_COMMAND": ["/usr/bin/google-chrome", "--profile-directory=Profile 1", "--new-window"],
        "JIRA_URL": "https://YOUR_ORG.atlassian.net",
        "JIRA_SESSION_FILE": "jira_session.pkl",
        "CHROME_DRIVER_PATH": "path/to/your/chromedriver",
        "TRELLO_URL": "https://trello.com",
        "TRELLO_SESSION_FILE": "trello_session.pkl",
        "CALENDAR_CSV": "https://docs.google.com/spreadsheets/d/e/YOUR_EXPORTED_CALENDAR_CSV_HERE/pub?gid=0&single=true&output=csv",
        "WEB_MONITORING": {
            "ENABLED": False,
            "CHECK_INTERVAL_MINUTES": 30,
            "PAGES": []
        },
        "TIME_TRACKING": {
            "ENABLED": True,
            "HOURLY_CHECKIN_ENABLED": True,
            "CHECKIN_INTERVAL_MINUTES": 60
        }
    }
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            loaded_config = json.load(f)
        # Ensure all default keys are present
        for key, value in default_config.items():
            loaded_config.setdefault(key, value)
        config = loaded_config
        return True
    except (FileNotFoundError, json.JSONDecodeError):
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4)
        config = default_config
        # Return False to indicate that a new config was created and needs editing
        return False

def load_translations():
    """Loads the language JSON file into the global STRINGS dictionary."""
    global STRINGS
    lang_code = config.get("LANGUAGE", "fi")
    lang_dir = os.path.join(SCRIPT_DIR, "../lang")
    if not os.path.exists(lang_dir): os.makedirs(lang_dir)
    path = os.path.join(lang_dir, f"{lang_code}.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            STRINGS = json.load(f)
    except FileNotFoundError:
        print(f"Warning: Language file '{path}' not found. Using empty strings.", file=sys.stderr)
        STRINGS = {}