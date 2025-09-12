import curses
import json
import time
from datetime import datetime, timedelta, date
import os
import sys
import copy
import locale
from urllib.parse import urlparse, urlunparse
import re
import threading
import requests
import subprocess
import webbrowser
import pickle
import logging
import threading
import csv
import io
import hashlib
from bs4 import BeautifulSoup

# internal imports
import inc.config_manager


from inc.jira import (
    load_jira_cache,
    jira_queue_worker,  # Import the new worker
    jira_request_queue, # Import the queue
    jira_in_flight,     # Import the in-flight tracker
    get_and_save_web_session,  # old
    # jira_data_poller, # old
    config as jira_config
)
import inc.helpers
from inc.helpers import t

# Attempt to import Selenium, but allow the app to run without it.
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)),
LOG_FILE = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "debug.log"
)

logging.basicConfig(filename=LOG_FILE,
                    filemode='a',
                    format='%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.DEBUG)

# --- Global Dictionaries ---

sent_notifications = set() # Global set to track sent notifications to avoid duplicates
pull_requests_for_review = []
reviews_lock = threading.Lock()
sent_review_notifications = set()
permanent_notifications = []
app_data = {}
external_meetings = []
external_meetings_lock = threading.Lock()
web_change_notifications = []

# -- Setup Locale --
try:
    locale.setlocale(locale.LC_ALL, '')
except locale.Error as e:
    print(f"Warning: Could not set locale ({e}). Non-ASCII characters may not work correctly.", file=sys.stderr)

# -- Constants and Globals --
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, "jira_data.json")
JIRA_BOX_FILE = os.path.join(SCRIPT_DIR, "jira_box2.txt")
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache")
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

# -- Color Pairs --
(COLOR_PAIR_DEFAULT, COLOR_PAIR_REVERSE, COLOR_PAIR_GREY, COLOR_PAIR_PAUSED,
 COLOR_PAIR_SELECTED, COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN, COLOR_PAIR_URGENT_BOX,
 COLOR_PAIR_PR_UNHANDLED, COLOR_PAIR_PR_APPROVED, COLOR_PAIR_FOCUSED,
 COLOR_PAIR_PERMANENT_NOTIFICATION, COLOR_PAIR_STANDOUT, COLOR_PAIR_NEW_COMMENT) = range(1, 15)


# -- Views --
VIEW_MAIN = "main"
VIEW_DEDICATED_NOTES = "dedicated_notes"
VIEW_DAILY_NOTES = "daily_notes"
VIEW_TIME_LOG = "time_log"
VIEW_HOURLY_CHECKIN = "hourly_checkin"

WEEKDAY_MAP = {
    'ma': 0, 'mo': 0, 'ti': 1, 'tu': 1, 'ke': 2, 'we': 2,
    'to': 3, 'th': 3, 'pe': 4, 'fr': 4, 'la': 5, 'sa': 5,
    'su': 6, 'su': 6
}

def poll_external_calendar():
    """Polls an external calendar URL for meetings."""
    global external_meetings
    calendar_url = inc.config_manager.config.get('CALENDAR_CSV')
    while True:
        try:
            response = requests.get(calendar_url, timeout=20)
            response.raise_for_status()
            csv_data = response.text
            csv_file = io.StringIO(csv_data)
            reader = csv.reader(csv_file)
            next(reader)  # Skip header row
            new_meetings = []
            for row in reader:
                if len(row) >= 6:
                    new_meetings.append({
                        'start_time': row[1],
                        'end_time': row[2],
                        'title': row[3],
                        'url': row[5]
                    })
            with external_meetings_lock:
                external_meetings.clear()
                external_meetings.extend(new_meetings)
        except requests.exceptions.RequestException as e:
            print(t('polling_err', url=calendar_url, e=e), file=sys.stderr)
            pass # Silently continue on network errors
        time.sleep(3600) # Poll every hour

def poll_web_pages():
    """Polls web pages for changes."""
    global web_change_notifications
    web_monitoring_config = inc.config_manager.config.get("WEB_MONITORING", {})
    if not web_monitoring_config.get("ENABLED"):
        if (web_change_notifications):
            web_change_notifications = [];
            save_data(app_data)
        return

    check_interval = web_monitoring_config.get("CHECK_INTERVAL_MINUTES", 30) * 60
    pages = web_monitoring_config.get("PAGES", [])

    while True:
        for page in pages:
            try:
                response = requests.get(page["url"], timeout=20)
                response.raise_for_status()
                content = response.text

                if page.get("selector"):
                    soup = BeautifulSoup(content, 'lxml')
                    element = soup.select_one(page["selector"])
                    content = str(element) if element else ""

                url_hash = hashlib.md5(page["url"].encode()).hexdigest()
                cache_file = os.path.join(CACHE_DIR, f"{url_hash}.html")

                last_content = ""
                if os.path.exists(cache_file):
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        last_content = f.read()

                if content != last_content:
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        f.write(content)

                    notification_message = t('web_change_notification', name=page['name'], url=page['url'])
                    if notification_message not in web_change_notifications:
                        web_change_notifications.append(notification_message)
                        send_desktop_notification(t('web_change_notification_title'), notification_message)
                        save_data(app_data)


            except requests.exceptions.RequestException as e:
                print(t('polling_err', url=page["url"], e=e), file=sys.stderr)
                pass # Silently continue on network errors
        time.sleep(check_interval)

def load_data():
    data = {}
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        print(t('error_json_read', file=DATA_FILE), file=sys.stderr)
        pass

    data.setdefault("current_ticket", None)
    data.setdefault("focused_ticket", None)
    data.setdefault("focused_subtask", None)
    data.setdefault("completed_tickets", [])
    data.setdefault("task_start_time", None)
    data.setdefault("sub_tasks", {})
    data.setdefault("tasks_done", {})
    data.setdefault("meetings", [])
    
    # Time tracking data structures
    from inc.time_tracker import ensure_time_tracking_defaults
    ensure_time_tracking_defaults(data)
    data.setdefault("interruptions", [])
    data.setdefault("notes", {})
    data.setdefault("paused_tasks", [])
    data.setdefault("recurring_events", [])
    data.setdefault("daily_notes", {})
    data.setdefault("show_hidden_tasks", False)
    data.setdefault("web_change_notifications", [])
    global web_change_notifications
    web_change_notifications = data["web_change_notifications"]


    # Data migration and cleanup logic
    for ticket_name, sub_tasks_for_ticket in data.get("sub_tasks", {}).items():
        if isinstance(sub_tasks_for_ticket, dict):
            for sub_task_name, sub_task_details in list(sub_tasks_for_ticket.items()):
                if not isinstance(sub_task_details, dict):
                    current_status = "done" if sub_task_details else "todo"
                    sub_tasks_for_ticket[sub_task_name] = {"status": current_status, "notes": [], "pr_url": None, "pr_status": None, "jira_refreshed": None}
                else:
                    # Migrate old status fields to new 'status' field
                    current_status = sub_task_details.get("status")
                    if not current_status or current_status not in ["todo", "in_progress", "done", "hidden", "focused"]:
                        if sub_task_details.get("hidden", False):
                            current_status = "hidden"
                        elif sub_task_details.get("done", False):
                            current_status = "done"
                        elif sub_task_details.get("focused", False):
                            current_status = "focused"
                        else:
                            current_status = "todo"

                    sub_task_details["status"] = current_status
                    sub_task_details.setdefault("notes", [])
                    sub_task_details.setdefault("pr_url", None)
                    sub_task_details.setdefault("pr_status", None)
                    sub_task_details.setdefault("jira_refreshed", None)

                    # Clean up old fields
                    sub_task_details.pop("done", None)
                    sub_task_details.pop("hidden", None)
                    sub_task_details.pop("focused", None)
                    # Old field cleanup for migration
                    if "pr_unhandled_comments" in sub_task_details:
                        if sub_task_details["pr_unhandled_comments"] and sub_task_details.get("pr_status") is None:
                             sub_task_details["pr_status"] = "attention_needed"
                        del sub_task_details["pr_unhandled_comments"]


                    if sub_task_details.get("pr_url") and "notes" in sub_task_details:
                        cleaned_notes = [note for note in sub_task_details["notes"] if not note.strip().startswith("PR:")]
                        sub_task_details["notes"] = cleaned_notes

        elif sub_tasks_for_ticket is not None :
             data["sub_tasks"][ticket_name] = {}
    return data

def save_data(data):
    data["web_change_notifications"] = web_change_notifications
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, default=str, ensure_ascii=False)
    except IOError as e:
        print(t('error_json_save', file=DATA_FILE, e=e), file=sys.stderr)
    except TypeError as e:
         print(t('error_json_convert', e=e), file=sys.stderr)

def format_timedelta_minutes(delta):
    if not isinstance(delta, timedelta):
        return ""
    total_seconds = int(delta.total_seconds())
    is_past = total_seconds < 0
    total_seconds = abs(total_seconds)

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    parts = []
    if hours > 0:
        parts.append(t('time_h', h=hours))
    if minutes > 0:
        parts.append(t('time_m', m=minutes))
    if hours == 0 and minutes < 5 and seconds > 0: # Show seconds only for short durations
        parts.append(t('time_s', s=seconds))

    if not parts:
        return t('time_moment_ago') if is_past else ""

    time_str = " ".join(parts)
    return t('time_ago', time_str=time_str) if is_past else t('time_in', time_str=time_str)

def _draw_wrapped_text(stdscr, text_to_draw, start_row, start_col,
                       max_width_for_text_line,
                       effective_content_width,
                       content_height_obj,
                       prefix="", subsequent_indent_offset=0, attr=0):
    lines_used_for_item = 0
    remaining_text = text_to_draw
    current_line_y = start_row

    max_h, max_w = stdscr.getmaxyx()

    if content_height_obj[0] > 0 and current_line_y < max_h -1 :
        line_content_with_prefix = prefix + remaining_text
        available_for_text_on_first_line = effective_content_width - start_col - len(prefix)
        if available_for_text_on_first_line < 0: available_for_text_on_first_line = 0
        text_segment_on_first_line = remaining_text[:available_for_text_on_first_line]
        full_first_line_to_draw = prefix + text_segment_on_first_line

        try:
            draw_len = min(len(full_first_line_to_draw), effective_content_width - start_col)
            if draw_len > 0 and start_col + draw_len <= max_w and start_col >=0:
                 stdscr.addstr(current_line_y, start_col, full_first_line_to_draw[:draw_len], attr)
            lines_used_for_item += 1
            content_height_obj[0] -= 1
            remaining_text = remaining_text[len(text_segment_on_first_line):]
            current_line_y +=1
        except curses.error: remaining_text = ""
    else: remaining_text = ""

    wrapped_line_draw_start_col = start_col + subsequent_indent_offset
    max_width_for_this_wrapped_line = effective_content_width - wrapped_line_draw_start_col

    while remaining_text and content_height_obj[0] > 0 and current_line_y < max_h -1:
        if max_width_for_this_wrapped_line <= 0: break
        segment = remaining_text[:max_width_for_this_wrapped_line]
        try:
            draw_len = min(len(segment), effective_content_width - wrapped_line_draw_start_col)
            if draw_len > 0 and wrapped_line_draw_start_col + draw_len <= max_w and wrapped_line_draw_start_col >=0:
                stdscr.addstr(current_line_y, wrapped_line_draw_start_col, segment[:draw_len], attr)
            lines_used_for_item += 1
            content_height_obj[0] -= 1
            remaining_text = remaining_text[len(segment):]
            current_line_y += 1
        except curses.error: break
    return lines_used_for_item


def read_jira_box_content(max_lines=10):
    try:
        with open(JIRA_BOX_FILE, 'r', encoding='utf-8') as f:
            lines = [line.rstrip('\n') for line in f.readlines()]
            return lines[:max_lines]
    except FileNotFoundError:
        return []
    except Exception:
        return []


