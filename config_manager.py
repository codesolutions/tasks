import json
import os

# This dictionary will be populated by load_config and used by other modules
config = {}
# This dictionary will be populated by load_translations
STRINGS = {}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def load_config():
    """Loads config.json, creating a default one if it doesn't exist."""
    global config
    config_path = os.path.join(SCRIPT_DIR, "config.json")
    default_config = {
        "API_TOKEN": "PASTE_YOUR_BEARER_TOKEN_HERE",
        "STASH_URL": "http://your-stash-instance.com:7990",
        "STASH_REVIEW_URL": "http://your-stash-instance.com:7990/rest/api/latest/dashboard/pull-requests?state=OPEN&role=REVIEWER",
        "USER_ID": 3006,
        "LANGUAGE": "fi",
        "NOTIFICATION_WINDOW_TITLE": "TODAYTASKS",
        "BROWSER_COMMAND": ["/usr/bin/google-chrome", "--profile-directory=Profile 1", "--new-window"],
        "JIRA_URL": "https://YOUR_ORG.atlassian.net",
        "JIRA_SESSION_FILE": "jira_session.pkl",
        "CHROME_DRIVER_PATH": "path/to/your/chromedriver"
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
    lang_dir = os.path.join(SCRIPT_DIR, "lang")
    if not os.path.exists(lang_dir): os.makedirs(lang_dir)
    path = os.path.join(lang_dir, f"{lang_code}.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            STRINGS = json.load(f)
    except FileNotFoundError:
        print(f"Warning: Language file '{path}' not found. Using empty strings.", file=sys.stderr)
        STRINGS = {}

def t(key, **kwargs):
    """Gets a translated string by its key."""
    template = STRINGS.get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, TypeError):
            return f"FORMAT_ERROR_FOR_KEY: {key}"
    return template
