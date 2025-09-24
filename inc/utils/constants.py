"""
Application constants and configuration values.

This module contains all constants used throughout the application,
including color pairs, view names, file paths, and other configuration.
"""

import os

# -- File Paths --
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__ + "/../.."))
DATA_FILE = os.path.join(SCRIPT_DIR, "jira_data.json")
JIRA_BOX_FILE = os.path.join(SCRIPT_DIR, "jira_box2.txt")
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache")
LOG_FILE = os.path.join(SCRIPT_DIR, "debug.log")

# -- Color Pairs --
(COLOR_PAIR_DEFAULT, COLOR_PAIR_REVERSE, COLOR_PAIR_GREY, COLOR_PAIR_PAUSED,
 COLOR_PAIR_SELECTED, COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN, COLOR_PAIR_URGENT_BOX,
 COLOR_PAIR_PR_UNHANDLED, COLOR_PAIR_PR_APPROVED, COLOR_PAIR_FOCUSED,
 COLOR_PAIR_PERMANENT_NOTIFICATION, COLOR_PAIR_STANDOUT, COLOR_PAIR_NEW_COMMENT, COLOR_PAIR_HELP_OVERLAY, COLOR_PAIR_CODE) = range(1, 17)

# -- View Names --
VIEW_MAIN = "main"
VIEW_DEDICATED_NOTES = "dedicated_notes"
VIEW_DAILY_NOTES = "daily_notes"
VIEW_TIME_LOG = "time_log"
VIEW_HOURLY_CHECKIN = "hourly_checkin"

# -- Weekday Mapping --
WEEKDAY_MAP = {
    'ma': 0, 'mo': 0, 'ti': 1, 'tu': 1, 'ke': 2, 'we': 2,
    'to': 3, 'th': 3, 'pe': 4, 'fr': 4, 'la': 5, 'sa': 5,
    'su': 6, 'su': 6
}

# -- Logging Configuration --
LOG_FORMAT = '%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# -- Polling Intervals --
CALENDAR_POLL_INTERVAL = 300  # 5 minutes in seconds
DEFAULT_WEB_MONITORING_INTERVAL = 30  # minutes

# -- Default Values --
DEFAULT_CONTENT_REFRESH_INTERVAL = 10.0  # seconds
DEFAULT_CLOCK_REFRESH_INTERVAL = 1.0     # seconds