def display_dedicated_notes_view(stdscr, data, command_buffer, entity_for_notes, show_help_footer, selected_note_idx, jira_cache=None, jira_cache_lock=None, scroll_offset=0):
    height, width = stdscr.getmaxyx()
    now_time_str = datetime.now().strftime("%H:%M:%S")
    stdscr.clear()

    row = 0
    stdscr.addstr(row, 0, t('ui_clock', now_time_str=now_time_str), curses.color_pair(COLOR_PAIR_DEFAULT))
    row += 1
    stdscr.addstr(row, 0, "-" * width)
    row += 1

    title = t('dedicated_notes_title')
    notes_list_to_display = []
    jira_comments = []
    trello_comments = []
    task_info_to_show = []
    
    # Get cache copy if available
    cache_copy = {}
    if jira_cache and jira_cache_lock:
        with jira_cache_lock:
            cache_copy = jira_cache.copy()

    if entity_for_notes:
        entity_type = entity_for_notes.get("type")
        entity_name = entity_for_notes.get("name")
        main_task_name_context = entity_for_notes.get("main_task_name", data.get("current_ticket"))

        if entity_type == "task" and entity_name:
            title = t('dedicated_notes_header_task', name=entity_name)
            notes_list_to_display = data.get("notes", {}).get(entity_name, [])
        elif entity_type == "subtask" and main_task_name_context and entity_name:
            title = t('dedicated_notes_header_subtask', main_task=main_task_name_context, name=entity_name)
            subtask_details = data.get("sub_tasks",{}).get(main_task_name_context,{}).get(entity_name)
            if subtask_details and isinstance(subtask_details, dict):
                notes_list_to_display = subtask_details.get("notes", [])
                
            # Get Jira and Trello data for subtask
            jira_ticket_id = inc.helpers.get_jira_ticket_from_url(entity_name)
            cached_item = cache_copy.get(jira_ticket_id, {})
            
            if cached_item:
                # Get task info
                status = cached_item.get('data', {}).get('fields', {}).get('status', {}).get('name', 'N/A')
                status_icon = status
                if status == "Done":
                    status_icon = "✅"
                elif status == "In Progress":
                    status_icon = "🚧"
                elif status == "In Review":
                    status_icon = "👀"
                elif status == "To Do":
                    status_icon = "📌"
                elif status == "Backlog":
                    status_icon = "🗂️"
                
                jira_link = f"{inc.config_manager.config.get('JIRA_URL')}/browse/{jira_ticket_id}"
                task_info_to_show.append(f"{status_icon} {jira_link}")
                
                summary = cached_item.get('data', {}).get('fields', {}).get('summary', '')
                if summary:
                    task_info_to_show.append(f"Summary: {summary}")
                
                # Check for Trello link in description
                jira_description = cached_item.get('data', {}).get('fields', {}).get('description', "")
                if jira_description and isinstance(jira_description, str):
                    pattern = r"(https://trello\.com/c/[^]]+)"
                    match = re.search(pattern, jira_description)
                    if match:
                        trello_link = match.group(0)
                        task_info_to_show.append(f"Trello: {trello_link}")
                
                # VF link
                vf_link = next((l.get("object",{}).get("url") for l in cached_item.get('remotelinks',[]) if l.get("globalId") == "VF - Log Hours"), None)
                if vf_link and vf_link != "N/A":
                    task_info_to_show.append(f"VF: {vf_link}")
                
                # PR info from subtask details
                if subtask_details and subtask_details.get("pr_url"):
                    task_info_to_show.append(f"PR: {subtask_details.get('pr_url')}")
                    
                    pr_details = subtask_details.get("pr_details", {})
                    if pr_details:
                        status_text = pr_details.get('status_text', 'waiting')
                        approvers_str = "PR " + status_text + ": " + ", ".join(pr_details.get('approvers_formatted', []))
                        task_info_to_show.append(approvers_str)
                
                # Get Jira comments
                jira_comments = list(reversed(cached_item.get('data', {}).get('fields', {}).get('comment', {}).get('comments', [])))
                
                # Get Trello comments
                trello_data = cached_item.get('trello_data', {})
                if trello_data and len(trello_data):
                    for action in trello_data['actions']:
                        if action['type'] == 'commentCard':
                            date_obj = datetime.fromisoformat(action['date'].replace('Z', '+00:00'))
                            formatted_date = date_obj.strftime('%d.%m %H:%M')
                            trello_comments.append({
                                'comment_text': action['data']['text'],
                                'creator_name': action['memberCreator']['fullName'],
                                'date': formatted_date
                            })
        else:
            title = t('dedicated_notes_no_selection')
    else:
        title = t('dedicated_notes_no_selection')

    stdscr.addstr(row, 0, title[:width])
    row +=1
    if len(title[:width-1]) > 0 : stdscr.addstr(row, 0, "-" * len(title[:width-1]))
    row +=1

    help_lines_notes_view = [
        t('help_header'),
        t('dedicated_notes_help_select'),
        t('dedicated_notes_help_delete'),
        t('dedicated_notes_help_add'),
        t('dedicated_notes_help_scroll_up'),
        t('dedicated_notes_help_scroll_down'),
        t('dedicated_notes_help_back')
    ]
    num_help_lines_notes_view = len(help_lines_notes_view)
    reserved_rows_notes_footer = num_help_lines_notes_view + 2

    content_height_val = height - (row + reserved_rows_notes_footer)
    if content_height_val < 0: content_height_val = 0
    content_height_obj = [content_height_val]
    
    # Create a virtual content area to render all content, then apply scrolling
    virtual_content = []
    virtual_row = 0
    
    # Helper function to add content to virtual screen with intelligent text wrapping
    def add_virtual_content(text, attr=curses.color_pair(COLOR_PAIR_DEFAULT), prefix="", indent_continuation=0):
        if not text:
            virtual_content.append({'text': "", 'attr': attr})
            return
            
        # Calculate available width for text
        available_width = width - 1  # Leave space for cursor
        
        def smart_wrap(text_to_wrap, line_width):
            """Intelligently wrap text, preferring to break at word boundaries"""
            if len(text_to_wrap) <= line_width:
                return [text_to_wrap]
            
            wrapped_lines = []
            remaining = text_to_wrap
            
            while remaining:
                if len(remaining) <= line_width:
                    wrapped_lines.append(remaining)
                    break
                    
                # Try to find a good break point (space, punctuation)
                break_point = line_width
                for i in range(line_width, max(0, line_width - 20), -1):
                    if i < len(remaining) and remaining[i] in ' \t\n.,;:!?':
                        break_point = i + 1 if remaining[i] in ' \t\n' else i + 1
                        break
                
                # If we couldn't find a good break point, just break at the width
                if break_point == line_width and line_width < len(remaining):
                    # Look ahead to see if we're in the middle of a word
                    if line_width < len(remaining) and remaining[line_width] not in ' \t\n':
                        # Try to find the end of the current word
                        word_end = line_width
                        while word_end > line_width - 10 and word_end > 0 and remaining[word_end - 1] not in ' \t\n':
                            word_end -= 1
                        if word_end > line_width - 10:  # Found a reasonable word boundary
                            break_point = word_end
                
                wrapped_lines.append(remaining[:break_point].rstrip())
                remaining = remaining[break_point:].lstrip()
            
            return wrapped_lines
        
        # Handle prefixed lines (like "| comment text")
        if prefix:
            # First line with prefix
            first_line_available = available_width - len(prefix)
            if first_line_available <= 0:
                virtual_content.append({'text': prefix, 'attr': attr})
                return
                
            wrapped_lines = smart_wrap(text, first_line_available)
            
            # First line with prefix
            if wrapped_lines:
                virtual_content.append({'text': prefix + wrapped_lines[0], 'attr': attr})
                
                # Continuation lines with indentation
                if len(wrapped_lines) > 1:
                    continuation_prefix = " " * (len(prefix) + indent_continuation)
                    continuation_width = available_width - len(continuation_prefix)
                    
                    for line in wrapped_lines[1:]:
                        # Further wrap continuation lines if needed
                        cont_wrapped = smart_wrap(line, continuation_width)
                        for cont_line in cont_wrapped:
                            virtual_content.append({'text': continuation_prefix + cont_line, 'attr': attr})
        else:
            # Simple text without prefix - wrap at available width
            wrapped_lines = smart_wrap(text, available_width)
            for line in wrapped_lines:
                virtual_content.append({'text': line, 'attr': attr})
    
    # Display task info section if available
    if task_info_to_show:
        add_virtual_content("┌─────INFO─── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_PAUSED))
        for info_item in task_info_to_show:
            add_virtual_content(info_item, curses.color_pair(COLOR_PAIR_PAUSED), prefix="| ")
        add_virtual_content("└──────────── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_PAUSED))
        add_virtual_content("")  # Empty line for spacing

    # Display Trello comments in full detail
    if trello_comments:
        add_virtual_content("┌─────TRELLO COMMENTS─── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_GREY))
        for comment in trello_comments:
            comment_text = comment.get('comment_text', '')
            creator_name = comment.get('creator_name', '')
            comment_date = comment.get('date', '')
            
            # Show header with author and date
            header = f"{creator_name} - {comment_date}"
            add_virtual_content(header, curses.color_pair(COLOR_PAIR_GREY) | curses.A_BOLD, prefix="| ")
            
            # Show full comment text (preserve newlines and wrap long lines)
            if comment_text:
                for line in comment_text.split('\n'):
                    add_virtual_content(line, curses.color_pair(COLOR_PAIR_GREY), prefix="| ")
            
            # Add separator between comments
            add_virtual_content("---", curses.color_pair(COLOR_PAIR_GREY), prefix="| ")
        add_virtual_content("└──────────── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_GREY))
        add_virtual_content("")  # Empty line for spacing

    # Display Jira comments in full detail
    if jira_comments:
        add_virtual_content("┌─────JIRA COMMENTS─── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_STANDOUT))
        for comment in jira_comments:
            comment_body = comment.get('body', '')
            comment_date = comment.get('updated', '')
            author = comment.get('author', {}).get('displayName', 'Unknown')
            
            # Fix date format
            if comment_date:
                try:
                    if len(comment_date) > 2:
                        comment_date = comment_date[:-2] + ':' + comment_date[-2:]
                    dt = datetime.fromisoformat(comment_date)
                    formatted_date = dt.strftime('%d.%m %H:%M')
                except:
                    formatted_date = comment_date
            else:
                formatted_date = 'Unknown date'
            
            # Show header with author and date
            header = f"{author} - {formatted_date}"
            add_virtual_content(header, curses.color_pair(COLOR_PAIR_STANDOUT) | curses.A_BOLD, prefix="| ")
            
            # Show full comment text (clean up user mentions, preserve newlines and wrap long lines)
            if comment_body:
                # Clean up Jira user mentions
                clean_body = re.sub(r'\[~.*?\]', 'USER', comment_body)
                for line in clean_body.split('\n'):
                    add_virtual_content(line, curses.color_pair(COLOR_PAIR_STANDOUT), prefix="| ")
            
            # Add separator between comments
            add_virtual_content("---", curses.color_pair(COLOR_PAIR_STANDOUT), prefix="| ")
        add_virtual_content("└──────────── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_STANDOUT))
        add_virtual_content("")  # Empty line for spacing

    # Display regular notes (with selection/deletion functionality preserved)
    if notes_list_to_display:
        add_virtual_content("┌─────NOTES─── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_DEFAULT))
        for note_idx, note_text in enumerate(notes_list_to_display):
            item_attr = curses.color_pair(COLOR_PAIR_DEFAULT)
            prefix = f"  {note_idx+1}. "
            if note_idx == selected_note_idx:
                item_attr = curses.color_pair(COLOR_PAIR_SELECTED)
                prefix = f"> {note_idx+1}. "
            add_virtual_content(note_text, item_attr, prefix=prefix)
        add_virtual_content("└──────────── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_DEFAULT))
    
    # Show message if no content available
    if not notes_list_to_display and not jira_comments and not trello_comments and not task_info_to_show and entity_for_notes:
        add_virtual_content("No notes, comments, or details available for this item.")
    
    # Now render the visible portion of virtual content based on scroll offset
    visible_start = max(0, scroll_offset)
    visible_end = visible_start + content_height_val
    visible_content = virtual_content[visible_start:visible_end]
    
    for i, content_item in enumerate(visible_content):
        if row + i >= height - reserved_rows_notes_footer: break
        try:
            # Text wrapping is now handled in add_virtual_content, so just display as-is
            stdscr.addstr(row + i, 0, content_item['text'], content_item['attr'])
        except curses.error:
            pass
    
    # Show scroll indicators if there's more content
    total_content_lines = len(virtual_content)
    if total_content_lines > content_height_val:
        scroll_info = f"[{visible_start+1}-{min(visible_end, total_content_lines)}/{total_content_lines}]"
        try:
            stdscr.addstr(height - reserved_rows_notes_footer - 1, width - len(scroll_info) - 1, scroll_info, curses.color_pair(COLOR_PAIR_PAUSED))
        except curses.error:
            pass

    help_draw_start_y_notes = height - 1 - 1 - num_help_lines_notes_view
    if help_draw_start_y_notes >= row:
        for i, line in enumerate(help_lines_notes_view):
            try:
                if help_draw_start_y_notes + i < height -2:
                    stdscr.addstr(help_draw_start_y_notes + i, 0, line[:width])
            except curses.error: pass

    max_cmd_len_notes = width - 1
    max_buffer_len_notes = max_cmd_len_notes - len("> ")
    if max_buffer_len_notes < 0: max_buffer_len_notes = 0
    display_buffer_notes = command_buffer[:max_buffer_len_notes]
    command_line_text_notes = "> " + display_buffer_notes
    cursor_x_notes = len(command_line_text_notes)

    try:
        stdscr.addstr(height - 1, 0, " " * (width-1 if width > 0 else 0) )
        stdscr.addstr(height - 1, 0, command_line_text_notes.ljust(width-1 if width > 0 else 0), curses.color_pair(COLOR_PAIR_DEFAULT) | curses.A_BOLD)
        curses.curs_set(1)
        stdscr.move(height - 1, min(cursor_x_notes, width - 1 if width > 0 else 0))
    except curses.error: pass
    stdscr.refresh()
    return True

def display_daily_notes_view(stdscr, data, command_buffer, current_date_for_notes, show_help_footer, selected_note_idx):
    height, width = stdscr.getmaxyx()
    now_time_str = datetime.now().strftime("%H:%M:%S")
    stdscr.clear()

    row = 0
    stdscr.addstr(row, 0, t('ui_clock', now_time_str=now_time_str), curses.color_pair(COLOR_PAIR_DEFAULT))
    row += 1
    stdscr.addstr(row, 0, "-" * width)
    row += 1

    date_str_iso = current_date_for_notes.isoformat()
    weekday_str = t('weekdays')[current_date_for_notes.weekday()]
    title = t('daily_notes_header', date=date_str_iso, weekday=weekday_str)

    notes_list_to_display = data.get("daily_notes", {}).get(date_str_iso, [])

    stdscr.addstr(row, 0, title[:width])
    row +=1
    if len(title[:width-1]) > 0: stdscr.addstr(row, 0, "-" * len(title[:width-1]))
    row +=1

    help_lines_daily_notes = [
        t('help_header'),
        t('dedicated_notes_help_select'),
        t('dedicated_notes_help_delete'),
        t('dedicated_notes_help_add'),
        t('daily_notes_help_prev'),
        t('daily_notes_help_next'),
        t('dedicated_notes_help_back')
    ]
    num_help_lines_daily_notes = len(help_lines_daily_notes)
    reserved_rows_daily_footer = num_help_lines_daily_notes + 2

    content_height_val = height - (row + reserved_rows_daily_footer)
    if content_height_val < 0: content_height_val = 0
    content_height_obj = [content_height_val]

    for note_idx, note_text in enumerate(notes_list_to_display):
        if content_height_obj[0] <= 0:
            if row > 0 and note_idx < len(notes_list_to_display) and width > 7:
                try: stdscr.addstr(row, 2, "..."[:width-2])
                except curses.error: pass
            break

        item_attr = curses.color_pair(COLOR_PAIR_DEFAULT)
        prefix = f"  {note_idx+1}. "
        if note_idx == selected_note_idx:
            item_attr = curses.color_pair(COLOR_PAIR_SELECTED)
            prefix = f"> {note_idx+1}. "

        start_col = 0
        max_text_width_for_line = width - start_col - len(prefix) -1
        if max_text_width_for_line < 0: max_text_width_for_line = 0
        lines_used = _draw_wrapped_text(stdscr, note_text, row, start_col,
                                        max_text_width_for_line, width, content_height_obj,
                                        prefix=prefix, subsequent_indent_offset=len(prefix), attr=item_attr)
        row += lines_used
        if lines_used == 0 and content_height_obj[0] <=0 : break

    if not notes_list_to_display:
        if content_height_obj[0] > 0:
            stdscr.addstr(row, 0, t('daily_notes_no_notes'))
            row+=1; content_height_obj[0]-=1

    help_draw_start_y_daily = height - 1 - 1 - num_help_lines_daily_notes
    if help_draw_start_y_daily >= row:
        for i, line in enumerate(help_lines_daily_notes):
            try:
                if help_draw_start_y_daily + i < height -2:
                    stdscr.addstr(help_draw_start_y_daily + i, 0, line[:width])
            except curses.error: pass

    max_cmd_len_daily = width - 1
    max_buffer_len_daily = max_cmd_len_daily - len("> ")
    if max_buffer_len_daily < 0: max_buffer_len_daily = 0
    display_buffer_daily = command_buffer[:max_buffer_len_daily]
    command_line_text_daily = "> " + display_buffer_daily
    cursor_x_daily = len(command_line_text_daily)

    try:
        stdscr.addstr(height - 1, 0, " " * (width-1 if width > 0 else 0) )
        stdscr.addstr(height - 1, 0, command_line_text_daily.ljust(width-1 if width > 0 else 0), curses.color_pair(COLOR_PAIR_DEFAULT) | curses.A_BOLD)
        curses.curs_set(1)
        stdscr.move(height - 1, min(cursor_x_daily, width - 1 if width > 0 else 0))
    except curses.error: pass
    stdscr.refresh()

    #main_win.refresh()
    return True


def display_ui(stdscr, data, command_buffer="", full_redraw=False, selected_subtask_idx=-1,
               current_view_mode=VIEW_MAIN, entity_for_dedicated_notes=None,
               current_ticket_subtask_list_for_display_arg=None, show_help_footer=True,
               current_date_for_daily_notes_arg=None, selected_note_idx=-1,
               jira_cache=None, jira_cache_lock=None, notes_scroll_offset=0,
               selected_checkin_task_idx=-1):

    global pull_requests_for_review, permanent_notifications, web_change_notifications, reviews_lock, external_meetings_lock

    # Dispatch to appropriate view handlers
    if current_view_mode == VIEW_DEDICATED_NOTES:
        return display_dedicated_notes_view(stdscr, data, command_buffer, entity_for_dedicated_notes, show_help_footer, selected_note_idx, jira_cache, jira_cache_lock, notes_scroll_offset)
    elif current_view_mode == VIEW_DAILY_NOTES:
        return display_daily_notes_view(stdscr, data, command_buffer, current_date_for_daily_notes_arg, show_help_footer, selected_note_idx)
    elif current_view_mode == VIEW_TIME_LOG:
        from inc.views.time_log_view import display_time_log_view
        return display_time_log_view(stdscr, data, current_date_for_daily_notes_arg)
    elif current_view_mode == VIEW_HOURLY_CHECKIN:
        from inc.views.hourly_checkin_view import display_hourly_checkin_view
        return display_hourly_checkin_view(stdscr, data, selected_checkin_task_idx)
    
    # Default to main view rendering
    from inc.views.main_view import display_main_view
    return display_main_view(stdscr, data, command_buffer, full_redraw, selected_subtask_idx, 
                            current_view_mode, entity_for_dedicated_notes, 
                            current_ticket_subtask_list_for_display_arg, show_help_footer, 
                            current_date_for_daily_notes_arg, selected_note_idx, 
                            jira_cache, jira_cache_lock, reviews_lock, external_meetings_lock, notes_scroll_offset,
                            pull_requests_for_review, permanent_notifications, web_change_notifications, external_meetings)
 

def show_notification(stdscr, message):
    try:
        height, width = stdscr.getmaxyx()
        if height < 2 or width == 0: return
        notification_line = height - 2
        message_to_show = message[:width - 2 if width > 2 else width]

        stdscr.attron(curses.color_pair(COLOR_PAIR_REVERSE))
        stdscr.addstr(notification_line, 0, " " * (width-1 if width > 0 else 0))
        stdscr.addstr(notification_line, 0, message_to_show.ljust(width-1 if width > 0 else 0))
        stdscr.attroff(curses.color_pair(COLOR_PAIR_REVERSE))
        stdscr.refresh()
        curses.napms(500)
        stdscr.addstr(notification_line, 0, " " * (width-1 if width > 0 else 0))
        show_permanent_notification(stdscr)
        stdscr.refresh()
    except curses.error: pass
    except Exception: pass

def show_permanent_notification(stdscr):
    global permanent_notifications

    try:
        height, width = stdscr.getmaxyx()
        if height < 2 or width == 0: return
        notification_line = height - 2

        row = 1
        if permanent_notifications:
            for msg in permanent_notifications:

                stdscr.addstr(notification_line, 0, " " * (width-1 if width > 0 else 0))
                stdscr.addstr(notification_line, 0, f"{row}. {msg[:width-3]}", curses.color_pair(COLOR_PAIR_PERMANENT_NOTIFICATION) | curses.A_BOLD)
                row += 1

        stdscr.refresh()
    except curses.error: pass
    except Exception: pass

def handle_input(data, command_parts, stdscr, current_view_mode, selected_subtask_idx, selected_note_idx, current_ticket_subtask_list, all_displayable_tickets_for_cmd):
    global web_change_notifications
    if current_view_mode != VIEW_MAIN:
        command = command_parts[0].lower() if command_parts else ""
        if command == 'q': return None
        if command == 'h': return "TOGGLE_HELP"

        if command == 'd' and selected_note_idx != -1:
            return "DELETE_NOTE"

        show_notification(stdscr, t('cmd_exclusively_in_main_view'))
        return "NO_CHANGE"

    if not command_parts: return "NO_CHANGE"
    current_ticket_name_val = data.get("current_ticket")
    data_was_modified = False
    command = command_parts[0].lower()

    if command == "ok":
        if len(command_parts) > 1:
            try:
                index_to_remove = int(command_parts[1]) - 1
                if 0 <= index_to_remove < len(web_change_notifications):
                    web_change_notifications.pop(index_to_remove)
                    data_was_modified = True
                    show_notification(stdscr, t('cmd_info_notification_dismissed'))
                else:
                    show_notification(stdscr, t('cmd_err_invalid_index'))
            except ValueError:
                show_notification(stdscr, t('cmd_err_invalid_index'))
        else:
            show_notification(stdscr, t('cmd_usage_ok'))
        return data if data_was_modified else "NO_CHANGE"

    completed_tickets = data.get("completed_tickets", [])
    all_tickets_set = set()
    all_tickets_set.update(data.get("sub_tasks", {}).keys())
    all_tickets_set.update(data.get("notes", {}).keys())
    for paused_item in data.get("paused_tasks", []):
        if paused_item.get("ticket"): all_tickets_set.add(paused_item["ticket"])
    all_known_tickets = sorted(list(filter(None, all_tickets_set)))


    def pause_current_task(data_dict):
        paused_modified = False
        current_to_pause = data_dict.get("current_ticket")
        if current_to_pause:
            sub_tasks_for_pause = data_dict.get("sub_tasks", {}).get(current_to_pause, {})
            notes_for_pause = data_dict.get("notes", {}).get(current_to_pause, [])
            start_time_for_pause = data_dict.get("task_start_time")
            paused_item = {
                'ticket': current_to_pause,
                'sub_tasks': copy.deepcopy(sub_tasks_for_pause),
                'notes': copy.deepcopy(notes_for_pause),
                'task_start_time': start_time_for_pause
            }
            data_dict.setdefault('paused_tasks', []).insert(0, paused_item)
            data_dict["current_ticket"] = None
            if "task_start_time" in data_dict:
                del data_dict["task_start_time"]
            paused_modified = True
        return paused_modified

    if command == 'login':
        return "RESTART_FOR_LOGIN"

    if command == 'n':
        if len(command_parts) > 1:
            new_task_name_cmd = " ".join(command_parts[1:])

            if new_task_name_cmd.startswith("http:") or new_task_name_cmd.startswith("https:"):
                show_notification(stdscr, t('cmd_err_project_is_url'))
                return "NO_CHANGE"

            if data.get("current_ticket") and data.get("current_ticket").lower() == new_task_name_cmd.lower():
                show_notification(stdscr, t('cmd_err_task_already_active', name=new_task_name_cmd))
                return "NO_CHANGE"

            # Check if it's a completed task
            if new_task_name_cmd in data.get("completed_tickets", []):
                data["completed_tickets"].remove(new_task_name_cmd)
                pause_current_task(data)
                data["current_ticket"] = new_task_name_cmd
                data["task_start_time"] = time.time()
                data_was_modified = True
                show_notification(stdscr, t('cmd_info_task_restored', name=new_task_name_cmd))
                return data

            is_existing_ticket = False
            for t_name in all_known_tickets:
                if t_name.lower() == new_task_name_cmd.lower():
                    is_existing_ticket = True; break
            if is_existing_ticket:
                is_paused = any(pt.get('ticket','').lower() == new_task_name_cmd.lower() for pt in data.get('paused_tasks',[]))
                if is_paused: show_notification(stdscr, t('cmd_err_task_exists_paused', name=new_task_name_cmd))
                else: show_notification(stdscr, t('cmd_err_task_exists', name=new_task_name_cmd))
                return "NO_CHANGE"

            pause_modified_by_n = pause_current_task(data)
            data["current_ticket"] = new_task_name_cmd
            data["task_start_time"] = time.time()
            data.setdefault("sub_tasks", {}).setdefault(new_task_name_cmd, {})
            data.setdefault("notes", {}).setdefault(new_task_name_cmd, [])
            data_was_modified = True
            if pause_modified_by_n: show_notification(stdscr, t('cmd_info_task_resumed', name=new_task_name_cmd))
            else: show_notification(stdscr, t('cmd_info_task_started', name=new_task_name_cmd))
        else: show_notification(stdscr, t('cmd_usage_new_task'))

    elif command == 'h':
        return "TOGGLE_HELP"

    elif command == 't':
        data["show_hidden_tasks"] = not data.get("show_hidden_tasks", False)
        return data

    elif command == 'd':
        if selected_subtask_idx != -1 and 0 <= selected_subtask_idx < len(current_ticket_subtask_list):
            sub_task_to_hide_name, sub_task_details = current_ticket_subtask_list[selected_subtask_idx]
            if current_ticket_name_val in data.get("sub_tasks", {}) and \
               sub_task_to_hide_name in data["sub_tasks"][current_ticket_name_val]:
                data["sub_tasks"][current_ticket_name_val][sub_task_to_hide_name]["status"] = "hidden"
                if data["focused_subtask"] == sub_task_to_hide_name:
                    data["focused_subtask"] = None # Clear global focus if this was the one
                data_was_modified = True
                show_notification(stdscr, t('cmd_info_subtask_hidden', name=sub_task_to_hide_name))
            else:
                show_notification(stdscr, t('cmd_err_subtask_not_found'))
        else:
            show_notification(stdscr, t('cmd_prompt_select_subtask_to_hide'))
        return data if data_was_modified else "NO_CHANGE"


    elif command == 'a':
        if len(command_parts) > 1:
            sub_task_input = " ".join(command_parts[1:])
            
            # Check if input is a Jira ticket ID (e.g., DCURJ-1234)
            jira_ticket_pattern = re.match(r'^([A-Z]+[A-Z0-9]*-\d+)$', sub_task_input.strip())
            
            if jira_ticket_pattern:
                # Convert Jira ID to full URL
                jira_base_url = inc.config_manager.config.get('JIRA_URL', 'https://pinja.atlassian.net')
                sub_task_name_cmd = f"{jira_base_url}/browse/{sub_task_input.strip()}"
                jira_ticket_id = sub_task_input.strip()
                
                # Extract project prefix from ticket ID (e.g., DCURJ from DCURJ-1234)
                ticket_prefix = jira_ticket_id.split('-')[0]
                
                # Find the best matching project based on ticket patterns
                best_match_project = None
                best_match_score = 0
                
                for project_name, subtasks in data.get("sub_tasks", {}).items():
                    if project_name in data.get("completed_tickets", []):
                        continue  # Skip completed projects
                        
                    # Count how many subtasks in this project match the ticket prefix
                    matching_count = 0
                    total_jira_tickets = 0
                    
                    for subtask_url in subtasks.keys():
                        # Extract ticket ID from URL
                        url_ticket_match = re.search(r'/browse/([A-Z]+[A-Z0-9]*-\d+)$', subtask_url)
                        if url_ticket_match:
                            total_jira_tickets += 1
                            existing_ticket_id = url_ticket_match.group(1)
                            existing_prefix = existing_ticket_id.split('-')[0]
                            if existing_prefix == ticket_prefix:
                                matching_count += 1
                    
                    # Calculate match score (prefer higher match ratio)
                    if total_jira_tickets > 0:
                        match_ratio = matching_count / total_jira_tickets
                        # Also consider absolute count for tie-breaking
                        score = match_ratio * 1000 + matching_count
                        
                        # Debug logging
                        logging.debug(f"Project {project_name}: {matching_count}/{total_jira_tickets} match {ticket_prefix} (score: {score:.1f})")
                        
                        if score > best_match_score:
                            best_match_score = score
                            best_match_project = project_name
                
                # If no good match found, use current project or show error
                logging.debug(f"Best match for {ticket_prefix}: {best_match_project} (score: {best_match_score:.1f})")
                
                if best_match_project:
                    target_project = best_match_project
                    switch_message = ""
                    if current_ticket_name_val != best_match_project:
                        # Need to switch projects
                        pause_current_task(data)
                        data["current_ticket"] = best_match_project
                        data["task_start_time"] = time.time()
                        switch_message = f" (switched to {best_match_project})"
                elif current_ticket_name_val:
                    target_project = current_ticket_name_val
                    switch_message = ""
                else:
                    show_notification(stdscr, t('cmd_err_no_matching_project', prefix=ticket_prefix))
                    return "NO_CHANGE"
                    
                # Add subtask to target project
                target_subtasks = data.setdefault("sub_tasks", {}).setdefault(target_project, {})
                if sub_task_name_cmd not in target_subtasks:
                    target_subtasks[sub_task_name_cmd] = {
                        "status": "todo", 
                        "notes": [], 
                        "pr_url": None, 
                        "pr_status": None, 
                        "jira_refreshed": None
                    }
                    data_was_modified = True
                    
                    # Set focus on the newly added subtask
                    data["focused_ticket"] = target_project
                    data["focused_subtask"] = sub_task_name_cmd
                    
                    # Find the index of the new subtask for selection
                    show_hidden = data.get("show_hidden_tasks", False)
                    subtask_list = [(name, details) for name, details in target_subtasks.items() 
                                  if isinstance(details, dict) and (show_hidden or details.get("status") != "hidden")]
                    
                    # Find index of our new subtask
                    for idx, (name, _) in enumerate(subtask_list):
                        if name == sub_task_name_cmd:
                            # This would need to be returned to update selection in main loop
                            break
                    
                    if switch_message:
                        show_notification(stdscr, t('cmd_info_subtask_added_with_switch', 
                                                   ticket=jira_ticket_id, 
                                                   project=target_project, 
                                                   old_project=current_ticket_name_val or "None"))
                    else:
                        show_notification(stdscr, t('cmd_info_subtask_added', 
                                                   ticket=jira_ticket_id, 
                                                   project=target_project))
                else:
                    show_notification(stdscr, t('cmd_err_ticket_already_exists', 
                                               ticket=jira_ticket_id, 
                                               project=target_project))
            else:
                # Handle regular URL or text input (original behavior)
                sub_task_name_cmd = sub_task_input
                
                if not current_ticket_name_val:
                    show_notification(stdscr, t('cmd_err_no_active_task_for_subtask'))
                    return "NO_CHANGE"
                    
                current_ticket_subtasks = data.setdefault("sub_tasks", {}).setdefault(current_ticket_name_val, {})
                if sub_task_name_cmd not in current_ticket_subtasks:
                    current_ticket_subtasks[sub_task_name_cmd] = {
                        "status": "todo", 
                        "notes": [], 
                        "pr_url": None, 
                        "pr_status": None, 
                        "jira_refreshed": None
                    }
                    data_was_modified = True
                    show_notification(stdscr, t('cmd_info_subtask_added', 
                                               ticket=sub_task_name_cmd, 
                                               project=current_ticket_name_val))
                else:
                    show_notification(stdscr, t('cmd_err_subtask_exists', name=sub_task_name_cmd))
        else:
            show_notification(stdscr, t('cmd_usage_add_subtask'))

    elif command == 'pr':
        if current_ticket_name_val and selected_subtask_idx != -1 and \
           0 <= selected_subtask_idx < len(current_ticket_subtask_list):
            if len(command_parts) > 1:
                pr_url = " ".join(command_parts[1:])
                sub_task_to_modify_name, _ = current_ticket_subtask_list[selected_subtask_idx]
                if current_ticket_name_val in data.get("sub_tasks", {}) and \
                   sub_task_to_modify_name in data["sub_tasks"][current_ticket_name_val]:
                    data["sub_tasks"][current_ticket_name_val][sub_task_to_modify_name]["pr_url"] = pr_url
                    data["sub_tasks"][current_ticket_name_val][sub_task_to_modify_name]["pr_status"] = None # Reset status
                    data_was_modified = True
                    show_notification(stdscr, t('cmd_info_pr_added', name=sub_task_to_modify_name))
                else:
                    show_notification(stdscr, t('cmd_err_subtask_not_found'))
            else:
                show_notification(stdscr, t('cmd_usage_add_pr'))
        else:
            show_notification(stdscr, t('cmd_prompt_select_subtask_for_pr'))
        return data if data_was_modified else "NO_CHANGE"

    elif command == 'x':
        if current_ticket_name_val:
            if current_ticket_name_val not in data.get("completed_tickets", []):
                data.setdefault("completed_tickets", []).append(current_ticket_name_val)
            if data.get("focused_ticket") == current_ticket_name_val:
                data["focused_ticket"] = None
                data["focused_subtask"] = None
            data["current_ticket"] = None
            if "task_start_time" in data:
                del data["task_start_time"]
            data_was_modified = True
            show_notification(stdscr, t('cmd_info_task_completed_and_hidden', name=current_ticket_name_val))
        else:
            show_notification(stdscr, t('cmd_err_no_active_task_to_complete'))

    elif command == 'f':
        if current_ticket_name_val and selected_subtask_idx != -1 and \
           0 <= selected_subtask_idx < len(current_ticket_subtask_list):
            sub_task_name, sub_task_details = current_ticket_subtask_list[selected_subtask_idx]
            current_status = sub_task_details.get("status", "todo")

            # Log time for previously focused subtask if work session is active
            work_session = data.get("work_session", {})
            if work_session.get("active") and not work_session.get("paused"):
                prev_focused = data.get("focused_subtask")
                prev_focused_ticket = data.get("focused_ticket")
                if prev_focused and prev_focused_ticket and work_session.get("current_timer_start_ts"):
                    elapsed_seconds = int(datetime.now().timestamp() - work_session["current_timer_start_ts"])
                    if elapsed_seconds > 0:
                        from inc.time_tracker import add_time_entry
                        # Use the standardized format for time logging
                        normalized_subtask = f"[{prev_focused_ticket}] {prev_focused}"
                        add_time_entry(data, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
            
            # Unfocus all other subtasks in the current ticket
            for st_name, st_details in data["sub_tasks"][current_ticket_name_val].items():
                if st_details.get("status") == "focused":
                    st_details["status"] = "todo" # Or previous status if we want to be more complex

            if current_status == "focused":
                data["sub_tasks"][current_ticket_name_val][sub_task_name]["status"] = "todo"
                data["focused_ticket"] = None
                data["focused_subtask"] = None
                # Clear timer when unfocusing
                if work_session.get("active"):
                    work_session.pop("current_timer_start_ts", None)
                show_notification(stdscr, t('cmd_info_focus_cleared'))
            else:
                data["sub_tasks"][current_ticket_name_val][sub_task_name]["status"] = "focused"
                data["focused_ticket"] = current_ticket_name_val
                data["focused_subtask"] = sub_task_name
                # Start timer when focusing if work session is active
                if work_session.get("active") and not work_session.get("paused"):
                    work_session["current_timer_start_ts"] = datetime.now().timestamp()
                    work_session["last_activity_ts"] = datetime.now().timestamp()
                show_notification(stdscr, t('cmd_info_subtask_focus_set', name=sub_task_name))

            data_was_modified = True
        else:
            show_notification(stdscr, t('cmd_prompt_select_subtask_for_focus'))

    elif command == 'focus':
        if len(command_parts) > 1:
            identifier = " ".join(command_parts[1:])
            target_ticket = None
            target_subtask = None

            # First, search for a subtask
            found_subtasks = []
            for ticket_name_iter, subtasks in data.get("sub_tasks", {}).items():
                if ticket_name_iter in completed_tickets: continue
                for st_name, st_details in subtasks.items():
                    if identifier.lower() in st_name.lower():
                        found_subtasks.append((ticket_name_iter, st_name))

            if len(found_subtasks) == 1:
                target_ticket, target_subtask = found_subtasks[0]
            elif len(found_subtasks) > 1:
                show_notification(stdscr, t('cmd_err_multiple_subtasks_found', options=", ".join([st for _, st in found_subtasks])))
                return "NO_CHANGE"

            # If no subtask found, search for a main ticket
            if not target_ticket:
                try:
                    idx = int(identifier) - 1
                    if 0 <= idx < len(all_displayable_tickets_for_cmd):
                        target_ticket = all_displayable_tickets_for_cmd[idx]
                except ValueError:
                    matches = [t_name for t_name in all_displayable_tickets_for_cmd if identifier.lower() in t_name.lower()]
                    if len(matches) == 1:
                        target_ticket = matches[0]
                    elif len(matches) > 1:
                        show_notification(stdscr, t('cmd_err_multiple_tickets_found', options=", ".join(matches)))
                        return "NO_CHANGE"

            if target_ticket:
                # Log time for previously focused subtask if work session is active
                work_session = data.get("work_session", {})
                if work_session.get("active") and not work_session.get("paused"):
                    prev_focused = data.get("focused_subtask")
                    prev_focused_ticket = data.get("focused_ticket")
                    if prev_focused and prev_focused_ticket and work_session.get("current_timer_start_ts"):
                        elapsed_seconds = int(datetime.now().timestamp() - work_session["current_timer_start_ts"])
                        if elapsed_seconds > 0:
                            from inc.time_tracker import add_time_entry
                            # Use the standardized format for time logging
                            normalized_subtask = f"[{prev_focused_ticket}] {prev_focused}"
                            add_time_entry(data, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
                
                # Clear all previous focuses
                data["focused_ticket"] = None
                data["focused_subtask"] = None
                for ticket_subtasks in data["sub_tasks"].values():
                    for st in ticket_subtasks.values():
                        if st.get("status") == "focused":
                            st["status"] = "todo"

                # Set new focus
                data["focused_ticket"] = target_ticket
                if target_subtask:
                    data["sub_tasks"][target_ticket][target_subtask]["status"] = "focused"
                    data["focused_subtask"] = target_subtask
                    # Start timer for new subtask if work session is active
                    if work_session.get("active") and not work_session.get("paused"):
                        work_session["current_timer_start_ts"] = datetime.now().timestamp()
                        work_session["last_activity_ts"] = datetime.now().timestamp()
                else:
                    # Clear timer when focusing on ticket without subtask
                    if work_session.get("active"):
                        work_session.pop("current_timer_start_ts", None)

                data_was_modified = True
                show_notification(stdscr, t('cmd_info_focus_set', name=target_ticket))
            else:
                show_notification(stdscr, t('cmd_err_ticket_not_found', name=identifier))
        else:
            # Clear focus if command is just 'focus'
            data["focused_ticket"] = None
            data["focused_subtask"] = None
            for ticket_subtasks in data["sub_tasks"].values():
                for st in ticket_subtasks.values():
                    st["focused"] = False
            data_was_modified = True
            show_notification(stdscr, t('cmd_info_focus_cleared'))


    elif command == 'note':
        if not current_ticket_name_val:
            show_notification(stdscr, t('cmd_err_no_active_task_for_note'))
            return "NO_CHANGE"
        if len(command_parts) > 1:
            note_text_cmd = " ".join(command_parts[1:])
            if selected_subtask_idx != -1 and 0 <= selected_subtask_idx < len(current_ticket_subtask_list):
                selected_sub_task_name_cmd, _ = current_ticket_subtask_list[selected_subtask_idx]
                if current_ticket_name_val in data.get("sub_tasks", {}):
                    sub_task_details_cmd = data["sub_tasks"][current_ticket_name_val].get(selected_sub_task_name_cmd)
                    if sub_task_details_cmd and isinstance(sub_task_details_cmd, dict):
                        sub_task_details_cmd.setdefault("notes", []).append(note_text_cmd)
                        data_was_modified = True
                        show_notification(stdscr, t('cmd_info_note_added_to_subtask', name=selected_sub_task_name_cmd))
                    else: show_notification(stdscr, t('cmd_err_subtask_details_not_found', name=selected_sub_task_name_cmd))
                else: show_notification(stdscr, t('cmd_err_main_task_details_not_found', name=current_ticket_name_val))
            else:
                data.setdefault("notes", {}).setdefault(current_ticket_name_val, []).append(note_text_cmd)
                data_was_modified = True
                show_notification(stdscr, t('cmd_info_note_added_to_task', name=current_ticket_name_val))
        else: show_notification(stdscr, t('cmd_usage_add_note'))

    elif command == 'p' or command == 'k':
        event_type = 'meeting' if command == 'p' else 'interruption'
        usage_msg = t('cmd_usage_add_meeting_event', command=command)
        if len(command_parts) < 3:
            show_notification(stdscr, usage_msg)
            return "NO_CHANGE"
        arg1 = command_parts[1].lower()
        is_recurring = arg1 in WEEKDAY_MAP
        if is_recurring:
            if len(command_parts) < 4:
                 show_notification(stdscr, usage_msg)
                 return "NO_CHANGE"
            weekday_str = arg1; time_str = command_parts[2]; details = " ".join(command_parts[3:])
            try:
                datetime.strptime(time_str, "%H:%M"); weekday_int = WEEKDAY_MAP[weekday_str]
                data.setdefault("recurring_events", []).append({'type': event_type, 'weekday': weekday_int,'time': time_str, 'details': details})
                data_was_modified = True
                show_notification(stdscr, t('cmd_info_recurring_event_added', type=event_type, day=weekday_str.upper(), time=time_str))
            except ValueError: show_notification(stdscr, t('cmd_err_invalid_time', time=time_str))
        else:
            time_str = command_parts[1]; details = " ".join(command_parts[2:])
            target_list_key = "meetings" if event_type == 'meeting' else "interruptions"
            try:
                time_obj = datetime.strptime(time_str, "%H:%M").time()
                event_datetime = datetime.combine(date.today(), time_obj)
                if event_datetime < datetime.now() - timedelta(minutes=5): event_datetime += timedelta(days=1)
                details_key = 'link' if event_type == 'meeting' else 'message'
                data.setdefault(target_list_key, []).append({"datetime": event_datetime.isoformat(), details_key: details})
                data_was_modified = True
                show_notification(stdscr, t('cmd_info_event_added', type=event_type, datetime=event_datetime.strftime('%Y-%m-%d %H:%M')))
            except ValueError: show_notification(stdscr, t('cmd_err_invalid_time', time=time_str))

    elif command == 'startday':
        work_session = data.setdefault("work_session", {})
        if work_session.get("active"):
            show_notification(stdscr, t('work_session_already_active'))
        else:
            work_session["active"] = True
            work_session["start_time"] = datetime.now().isoformat()
            work_session["current_timer_start_ts"] = datetime.now().timestamp()
            work_session["last_activity_ts"] = datetime.now().timestamp()
            data_was_modified = True
            show_notification(stdscr, t('work_session_started'))
    
    elif command == 'endday':
        work_session = data.get("work_session", {})
        if not work_session.get("active"):
            show_notification(stdscr, t('work_session_not_active'))
        else:
            # Log any remaining time if there's an active timer
            if work_session.get("current_timer_start_ts") and data.get("focused_subtask"):
                elapsed_seconds = int(datetime.now().timestamp() - work_session["current_timer_start_ts"])
                if elapsed_seconds > 0:
                    from inc.time_tracker import add_time_entry
                    focused_ticket = data.get("focused_ticket")
                    focused_subtask = data.get("focused_subtask")
                    if focused_ticket and focused_subtask:
                        # Use the standardized format for time logging
                        normalized_subtask = f"[{focused_ticket}] {focused_subtask}"
                        add_time_entry(data, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
            
            work_session["active"] = False
            work_session["end_time"] = datetime.now().isoformat()
            work_session.pop("current_timer_start_ts", None)
            data_was_modified = True
            show_notification(stdscr, t('work_session_ended'))
    
    elif command == 'pause':
        work_session = data.get("work_session", {})
        if not work_session.get("active"):
            show_notification(stdscr, t('work_session_not_active'))
        elif work_session.get("paused"):
            show_notification(stdscr, t('work_session_already_paused'))
        else:
            # Log current timer if running
            if work_session.get("current_timer_start_ts") and data.get("focused_subtask"):
                elapsed_seconds = int(datetime.now().timestamp() - work_session["current_timer_start_ts"])
                if elapsed_seconds > 0:
                    from inc.time_tracker import add_time_entry
                    focused_ticket = data.get("focused_ticket")
                    focused_subtask = data.get("focused_subtask")
                    if focused_ticket and focused_subtask:
                        # Use the standardized format for time logging
                        normalized_subtask = f"[{focused_ticket}] {focused_subtask}"
                        add_time_entry(data, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
            
            work_session["paused"] = True
            work_session["pause_time"] = datetime.now().isoformat()
            work_session.pop("current_timer_start_ts", None)
            data_was_modified = True
            show_notification(stdscr, t('work_session_paused'))
    
    elif command == 'resume':
        work_session = data.get("work_session", {})
        if not work_session.get("active"):
            show_notification(stdscr, t('work_session_not_active'))
        elif not work_session.get("paused"):
            show_notification(stdscr, t('work_session_not_paused'))
        else:
            work_session["paused"] = False
            work_session["resume_time"] = datetime.now().isoformat()
            work_session["current_timer_start_ts"] = datetime.now().timestamp()
            work_session["last_activity_ts"] = datetime.now().timestamp()
            data_was_modified = True
            show_notification(stdscr, t('work_session_resumed'))
    
    elif command == 'timelog' or command == 'log':
        return "VIEW_TIME_LOG"
    
    elif command == 'logtime':
        if len(command_parts) >= 2:
            try:
                # Two formats supported:
                # logtime <minutes> [date] - logs to focused subtask
                # logtime <subtask> <minutes> [date] - logs to specified subtask
                
                subtask_name = None
                minutes = None
                target_date = date.today().isoformat()
                
                # Try to parse first argument as minutes (focused subtask mode)
                try:
                    minutes = int(command_parts[1])
                    # First arg is minutes, use focused subtask
                    if data.get("focused_subtask") and data.get("focused_ticket"):
                        subtask_name = f"[{data['focused_ticket']}] {data['focused_subtask']}"
                    else:
                        show_notification(stdscr, "No subtask currently focused. Use: logtime <subtask> <minutes> [date]")
                        return "NO_CHANGE"
                    
                    # Check for optional date in 3rd position
                    if len(command_parts) >= 3:
                        target_date = command_parts[2]
                        date.fromisoformat(target_date)  # Validate date format
                        
                except ValueError:
                    # First arg is not minutes, assume it's subtask name
                    if len(command_parts) >= 3:
                        subtask_name = command_parts[1]
                        minutes = int(command_parts[2])
                        
                        # Check for optional date in 4th position
                        if len(command_parts) >= 4:
                            target_date = command_parts[3]
                            date.fromisoformat(target_date)  # Validate date format
                    else:
                        show_notification(stdscr, "Usage: logtime <minutes> [date] OR logtime <subtask> <minutes> [date]")
                        return "NO_CHANGE"
                
                # Convert minutes to seconds
                seconds = minutes * 60
                
                # Add the time entry
                from inc.time_tracker import add_time_entry
                add_time_entry(data, entry_type="task", subtask=subtask_name, seconds=seconds, entry_date_iso=target_date)
                data_was_modified = True
                
                # Show appropriate notification
                from inc.time_tracker import normalize_subtask_identifier
                display_name = normalize_subtask_identifier(subtask_name)
                show_notification(stdscr, f"Logged {minutes} minutes for {display_name}")
                
            except ValueError as e:
                show_notification(stdscr, f"Invalid time or date format: {str(e)}")
        else:
            if data.get("focused_subtask"):
                show_notification(stdscr, "Usage: logtime <minutes> [date] OR logtime <subtask> <minutes> [date]")
            else:
                show_notification(stdscr, "Usage: logtime <subtask> <minutes> [date] (no subtask focused)")

    elif command == 'q':
        return None

    elif len(command_parts) > 0 :
        identifier = " ".join(command_parts)
        target_ticket_name_to_activate = None

        try:
            target_idx_1_based = int(identifier)
            if 1 <= target_idx_1_based <= len(all_displayable_tickets_for_cmd):
                target_ticket_name_to_activate = all_displayable_tickets_for_cmd[target_idx_1_based - 1]
            else:
                show_notification(stdscr, t('cmd_err_invalid_index', index=target_idx_1_based))
                return "NO_CHANGE"
        except ValueError:
            matches = []
            for t_name in all_displayable_tickets_for_cmd:
                if identifier.lower() in t_name.lower():
                    matches.append(t_name)
            if len(matches) == 0:
                show_notification(stdscr, t('cmd_err_unknown_command_or_ticket', id=identifier))
                return "NO_CHANGE"
            elif len(matches) == 1:
                target_ticket_name_to_activate = matches[0]
            else:
                options_str = ", ".join([f"'{name}'" for name in matches[:3]])
                if len(matches) > 3: options_str += "..."
                show_notification(stdscr, t('cmd_err_multiple_tickets_found', options=options_str))
                return "NO_CHANGE"

        if target_ticket_name_to_activate:
            if data.get("current_ticket") == target_ticket_name_to_activate:
                show_notification(stdscr, t('cmd_err_task_already_active', name=target_ticket_name_to_activate))
                return "NO_CHANGE"
            pause_current_task(data)
            found_in_paused_and_removed = False
            for i, paused_task_item in enumerate(data.get("paused_tasks", [])):
                if paused_task_item.get("ticket") == target_ticket_name_to_activate:
                    resumed_item_details = data["paused_tasks"].pop(i)
                    data['current_ticket'] = target_ticket_name_to_activate
                    data['task_start_time'] = resumed_item_details.get('task_start_time', time.time())
                    resumed_sub_tasks_raw = resumed_item_details.get('sub_tasks', {})
                    migrated_resumed_sub_tasks = {}
                    if isinstance(resumed_sub_tasks_raw, dict):
                        for sub_name, sub_details in resumed_sub_tasks_raw.items():
                            if not isinstance(sub_details, dict):
                                migrated_resumed_sub_tasks[sub_name] = {"status": "done" if bool(sub_details) else "todo", "notes": [], "pr_url": None, "pr_status": None, "jira_refreshed": None}
                            else:
                                current_status = sub_details.get("status", "todo")
                                if sub_details.get("hidden", False):
                                    current_status = "hidden"
                                elif sub_details.get("done", False):
                                    current_status = "done"
                                elif sub_details.get("focused", False):
                                    current_status = "focused"

                                sub_details["status"] = current_status
                                sub_details.setdefault("notes", [])
                                sub_details.setdefault("pr_url", None)
                                sub_details.setdefault("pr_status", None)
                                sub_details.setdefault("jira_refreshed", None)

                                sub_details.pop("done", None)
                                sub_details.pop("hidden", None)
                                sub_details.pop("focused", None)
                                migrated_resumed_sub_tasks[sub_name] = sub_details
                    data.setdefault("sub_tasks", {})[target_ticket_name_to_activate] = migrated_resumed_sub_tasks
                    data.setdefault("notes", {})[target_ticket_name_to_activate] = resumed_item_details.get('notes', [])
                    found_in_paused_and_removed = True; break

            if not found_in_paused_and_removed:
                data['current_ticket'] = target_ticket_name_to_activate
                data['task_start_time'] = time.time()
                current_subs = data.setdefault("sub_tasks", {}).setdefault(target_ticket_name_to_activate, {})
                for sub_name, sub_details in list(current_subs.items()):
                    if not isinstance(sub_details, dict):
                        sub_details.setdefault("status", "todo"); sub_details.setdefault("notes", []); sub_details.setdefault("pr_url", None); sub_details.setdefault("pr_status", None); sub_details.setdefault("jira_refreshed", False)

                data.setdefault("notes", {}).setdefault(target_ticket_name_to_activate, [])
            data_was_modified = True
            show_notification(stdscr, t('cmd_info_switched_to_task', name=target_ticket_name_to_activate))
    else:
        if current_view_mode == VIEW_MAIN:
            if command_parts and command_parts[0]:
                show_notification(stdscr, t('cmd_err_unknown_command', command=command_parts[0]))
    return data if data_was_modified else "NO_CHANGE"


def format_subtask_for_title(subtask_name):
    """Extracts the last part of a URL-like subtask name for a cleaner title."""
    if subtask_name.startswith("http"):
        try:
            return [part for part in subtask_name.split('/') if part][-1]
        except IndexError:
            return subtask_name
    return subtask_name

def send_desktop_notification(title, message):
    """Sends a desktop notification using notify-send."""
    try:
        subprocess.run(['/usr/bin/notify-send', title, message], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"Could not send notification: {e}", file=sys.stderr)

def focus_window(window_title):
    """Focuses the terminal window with the given title using xdotool."""
    try:
        subprocess.run(['/usr/bin/xdotool', 'search', '--name', window_title, 'windowactivate'], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass # Silently fail if xdotool is not available or fails

def poll_reviews_needed():
    """Polls for pull requests that need the user's review."""
    global pull_requests_for_review, sent_review_notifications

    api_token = inc.config_manager.config.get("API_TOKEN")
    user_id = inc.config_manager.config.get("USER_ID")
    review_url = inc.config_manager.config.get("STASH_REVIEW_URL")

    if not all([api_token, user_id, review_url]) or "your-stash-instance.com" in review_url:
        return # Missing essential config or using placeholder

    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json;charset=UTF-8"}

    while True:
        try:
            response = requests.get(review_url, headers=headers, timeout=20)
            response.raise_for_status()
            prs_data = response.json()

            pending_reviews = []
            for pr in prs_data.get('values', []):
                for reviewer in pr.get('reviewers', []):
                    if reviewer.get('user', {}).get('id') == user_id and reviewer.get('status') == 'UNAPPROVED':
                        pending_reviews.append(pr)
                        # Handle notifications
                        if pr['id'] not in sent_review_notifications:
                            repo = f"{pr['links']['self'][0]['href']}"
                            notif_title = t('notification_review_title')
                            notif_body = t('notification_review_body', repo=repo, title=pr['title'])
                            send_desktop_notification(pr['title'], repo)
                            sent_review_notifications.add(pr['id'])
                        break # Move to next PR once user is found as unapproved reviewer

            with reviews_lock:
                pull_requests_for_review.clear()
                pull_requests_for_review.extend(pending_reviews)

        except requests.exceptions.RequestException as e:
            print(t('polling_err', url=review_url, e=e), file=sys.stderr)
            pass # Silently continue on network errors

        # Clear sent notification list if no PRs are pending review, so user gets notified again if they reappear
        with reviews_lock:
             current_review_ids = {pr['id'] for pr in pull_requests_for_review}
             sent_review_notifications.intersection_update(current_review_ids)

        time.sleep(300) # Poll every 5 minutes

def poll_pull_requests(data_lock, data_ref):
    api_token = inc.config_manager.config.get("API_TOKEN")
    my_user_id = inc.config_manager.config.get("USER_ID")

    while True:
        with data_lock:
            data_changed = False
            data_copy = copy.deepcopy(data_ref)

            for ticket, subtasks in data_copy.get("sub_tasks", {}).items():
                if not isinstance(subtasks, dict): continue
                for subtask_name, subtask_details in subtasks.items():
                    if not isinstance(subtask_details, dict): continue

                    original_subtask = data_ref["sub_tasks"][ticket][subtask_name]
                    pr_url = original_subtask.get("pr_url")
                    pr_status = original_subtask.get("pr_status")

                    if original_subtask.get("status") == "hidden" or not pr_url or pr_status == 'merged':
                        continue

                    api_url = convert_to_api_url(pr_url)
                    if not api_url: continue

                    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json;charset=UTF-8"}
                    try:

                        reviewers_response = requests.get(api_url, headers=headers, timeout=10)
                        reviewers_response.raise_for_status()
                        reviewers = reviewers_response.json()

                        api_url = f"{convert_to_api_url(pr_url)}/activities"
                        response = requests.get(api_url, headers=headers, timeout=10)
                        response.raise_for_status()
                        activities = response.json()

                        # logging.info(activities)

                        is_merged = False
                        unique_approvers = set()
                        for activity in activities.get("values", []):
                            action = activity.get("action")
                            if action == "MERGED":
                                is_merged = True
                                break
                            if action == "APPROVED":
                                approver_id = activity.get("user", {}).get("id")
                                if approver_id:
                                    unique_approvers.add(approver_id)





                        # Format approvers
                        approvers_formatted = []
                        approver_count = 0
                        total_reviewers = len(reviewers.get('reviewers', []))
                        for r in reviewers.get('reviewers', []):
                            status_emoji = "❓" # Not responded
                            if r['status'] == 'APPROVED':
                                status_emoji = "✅"
                                approver_count += 1
                            elif r['status'] == 'NEEDS_WORK':
                                status_emoji = "❌"
                            approvers_formatted.append(f"{status_emoji} {r['user']['displayName']}")

                        # Determine overall status text
                        status_text = "waiting"
                        if activities.get('state') == 'MERGED':
                            status_text = "merged"
                        elif activities.get('state') == 'DECLINED':
                            status_text = "declined"
                        elif approver_count > 0:
                            status_text = f"approved ({approver_count}/{total_reviewers})"

                        # Store in the main data object

                        original_subtask = app_data["sub_tasks"][ticket][subtask_name]
                        original_subtask['pr_details'] = {
                            'status_text': status_text,
                            'approvers_formatted': approvers_formatted
                        }
                        data_changed = True






                        if is_merged:
                            if pr_status != 'merged':
                                original_subtask['pr_status'] = 'merged'
                                notes = original_subtask.get('notes', [])
                                original_subtask['notes'] = [n for n in notes if not n.startswith("UNHANDLED") and not n.startswith(t('polling_note_approved'))]
                                data_changed = True
                                send_desktop_notification(t('notification_pr_merged_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), t('notification_pr_merged_body', pr_url=pr_url))
                        elif len(unique_approvers) >= 2:
                            if pr_status != 'approved':
                                original_subtask['pr_status'] = 'approved'
                                notes = original_subtask.get('notes', [])
                                notes_to_keep = [n for n in notes if not n.startswith("UNHANDLED")]
                                if t('polling_note_approved') not in notes_to_keep:
                                    notes_to_keep.append(t('polling_note_approved'))
                                original_subtask['notes'] = notes_to_keep
                                data_changed = True
                                send_desktop_notification(t('notification_pr_approved_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), t('notification_pr_approved_body', pr_url=pr_url))
                        else:
                            notes = original_subtask.get("notes", [])
                            notes_without_unhandled = [n for n in notes if not n.startswith("*PR* ")]
                            if len(notes_without_unhandled) < len(notes):
                                original_subtask["notes"] = notes_without_unhandled
                                data_changed = True

                            unhandled_comments = check_for_unhandled_comments(activities, my_user_id)
                            if unhandled_comments:
                                if pr_status != 'attention_needed':
                                    original_subtask['pr_status'] = 'attention_needed'
                                    data_changed = True
                                    send_desktop_notification(t('notification_pr_unhandled_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), t('notification_pr_unhandled_body', pr_url=pr_url))

                                for comment in unhandled_comments:
                                    note = t('polling_note_unhandled_comment', author=comment['author']['displayName'], text=comment['text'])
                                    if note not in original_subtask["notes"]:
                                        original_subtask["notes"].append(note)
                                        data_changed = True
                            else:
                                if pr_status == 'attention_needed':
                                    original_subtask['pr_status'] = None
                                    data_changed = True

                    except requests.exceptions.RequestException as e:
                        print(t('polling_err', url=api_url, e=e), file=sys.stderr)
                        pass

            if data_changed:
                save_data(data_ref)

        time.sleep(300)

def convert_to_api_url(pr_url):
    match = re.search(r'projects/(?P<projectKey>[^/]+)/repos/(?P<repositorySlug>[^/]+)/pull-requests/(?P<pullRequestId>\d+)', pr_url)
    if match:
        parts = match.groupdict()
        return f"{inc.config_manager.config.get('STASH_URL')}/rest/api/1.0/projects/{parts['projectKey']}/repos/{parts['repositorySlug']}/pull-requests/{parts['pullRequestId']}"
    return None

def check_for_unhandled_comments(activities, my_user_id):
    unhandled_comments = []
    for value in activities.get("values", []):
        if value.get("action") == "COMMENTED":
            comment = value.get("comment")
            if comment and comment.get("author", {}).get("id") != my_user_id:
                has_my_reply = False
                for reply in comment.get("comments", []):
                    if reply.get("author", {}).get("id") == my_user_id:
                        has_my_reply = True
                        break
                if not has_my_reply:
                    unhandled_comments.append(comment)
    return unhandled_comments

def event_notification_poller(data_lock, data_ref):
    """A thread that checks for upcoming events and sends notifications."""
    global sent_notifications

    def get_next_occurrence(recurring_event, now):
        """Calculates the next occurrence of a recurring event."""
        try:
            target_weekday = int(recurring_event['weekday']) # 0=Mon
            event_time_str = recurring_event['time'] # "HH:MM"
            event_time = datetime.strptime(event_time_str, "%H:%M").time()

            current_weekday = now.weekday() # 0=Mon
            days_ahead = target_weekday - current_weekday
            if days_ahead < 0: # Target day already passed this week
                days_ahead += 7
            elif days_ahead == 0 and now.time() >= event_time: # Target is today, but time has passed
                days_ahead += 7

            next_date = (now + timedelta(days=days_ahead)).date()
            return datetime.combine(next_date, event_time)
        except (ValueError, KeyError, TypeError):
            return None


    def open_link_in_browser(url, browser_cmd):
        try:
            if browser_cmd and isinstance(browser_cmd, list):
                subprocess.Popen(browser_cmd + [url])
            else:
                webbrowser.open(url)
        except Exception as e:
            print(t('error_browser_open', e=e), file=sys.stderr)


    while True:
        now = datetime.now()

        if now.hour == 0 and now.minute == 0: # Daily reset
            sent_notifications.clear()

        all_upcoming_events = []
        with data_lock:
            # Make a deep copy to work with, to release the lock quickly
            meetings = copy.deepcopy(data_ref.get("meetings", []))
            interruptions = copy.deepcopy(data_ref.get("interruptions", []))
            recurring = copy.deepcopy(data_ref.get("recurring_events", []))

        # Process external calendar events
        with external_meetings_lock:
            current_external_meetings = copy.deepcopy(external_meetings)

        for event in current_external_meetings:
            try:
                time_obj = datetime.strptime(event['start_time'], "%H:%M").time()
                dt = datetime.combine(date.today(), time_obj)
                if dt > now:
                    all_upcoming_events.append({
                        'datetime': dt,
                        'type': 'external_meeting',
                        'details': event,
                        'recurring': False
                    })
            except (ValueError, KeyError):
                continue

        # Process one-time events
        for event in meetings + interruptions:
            try:
                dt = datetime.fromisoformat(event['datetime'])
                if dt > now:
                    evt_type = 'meeting' if 'link' in event else 'interruption'
                    details = event.get('link') or event.get('message', '')
                    all_upcoming_events.append({'datetime': dt, 'type': evt_type, 'details': details, 'recurring': False})
            except (ValueError, TypeError):
                continue

        # Process recurring events
        for event in recurring:
            next_occurrence = get_next_occurrence(event, now)
            if next_occurrence:
                all_upcoming_events.append({
                    'datetime': next_occurrence,
                    'type': event.get('type'),
                    'details': event.get('details'),
                    'recurring': True
                })

        # Check for notifications
        for event in all_upcoming_events:
            time_diff = event['datetime'] - now
            if timedelta(seconds=0) <= time_diff < timedelta(minutes=11):
                minutes_until = int(time_diff.total_seconds() / 60)

                event_time_str = event['datetime'].strftime('%H:%M')
                event_id = f"{event['type']}_{event['details']}_{event['datetime'].strftime('%Y%m%d%H%M')}"

                notification_title = ""
                notification_body = ""

                if event['type'] == 'meeting' or event['type'] == 'external_meeting':
                    rec_str = f"({t('recurring')}) " if event['recurring'] else ""
                    
                    if event['type'] == 'external_meeting':
                        details = event.get('details', {})
                        rec_str = f"({details.get('title', '')}) "
                        notification_body = t('notification_meeting_body', link=f"{details.get('url', '')}")
                    else:
                        notification_body = t('notification_meeting_body', link=event['details'])

                    notification_title = t('notification_meeting_title', rec=rec_str, min=minutes_until, time=event_time_str)
                    
                else: # interruption
                    rec_str = f"({t('recurring')}) " if event['recurring'] else ""
                    notification_title = t('notification_event_title', rec=rec_str, min=minutes_until, time=event_time_str)
                    notification_body = event['details']

                # 10-minute warning
                if minutes_until == 10 and (event_id, '10min') not in sent_notifications:
                    focus_window(inc.config_manager.config.get("NOTIFICATION_WINDOW_TITLE"))
                    send_desktop_notification(notification_title, notification_body)
                    sent_notifications.add((event_id, '10min'))

                # 5-minute warning
                elif minutes_until == 5 and (event_id, '5min') not in sent_notifications:
                    focus_window(inc.config_manager.config.get("NOTIFICATION_WINDOW_TITLE"))
                    send_desktop_notification(notification_title, notification_body)
                    sent_notifications.add((event_id, '5min'))
                    if event['type'] == 'meeting' and event.get('details', '').startswith('http'):
                        open_link_in_browser(event['details'], inc.config_manager.config.get("BROWSER_COMMAND"))
                    elif event['type'] == 'external_meeting':
                        url_to_open = event.get('details', {}).get('url', '')
                        if url_to_open.startswith('http'):
                            open_link_in_browser(url_to_open, inc.config_manager.config.get("BROWSER_COMMAND"))

        time.sleep(60)


def main(stdscr):
    global COLOR_PAIR_DEFAULT, COLOR_PAIR_REVERSE, COLOR_PAIR_GREY, COLOR_PAIR_PAUSED, COLOR_PAIR_SELECTED, COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN, COLOR_PAIR_URGENT_BOX, COLOR_PAIR_PR_UNHANDLED, COLOR_PAIR_PR_APPROVED, COLOR_PAIR_FOCUSED, COLOR_PAIR_PERMANENT_NOTIFICATION, COLOR_PAIR_STANDOUT
    global app_data, permanent_notifications
    stop_event = threading.Event()
    jira_cache = load_jira_cache()
    jira_cache_lock = threading.Lock()
    data_lock = threading.Lock()
    result = "EXIT"

    if not inc.config_manager.STRINGS:
        print(f"Fatal: Could not load language files. Exiting.", file=sys.stderr)
        return "EXIT"

    if inc.config_manager.config.get("API_TOKEN") == "PASTE_YOUR_BEARER_TOKEN_HERE":
        print("ERROR: API_TOKEN has not been set in config.json. Please update it and restart.", file=sys.stderr)
        return "EXIT"

    try:
        curses.start_color()
        bg = curses.COLOR_BLACK
        CUSTOM_GRAY_COLOR_ID = 8
        curses.init_color(CUSTOM_GRAY_COLOR_ID, 500, 500, 500)

        curses.init_pair(COLOR_PAIR_DEFAULT, curses.COLOR_WHITE, bg)
        curses.init_pair(COLOR_PAIR_REVERSE, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(COLOR_PAIR_GREY, curses.COLOR_BLUE, bg)
        curses.init_pair(COLOR_PAIR_PAUSED, curses.COLOR_YELLOW, bg)
        curses.init_pair(COLOR_PAIR_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, curses.COLOR_GREEN, bg)
        curses.init_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN, CUSTOM_GRAY_COLOR_ID, bg)
        curses.init_pair(COLOR_PAIR_URGENT_BOX, curses.COLOR_RED, bg)
        curses.init_pair(COLOR_PAIR_PR_UNHANDLED, curses.COLOR_WHITE, curses.COLOR_RED)
        curses.init_pair(COLOR_PAIR_PR_APPROVED, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(COLOR_PAIR_FOCUSED, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(COLOR_PAIR_PERMANENT_NOTIFICATION, curses.COLOR_BLACK, curses.COLOR_RED)
        curses.init_pair(COLOR_PAIR_STANDOUT, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(COLOR_PAIR_NEW_COMMENT, curses.COLOR_WHITE, curses.COLOR_MAGENTA)

    except: pass

    try: curses.curs_set(1)
    except curses.error: pass
    stdscr.nodelay(True)
    stdscr.keypad(True)
    
    # Enable mouse support for scrolling
    try:
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    except:
        pass  # Mouse support is optional

    app_data = load_data()
    load_jira_cache()

    command_buffer = ""

    current_view = VIEW_MAIN
    selected_subtask_index = -1
    selected_note_index = -1
    entity_for_dedicated_notes = None
    show_help_footer = False
    current_date_for_daily_notes = date.today()
    selected_checkin_task_index = -1
    
    # Scroll offset for notes views (tracks how much content has been scrolled up)
    notes_scroll_offset = 0
    
    # Initialize time tracking scheduler
    from inc.time_tracker import HourlyCheckinScheduler, note_user_activity
    time_scheduler = HourlyCheckinScheduler(app_data, inc.config_manager.config, data_lock)
    time_scheduler.start()

    pr_polling_thread = threading.Thread(target=poll_pull_requests, args=(data_lock, app_data), daemon=True)
    pr_polling_thread.start()

    jira_thread = threading.Thread(target=jira_queue_worker, args=(stop_event, permanent_notifications, jira_cache, jira_cache_lock), daemon=True)
    jira_thread.start()

    notification_thread = threading.Thread(target=event_notification_poller, args=(data_lock, app_data), daemon=True)
    notification_thread.start()

    review_polling_thread = threading.Thread(target=poll_reviews_needed, args=(), daemon=True)
    review_polling_thread.start()

    calendar_polling_thread = threading.Thread(target=poll_external_calendar, args=(), daemon=True)
    calendar_polling_thread.start()

    web_polling_thread = threading.Thread(target=poll_web_pages, args=(), daemon=True)
    web_polling_thread.start()

    clock_refresh_interval = 1.0; last_clock_refresh_time = 0.0
    content_refresh_interval = 120.0; last_content_refresh_time = 0.0
    request_full_redraw = True
    previous_window_size = (0,0)

    ticket_name_at_loop_start = app_data.get("current_ticket")

    old_selected_subtask_index = -1

    while True:
        current_time = time.time()
        try: new_height, new_width = stdscr.getmaxyx()
        except curses.error: break

        if (new_height, new_width) != previous_window_size:
            request_full_redraw = True
            previous_window_size = (new_height, new_width)
        height, width = new_height, new_width

        with data_lock:
            ticket_name_at_loop_start = app_data.get("current_ticket")
            
            # Check for pending hourly check-in
            if app_data.get("pending_checkin") and current_view == VIEW_MAIN:
                current_view = VIEW_HOURLY_CHECKIN
                selected_checkin_task_index = -1  # Reset selection
                command_buffer = ""
                request_full_redraw = True
                
                # Send desktop notification to get user's attention
                pending_checkin = app_data.get("pending_checkin", {})
                duration_seconds = pending_checkin.get("duration_seconds", 3600)
                duration_minutes = duration_seconds // 60
                notification_title = "⏰ Hourly Check-in Time!"
                notification_body = f"Please account for the last {duration_minutes} minutes. What were you working on?"
                send_desktop_notification(notification_title, notification_body)
                
                # Focus the terminal window to get user's attention
                window_title = inc.config_manager.config.get("NOTIFICATION_WINDOW_TITLE")
                if window_title:
                    focus_window(window_title)

            completed_tickets = app_data.get("completed_tickets", [])
            current_ticket_subtasks_unfiltered = app_data.get("sub_tasks", {}).get(ticket_name_at_loop_start, {}) if ticket_name_at_loop_start else {}
            current_ticket_subtask_list_visible = []
            if isinstance(current_ticket_subtasks_unfiltered, dict):
                show_hidden = app_data.get("show_hidden_tasks", False)
                current_ticket_subtask_list_visible = [
                    (name, details) for name, details in current_ticket_subtasks_unfiltered.items()
                    if isinstance(details, dict) and (show_hidden or not details.get("status") == "hidden")
                ]

            all_tickets_set_for_cmd = set()
            if app_data.get("current_ticket"): all_tickets_set_for_cmd.add(app_data.get("current_ticket"))
            all_tickets_set_for_cmd.update(app_data.get("sub_tasks", {}).keys())
            all_tickets_set_for_cmd.update(app_data.get("notes", {}).keys())
            for paused_item_cmd in app_data.get("paused_tasks", []):
                if paused_item_cmd.get("ticket"): all_tickets_set_for_cmd.add(paused_item_cmd["ticket"])

            all_displayable_tickets_for_handle_input = sorted([t for t in list(filter(None, all_tickets_set_for_cmd)) if t not in completed_tickets])

        notifications_to_remove = []
        for msg in permanent_notifications:
            if "New Jira comment" in msg or "New Trello comment" in msg:
                if msg not in sent_notifications:
                    send_desktop_notification("New Comment", msg)
                    sent_notifications.add(msg)
                notifications_to_remove.append(msg)
        
        if notifications_to_remove:
            permanent_notifications = [n for n in permanent_notifications if n not in notifications_to_remove]


        key = -1
        try: key = stdscr.get_wch()
        except curses.error: pass
        except KeyboardInterrupt: break

        user_activity_caused_draw_this_cycle = False

        

        if key != -1:
            last_content_refresh_time = current_time
            last_clock_refresh_time = current_time
            user_activity_caused_draw_this_cycle = True
            
            # Note user activity for time tracking
            with data_lock:
                note_user_activity(app_data)

            if key == curses.KEY_BTAB:
                if current_view == VIEW_MAIN:
                    with data_lock:
                        active_main_ticket = app_data.get("current_ticket")
                    if selected_subtask_index != -1 and 0 <= selected_subtask_index < len(current_ticket_subtask_list_visible):
                        sub_name, _ = current_ticket_subtask_list_visible[selected_subtask_index]
                        entity_for_dedicated_notes = {"type": "subtask", "name": sub_name, "main_task_name": active_main_ticket}
                        current_view = VIEW_DEDICATED_NOTES
                    elif active_main_ticket:
                        entity_for_dedicated_notes = {"type": "task", "name": active_main_ticket}
                        current_view = VIEW_DEDICATED_NOTES
                    if current_view == VIEW_DEDICATED_NOTES:
                        command_buffer = ""; request_full_redraw = True; selected_note_index = -1; notes_scroll_offset = 0
                elif current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES, VIEW_TIME_LOG]:
                    current_view = VIEW_MAIN
                    entity_for_dedicated_notes = None; selected_note_index = -1
                    command_buffer = ""; request_full_redraw = True

            elif key == 27: # ESC key
                if current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES, VIEW_TIME_LOG]:
                    current_view = VIEW_MAIN
                    entity_for_dedicated_notes = None; selected_note_index = -1
                    command_buffer = ""; request_full_redraw = True
                elif current_view == VIEW_HOURLY_CHECKIN:
                    # Cancel/ignore the check-in
                    with data_lock:
                        app_data["pending_checkin"] = None
                        save_data(app_data)
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""; request_full_redraw = True

            if current_view == VIEW_MAIN:

                ##### JIRA LOGIN CHECK ######
                if f"{t('jira_login_prompt', service='Trello')}" in permanent_notifications or f"{t('jira_login_prompt', service='Jira')}" in permanent_notifications or t('jira_session_error') in permanent_notifications:
                    logging.error("Restarting app for login")
                    # permanent_notifications = []
                    # return "RESTART_FOR_LOGIN"

                if key == curses.KEY_LEFT:
                    current_view = VIEW_DAILY_NOTES
                    current_date_for_daily_notes = date.today()
                    command_buffer = ""; request_full_redraw = True; selected_note_index = -1
                

                elif key == curses.KEY_UP:
                    if current_ticket_subtask_list_visible:
                        if selected_subtask_index > -1:
                            selected_subtask_index -= 1
                        request_full_redraw = True
                        if selected_subtask_index != -1:
                            sub_task_name, _ = current_ticket_subtask_list_visible[selected_subtask_index]
                            jira_ticket_id = inc.helpers.get_jira_ticket_from_url(sub_task_name)
                            with jira_cache_lock:
                                if jira_ticket_id in jira_cache and (jira_cache[jira_ticket_id].get('new_jira_comment') or jira_cache[jira_ticket_id].get('new_trello_comment')):
                                    jira_cache[jira_ticket_id]['new_jira_comment'] = False
                                    jira_cache[jira_ticket_id]['new_trello_comment'] = False
                                    save_data(app_data) # Persist the change
                elif key == curses.KEY_DOWN:
                    if current_ticket_subtask_list_visible:
                        last_idx = len(current_ticket_subtask_list_visible) - 1
                        if selected_subtask_index < last_idx:
                            selected_subtask_index += 1
                        else:
                            selected_subtask_index = -1
                        request_full_redraw = True
                        if selected_subtask_index != -1:
                            sub_task_name, _ = current_ticket_subtask_list_visible[selected_subtask_index]
                            jira_ticket_id = inc.helpers.get_jira_ticket_from_url(sub_task_name)
                            with jira_cache_lock:
                                if jira_ticket_id in jira_cache and (jira_cache[jira_ticket_id].get('new_jira_comment') or jira_cache[jira_ticket_id].get('new_trello_comment')):
                                    jira_cache[jira_ticket_id]['new_jira_comment'] = False
                                    jira_cache[jira_ticket_id]['new_trello_comment'] = False
                                    save_data(app_data) # Persist the change
                elif key == '\n' or key == curses.KEY_ENTER:
                    cmd_parts = command_buffer.split()
                    action_processed = False
                    if not cmd_parts or not cmd_parts[0]:
                        if selected_subtask_index != -1 and 0 <= selected_subtask_index < len(current_ticket_subtask_list_visible):
                            sub_task_name, sub_task_details = current_ticket_subtask_list_visible[selected_subtask_index]
                            if ticket_name_at_loop_start in app_data.get("sub_tasks", {}) and sub_task_name in app_data["sub_tasks"][ticket_name_at_loop_start]:
                                status_cycle = ["todo", "in_progress", "done"]
                                current_status = app_data["sub_tasks"][ticket_name_at_loop_start][sub_task_name].get("status", "todo")
                                try:
                                    current_index = status_cycle.index(current_status)
                                    next_index = (current_index + 1) % len(status_cycle)
                                except ValueError:
                                    next_index = 0 # Default to 'todo' if status is unknown
                                app_data["sub_tasks"][ticket_name_at_loop_start][sub_task_name]["status"] = status_cycle[next_index]
                                save_data(app_data)
                                action_processed = True
                                request_full_redraw = True

                    ticket_changed = False

                    if cmd_parts:
                        with data_lock:
                            original_ticket = app_data.get("current_ticket")
                            handle_result = handle_input(app_data, cmd_parts, stdscr, current_view, selected_subtask_index, selected_note_index, current_ticket_subtask_list_visible, all_displayable_tickets_for_handle_input)
                        if handle_result is None: break
                        elif handle_result == "RESTART_FOR_LOGIN":
                            permanent_notifications = []
                            return "RESTART_FOR_LOGIN"
                        elif handle_result == "TOGGLE_HELP": show_help_footer = not show_help_footer
                        elif handle_result == "VIEW_TIME_LOG":
                            current_view = VIEW_TIME_LOG
                            current_date_for_daily_notes = date.today()
                            command_buffer = ""; request_full_redraw = True; selected_note_index = -1
                        elif handle_result != "NO_CHANGE":
                            with data_lock:
                                app_data = handle_result
                                if app_data.get("current_ticket") != original_ticket:
                                    ticket_changed = True
                                save_data(app_data)
                        action_processed = True
                    elif selected_subtask_index != -1 and 0 <= selected_subtask_index < len(current_ticket_subtask_list_visible):
                        sub_task_name, sub_task_details = current_ticket_subtask_list_visible[selected_subtask_index]
                        with data_lock:
                            main_ticket = app_data.get("current_ticket")
                            sub_task = app_data["sub_tasks"][main_ticket].get(sub_task_name)
                            if sub_task:
                                status_cycle = ["todo", "in_progress", "done"]
                                current_status = sub_task.get("status", "todo")
                                try:
                                    current_index = status_cycle.index(current_status)
                                    next_index = (current_index + 1) % len(status_cycle)
                                except ValueError:
                                    next_index = 0 # Default to 'todo' if status is unknown
                                sub_task["status"] = status_cycle[next_index]
                                # Auto-unfocus if marked done
                                if sub_task["status"] == "done" and app_data.get("focused_subtask") == sub_task_name:
                                    app_data["focused_subtask"] = None
                                    app_data["focused_ticket"] = None
                                save_data(app_data)
                        action_processed = True

                    if action_processed or ticket_changed:
                        with data_lock:
                            new_ticket = app_data.get("current_ticket")
                        if new_ticket != ticket_name_at_loop_start:
                            selected_subtask_index = -1
                    command_buffer = ""
                    request_full_redraw = True

                elif key not in [curses.KEY_UP, curses.KEY_DOWN, curses.KEY_BTAB, 27]:
                    if isinstance(key, str) and key.isprintable():
                        max_len = (width - 1) - len("> ") if width > 0 else 0
                        if len(command_buffer) < max_len:
                            command_buffer += key
                        else:
                            try: curses.beep()
                            except: pass
                    elif key in [curses.KEY_BACKSPACE, 127, 8]:
                        command_buffer = command_buffer[:-1]
                    elif key == curses.KEY_RESIZE:
                        request_full_redraw = True

            elif current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES, VIEW_TIME_LOG]:
                notes_list_size = 0
                with data_lock:
                    if current_view == VIEW_DEDICATED_NOTES and entity_for_dedicated_notes:
                        ent_type = entity_for_dedicated_notes.get("type")
                        ent_name = entity_for_dedicated_notes.get("name")
                        if ent_type == "task":
                            notes_list_size = len(app_data.get("notes", {}).get(ent_name, []))
                        elif ent_type == "subtask":
                            main_task = entity_for_dedicated_notes.get("main_task_name")
                            sub_details = app_data.get("sub_tasks",{}).get(main_task,{}).get(ent_name)
                            if sub_details: notes_list_size = len(sub_details.get("notes", []))
                    elif current_view == VIEW_DAILY_NOTES:
                        date_iso = current_date_for_daily_notes.isoformat()
                        notes_list_size = len(app_data.get("daily_notes", {}).get(date_iso, []))

                if key == curses.KEY_UP:
                    if selected_note_index > -1:
                        selected_note_index -= 1
                    request_full_redraw = True
                elif key == curses.KEY_DOWN:
                    if notes_list_size > 0 and selected_note_index < notes_list_size - 1:
                        selected_note_index += 1
                    request_full_redraw = True
                elif key == curses.KEY_NPAGE:  # Page Down
                    notes_scroll_offset += 10  # Scroll down 10 lines
                    request_full_redraw = True
                elif key == curses.KEY_PPAGE:  # Page Up
                    notes_scroll_offset = max(0, notes_scroll_offset - 10)  # Scroll up 10 lines
                    request_full_redraw = True
                elif key == curses.KEY_MOUSE:  # Mouse events
                    try:
                        _, mx, my, _, bstate = curses.getmouse()
                        if bstate & curses.BUTTON4_PRESSED:  # Mouse wheel up
                            notes_scroll_offset = max(0, notes_scroll_offset - 3)
                            request_full_redraw = True
                        elif bstate & curses.BUTTON5_PRESSED:  # Mouse wheel down
                            notes_scroll_offset += 3
                            request_full_redraw = True
                    except curses.error:
                        pass  # Ignore mouse errors
                elif key == '\n' or key == curses.KEY_ENTER:
                    cmd_parts = command_buffer.split()
                    if cmd_parts and cmd_parts[0].lower() == 'd' and selected_note_index != -1:
                         if 0 <= selected_note_index < notes_list_size:
                            with data_lock:
                                if current_view == VIEW_DEDICATED_NOTES:
                                    ent_type = entity_for_dedicated_notes.get("type")
                                    ent_name = entity_for_dedicated_notes.get("name")
                                    if ent_type == "task":
                                        app_data["notes"][ent_name].pop(selected_note_index)
                                    elif ent_type == "subtask":
                                        main_task = entity_for_dedicated_notes.get("main_task_name")
                                        app_data["sub_tasks"][main_task][ent_name]["notes"].pop(selected_note_index)
                                elif current_view == VIEW_DAILY_NOTES:
                                    date_iso = current_date_for_daily_notes.isoformat()
                                    app_data["daily_notes"][date_iso].pop(selected_note_index)
                                save_data(app_data)

                            new_size = notes_list_size - 1
                            if selected_note_index >= new_size and new_size > 0:
                               selected_note_index = new_size - 1
                            elif new_size <= 0:
                                selected_note_index = -1
                    elif command_buffer.strip():
                        with data_lock:
                            if current_view == VIEW_DEDICATED_NOTES and entity_for_dedicated_notes:
                                ent_type = entity_for_dedicated_notes.get("type")
                                ent_name = entity_for_dedicated_notes.get("name")
                                if ent_type == "task":
                                    app_data.setdefault("notes",{}).setdefault(ent_name,[]).append(command_buffer)
                                elif ent_type == "subtask":
                                    main_task = entity_for_dedicated_notes.get("main_task_name")
                                    sub_details = app_data.get("sub_tasks",{}).get(main_task,{}).get(ent_name)
                                    if sub_details:
                                        sub_details.setdefault("notes",[]).append(command_buffer)
                            elif current_view == VIEW_DAILY_NOTES:
                                date_iso = current_date_for_daily_notes.isoformat()
                                app_data.setdefault("daily_notes", {}).setdefault(date_iso, []).append(command_buffer)
                            save_data(app_data)
                    command_buffer = ""; request_full_redraw = True
                elif key == curses.KEY_LEFT and current_view == VIEW_DAILY_NOTES:
                    current_date_for_daily_notes -= timedelta(days=1)
                    command_buffer = ""; selected_note_index = -1; request_full_redraw = True
                elif key == curses.KEY_RIGHT and current_view == VIEW_DAILY_NOTES:
                    current_date_for_daily_notes += timedelta(days=1)
                    if current_date_for_daily_notes > date.today():
                        current_date_for_daily_notes = date.today()
                        current_view = VIEW_MAIN
                    command_buffer = ""; selected_note_index = -1; request_full_redraw = True
                
                elif key == curses.KEY_LEFT and current_view == VIEW_TIME_LOG:
                    current_date_for_daily_notes -= timedelta(days=1)
                    command_buffer = ""; selected_note_index = -1; request_full_redraw = True
                elif key == curses.KEY_RIGHT and current_view == VIEW_TIME_LOG:
                    current_date_for_daily_notes += timedelta(days=1)
                    if current_date_for_daily_notes > date.today():
                        current_date_for_daily_notes = date.today()
                    command_buffer = ""; selected_note_index = -1; request_full_redraw = True
                elif isinstance(key, str) and key.isprintable():
                    command_buffer += key
                    request_full_redraw = True
                elif key in [curses.KEY_BACKSPACE, 127, 8]:
                    command_buffer = command_buffer[:-1]
                    request_full_redraw = True
            
            elif current_view == VIEW_HOURLY_CHECKIN:
                # Handle hourly check-in inputs
                if key in ['Y', 'y']:
                    # User worked on suggested task
                    with data_lock:
                        # First, handle any currently running timer
                        work_session = app_data.get("work_session", {})
                        if work_session.get("active") and not work_session.get("paused"):
                            if work_session.get("current_timer_start_ts") and app_data.get("focused_subtask"):
                                elapsed_seconds = int(datetime.now().timestamp() - work_session["current_timer_start_ts"])
                                if elapsed_seconds > 0:
                                    from inc.time_tracker import add_time_entry
                                    focused_ticket = app_data.get("focused_ticket")
                                    focused_subtask = app_data.get("focused_subtask")
                                    if focused_ticket and focused_subtask:
                                        # Use the standardized format for time logging
                                        normalized_subtask = f"[{focused_ticket}] {focused_subtask}"
                                        add_time_entry(app_data, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
                        
                        # Now log the time from the hourly check-in
                        pending = app_data.get("pending_checkin", {})
                        if pending:
                            duration_seconds = pending.get("duration_seconds", 3600)
                            suggested_subtask = pending.get("suggested_subtask")
                            if suggested_subtask:
                                from inc.time_tracker import add_time_entry
                                add_time_entry(app_data, entry_type="task", subtask=suggested_subtask, seconds=duration_seconds)
                                save_data(app_data)
                                
                                # Restart timer for the suggested subtask if work session is active
                                if work_session.get("active") and not work_session.get("paused"):
                                    work_session["current_timer_start_ts"] = datetime.now().timestamp()
                                    work_session["last_activity_ts"] = datetime.now().timestamp()
                            
                        app_data["pending_checkin"] = None
                        save_data(app_data)
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""; request_full_redraw = True
                
                elif key in ['S', 's']:
                    # User wants to select a different task - start selection mode
                    with data_lock:
                        # First, log any currently running timer before entering selection mode
                        work_session = app_data.get("work_session", {})
                        if work_session.get("active") and not work_session.get("paused"):
                            if work_session.get("current_timer_start_ts") and app_data.get("focused_subtask"):
                                elapsed_seconds = int(datetime.now().timestamp() - work_session["current_timer_start_ts"])
                                if elapsed_seconds > 0:
                                    from inc.time_tracker import add_time_entry
                                    focused_ticket = app_data.get("focused_ticket")
                                    focused_subtask = app_data.get("focused_subtask")
                                    if focused_ticket and focused_subtask:
                                        # Use the standardized format for time logging
                                        normalized_subtask = f"[{focused_ticket}] {focused_subtask}"
                                        add_time_entry(app_data, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
                                
                                # Stop the current timer since we're switching tasks
                                work_session.pop("current_timer_start_ts", None)
                        
                        # Get available tasks to ensure we have a valid starting index
                        available_tasks = []
                        current_ticket = app_data.get("current_ticket")
                        if current_ticket:
                            subtasks = app_data.get("sub_tasks", {}).get(current_ticket, {})
                            for sub_name, sub_details in subtasks.items():
                                if isinstance(sub_details, dict) and sub_details.get("status") != "hidden":
                                    available_tasks.append((current_ticket, sub_name))
                        
                        # Add paused tasks
                        for paused_task in app_data.get("paused_tasks", []):
                            ticket_name = paused_task.get("ticket")
                            if ticket_name:
                                subtasks = paused_task.get("sub_tasks", {})
                                for sub_name, sub_details in subtasks.items():
                                    if isinstance(sub_details, dict) and sub_details.get("status") != "hidden":
                                        available_tasks.append((ticket_name, sub_name))
                    
                    if available_tasks:
                        selected_checkin_task_index = 0  # Start selection at first task
                    else:
                        selected_checkin_task_index = -1  # No tasks available
                    request_full_redraw = True
                
                elif key in ['B', 'b']:
                    # User was on break/meeting
                    with data_lock:
                        # First, log any currently running timer before logging break time
                        work_session = app_data.get("work_session", {})
                        if work_session.get("active") and not work_session.get("paused"):
                            if work_session.get("current_timer_start_ts") and app_data.get("focused_subtask"):
                                elapsed_seconds = int(datetime.now().timestamp() - work_session["current_timer_start_ts"])
                                if elapsed_seconds > 0:
                                    from inc.time_tracker import add_time_entry
                                    focused_ticket = app_data.get("focused_ticket")
                                    focused_subtask = app_data.get("focused_subtask")
                                    if focused_ticket and focused_subtask:
                                        # Use the standardized format for time logging
                                        normalized_subtask = f"[{focused_ticket}] {focused_subtask}"
                                        add_time_entry(app_data, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
                            # Stop the current timer
                            work_session.pop("current_timer_start_ts", None)
                        
                        # Now log the break time from the hourly check-in
                        pending = app_data.get("pending_checkin", {})
                        if pending:
                            duration_seconds = pending.get("duration_seconds", 3600)
                            from inc.time_tracker import add_time_entry
                            add_time_entry(app_data, entry_type="break", subtask=None, seconds=duration_seconds)
                            save_data(app_data)
                        app_data["pending_checkin"] = None
                        save_data(app_data)
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""; request_full_redraw = True
                
                elif key in ['I', 'i']:
                    # Ignore this check-in
                    with data_lock:
                        app_data["pending_checkin"] = None
                        save_data(app_data)
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""; request_full_redraw = True
                
                elif key == curses.KEY_UP and selected_checkin_task_index > 0:
                    selected_checkin_task_index -= 1
                    request_full_redraw = True
                
                elif key == curses.KEY_DOWN:
                    # Get available tasks - build consistent task list
                    available_tasks = []
                    with data_lock:
                        current_ticket = app_data.get("current_ticket")
                        if current_ticket:
                            subtasks = app_data.get("sub_tasks", {}).get(current_ticket, {})
                            for sub_name, sub_details in subtasks.items():
                                if isinstance(sub_details, dict) and sub_details.get("status") != "hidden":
                                    available_tasks.append((current_ticket, sub_name))
                        
                        # Add paused tasks
                        for paused_task in app_data.get("paused_tasks", []):
                            ticket_name = paused_task.get("ticket")
                            if ticket_name:
                                subtasks = paused_task.get("sub_tasks", {})
                                for sub_name, sub_details in subtasks.items():
                                    if isinstance(sub_details, dict) and sub_details.get("status") != "hidden":
                                        available_tasks.append((ticket_name, sub_name))
                    
                    if selected_checkin_task_index != -1 and selected_checkin_task_index < len(available_tasks) - 1:
                        selected_checkin_task_index += 1
                    request_full_redraw = True
                
                elif key in ['\n', curses.KEY_ENTER] and selected_checkin_task_index >= 0:
                    # User confirmed selection of a task
                    available_tasks = []
                    with data_lock:
                        current_ticket = app_data.get("current_ticket")
                        if current_ticket:
                            subtasks = app_data.get("sub_tasks", {}).get(current_ticket, {})
                            for sub_name, sub_details in subtasks.items():
                                if isinstance(sub_details, dict) and sub_details.get("status") != "hidden":
                                    available_tasks.append((current_ticket, sub_name))
                        
                        # Add paused tasks
                        for paused_task in app_data.get("paused_tasks", []):
                            ticket_name = paused_task.get("ticket")
                            if ticket_name:
                                subtasks = paused_task.get("sub_tasks", {})
                                for sub_name, sub_details in subtasks.items():
                                    if isinstance(sub_details, dict) and sub_details.get("status") != "hidden":
                                        available_tasks.append((ticket_name, sub_name))
                        
                        if 0 <= selected_checkin_task_index < len(available_tasks):
                            selected_ticket, selected_subtask = available_tasks[selected_checkin_task_index]
                            pending = app_data.get("pending_checkin", {})
                            if pending:
                                duration_seconds = pending.get("duration_seconds", 3600)
                                # Use the actual subtask identifier, which will be normalized by add_time_entry
                                from inc.time_tracker import add_time_entry
                                add_time_entry(app_data, entry_type="task", subtask=selected_subtask, seconds=duration_seconds)
                                save_data(app_data)
                                
                                # Start timer for the selected subtask if work session is active
                                work_session = app_data.get("work_session", {})
                                if work_session.get("active") and not work_session.get("paused"):
                                    work_session["current_timer_start_ts"] = datetime.now().timestamp()
                                    work_session["last_activity_ts"] = datetime.now().timestamp()
                                    
                                    # Update focus to the selected subtask
                                    app_data["focused_ticket"] = selected_ticket
                                    app_data["focused_subtask"] = selected_subtask
                                    
                            app_data["pending_checkin"] = None
                            save_data(app_data)
                    
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""; request_full_redraw = True

            # Redraw the UI after every valid keypress.
            request_full_redraw = True
            last_content_refresh_time = 0
            display_ui(stdscr, app_data, command_buffer, request_full_redraw, selected_subtask_index, current_view, entity_for_dedicated_notes, current_ticket_subtask_list_visible, show_help_footer, current_date_for_daily_notes, selected_note_index, jira_cache, jira_cache_lock, notes_scroll_offset, selected_checkin_task_index)
            if request_full_redraw : request_full_redraw = False

        if not user_activity_caused_draw_this_cycle:
            if current_time - last_content_refresh_time >= content_refresh_interval:
                request_full_redraw = True

            if request_full_redraw or (current_time - last_clock_refresh_time >= clock_refresh_interval):
                display_ui(stdscr, app_data, command_buffer, request_full_redraw, selected_subtask_index, current_view, entity_for_dedicated_notes, current_ticket_subtask_list_visible, show_help_footer, current_date_for_daily_notes, selected_note_index, jira_cache, jira_cache_lock, notes_scroll_offset, selected_checkin_task_index)
                last_clock_refresh_time = current_time
                if request_full_redraw:
                    last_content_refresh_time = current_time
                    request_full_redraw = False
        time.sleep(0.05)

    return result

if __name__ == "__main__":
    if not inc.config_manager.load_config():
        print("INFO: New 'config.json' created. Please edit it with your details and restart the application.")
        sys.exit()
    inc.config_manager.load_translations()
    result = "EXIT"

  

    while True:

        try:
            result = curses.wrapper(main)

        except curses.error as e:
            print(t('error_curses', e=e), file=sys.stderr)
            try:
                curses.nocbreak(); curses.echo(); curses.endwin()
            except: pass
        except Exception as e:
            import traceback
            print(t('error_unexpected', e=e), file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        finally:
            try:
                if 'stdscr' in locals() and 'curses' in sys.modules and not sys.modules['curses'].isendwin():
                    curses.nocbreak()
                    if hasattr(stdscr, 'keypad'): stdscr.keypad(False)
                    curses.echo()
                    curses.endwin()
                elif 'curses' in sys.modules and not sys.modules['curses'].isendwin():
                     curses.nocbreak(); curses.echo(); curses.endwin()
            except Exception as e_cleanup:
                print(t('error_terminal_restore', e_cleanup=e_cleanup), file=sys.stderr)

        if result == "RESTART_FOR_LOGIN":
            get_and_save_web_session(
                service_name="Jira",
                login_url=inc.config_manager.config.get("JIRA_URL"),
                session_file=inc.config_manager.config.get("JIRA_SESSION_FILE"),
                driver_path=inc.config_manager.config.get("CHROME_DRIVER_PATH"),
                permanent_notifications_ref=permanent_notifications
            )

            get_and_save_web_session(
                service_name="Trello",
                login_url=f"{inc.config_manager.config.get('TRELLO_URL')}/login",
                session_file=inc.config_manager.config.get("TRELLO_SESSION_FILE"),
                driver_path=inc.config_manager.config.get("CHROME_DRIVER_PATH"),
                permanent_notifications_ref=permanent_notifications
            )

            print("\nLogin process finished. Restarting application in 3 seconds...")
            time.sleep(3)
            continue
        elif result == None:
            continue
        else:
            break

    #logging.info("Stopping Jira poller thread.")
    #stop_event.set()
    #jira_thread.join()  # Wait for the thread to finish
    #logging.info("Jira poller thread stopped.")
    print(f"\n{t('app_closed')}")