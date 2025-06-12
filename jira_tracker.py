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

# --- Global Dictionaries ---
STRINGS = {} # Global dictionary to hold loaded strings.
sent_notifications = set() # Global set to track sent notifications to avoid duplicates
pull_requests_for_review = []
reviews_lock = threading.Lock()
sent_review_notifications = set()

# --- Translation & Config Functions ---
def load_config():
    """Loads config.json, creating a default one if it doesn't exist."""
    config_path = os.path.join(SCRIPT_DIR, "config.json")
    default_config = {
        "API_TOKEN": "PASTE_YOUR_BEARER_TOKEN_HERE",
        "STASH_URL": "http://your-stash-instance.com:7990",
        "STASH_REVIEW_URL": "http://your-stash-instance.com:7990/rest/api/latest/dashboard/pull-requests?state=OPEN&role=REVIEWER",
        "USER_ID": 3006,
        "LANGUAGE": "fi",
        "NOTIFICATION_WINDOW_TITLE": "TODAYTASKS",
        "BROWSER_COMMAND": ["/usr/bin/google-chrome", "--profile-directory=Profile 1", "--new-window"]
    }
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            # Ensure all keys are present
            for key, value in default_config.items():
                config.setdefault(key, value)
            return config
    except (FileNotFoundError, json.JSONDecodeError):
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4)
        print(f"INFO: Configuration file 'config.json' was not found or was invalid.\n"
              f"A default one has been created. Please edit it with your details.", file=sys.stderr)
        return default_config

def load_translations(lang_code="fi"):
    """Loads the language JSON file into the global STRINGS dictionary."""
    global STRINGS
    lang_dir = os.path.join(SCRIPT_DIR, "lang")
    if not os.path.exists(lang_dir):
        os.makedirs(lang_dir)

    path = os.path.join(lang_dir, f"{lang_code}.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            STRINGS = json.load(f)
    except FileNotFoundError:
        print(f"Warning: Language file '{path}' not found. Falling back to 'fi'.", file=sys.stderr)
        if lang_code != "fi":
            load_translations("fi")

def t(key, **kwargs):
    """Gets a translated string by its key."""
    template = STRINGS.get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, TypeError):
            return f"FORMAT_ERROR_FOR_KEY: {key}"
    return template

# -- Setup Locale --
try:
    locale.setlocale(locale.LC_ALL, '')
except locale.Error as e:
    print(f"Warning: Could not set locale ({e}). Non-ASCII characters may not work correctly.", file=sys.stderr)

# -- Constants and Globals --
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, "jira_data.json")
JIRA_BOX_FILE = os.path.join(SCRIPT_DIR, "jira_box.txt")

# -- Color Pairs --
COLOR_PAIR_DEFAULT = 1
COLOR_PAIR_REVERSE = 2
COLOR_PAIR_GREY = 3
COLOR_PAIR_PAUSED = 4
COLOR_PAIR_SELECTED = 5
COLOR_PAIR_TASK_ALL_SUBTASKS_DONE = 6
COLOR_PAIR_URGENT_BOX = 7
COLOR_PAIR_PR_UNHANDLED = 8
COLOR_PAIR_PR_APPROVED = 9
COLOR_PAIR_FOCUSED = 10

# -- Views --
VIEW_MAIN = "main"
VIEW_DEDICATED_NOTES = "dedicated_notes"
VIEW_DAILY_NOTES = "daily_notes"

WEEKDAY_MAP = {
    'ma': 0, 'mo': 0, 'ti': 1, 'tu': 1, 'ke': 2, 'we': 2,
    'to': 3, 'th': 3, 'pe': 4, 'fr': 4, 'la': 5, 'sa': 5,
    'su': 6, 'su': 6
}


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
    data.setdefault("interruptions", [])
    data.setdefault("notes", {})
    data.setdefault("paused_tasks", [])
    data.setdefault("recurring_events", [])
    data.setdefault("daily_notes", {})

    # Data migration and cleanup logic
    for ticket_name, sub_tasks_for_ticket in data.get("sub_tasks", {}).items():
        if isinstance(sub_tasks_for_ticket, dict):
            for sub_task_name, sub_task_details in list(sub_tasks_for_ticket.items()):
                if not isinstance(sub_task_details, dict):
                    current_done_status = sub_task_details
                    sub_tasks_for_ticket[sub_task_name] = {"done": current_done_status, "notes": [], "hidden": False, "pr_url": None, "pr_status": None, "focused": False}
                else:
                    sub_task_details.setdefault("done", False)
                    sub_task_details.setdefault("notes", [])
                    sub_task_details.setdefault("hidden", False)
                    sub_task_details.setdefault("pr_url", None)
                    sub_task_details.setdefault("pr_status", None)
                    sub_task_details.setdefault("focused", False)
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


def display_dedicated_notes_view(stdscr, data, command_buffer, entity_for_notes, show_help_footer, selected_note_idx):
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
        t('dedicated_notes_help_back')
    ]
    num_help_lines_notes_view = len(help_lines_notes_view)
    reserved_rows_notes_footer = num_help_lines_notes_view + 2

    content_height_val = height - (row + reserved_rows_notes_footer)
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

    if not notes_list_to_display and entity_for_notes:
        if content_height_obj[0] > 0:
            stdscr.addstr(row, 0, t('dedicated_notes_no_notes'))
            row+=1; content_height_obj[0]-=1

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
    return True


def display_ui(stdscr, data, command_buffer="", full_redraw=False, selected_subtask_idx=-1,
               current_view_mode=VIEW_MAIN, entity_for_dedicated_notes=None,
               current_ticket_subtask_list_for_display_arg=None, show_help_footer=True,
               current_date_for_daily_notes_arg=None, selected_note_idx=-1):

    global pull_requests_for_review

    if current_view_mode == VIEW_DEDICATED_NOTES:
        return display_dedicated_notes_view(stdscr, data, command_buffer, entity_for_dedicated_notes, show_help_footer, selected_note_idx)
    if current_view_mode == VIEW_DAILY_NOTES:
        return display_daily_notes_view(stdscr, data, command_buffer, current_date_for_daily_notes_arg, show_help_footer, selected_note_idx)

    try:
        height, width = stdscr.getmaxyx()
    except curses.error: return False
    now_time_str = datetime.now().strftime("%H:%M:%S")
    now_dt = datetime.now()
    today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    if height <= 0 or width <= 0: return False

    completed_tickets = data.get("completed_tickets", [])
    all_tickets_set = set()
    if data.get("current_ticket"): all_tickets_set.add(data.get("current_ticket"))
    all_tickets_set.update(data.get("sub_tasks", {}).keys())
    all_tickets_set.update(data.get("notes", {}).keys())
    for paused_item in data.get("paused_tasks", []):
        if paused_item.get("ticket"): all_tickets_set.add(paused_item["ticket"])
    
    all_displayable_tickets = sorted([t for t in list(filter(None, all_tickets_set)) if t not in completed_tickets])


    display_right_panel = bool(all_displayable_tickets)
    separator_char = "|"
    effective_main_width = width
    min_main_content_width = 35
    min_panel_item_len = 8
    actual_panel_content_width = 0

    info_box_content = []
    with reviews_lock:
        if pull_requests_for_review:
            info_box_content.append(t('ui_reviews_header'))
            for pr in pull_requests_for_review:
                repo_name = f"{pr['toRef']['repository']['project']['key']}/{pr['toRef']['repository']['name']}"
                info_box_content.append(f" {repo_name} #{pr['id']}")
                info_box_content.append(f"  {pr['title']}")
            info_box_content.append("---")
    
    info_box_content.extend(read_jira_box_content(max_lines=10))
    display_info_box_area = bool(info_box_content) and display_right_panel

    if display_right_panel:
        max_len_of_panel_item_str = 0
        if all_displayable_tickets:
            for idx, ticket_name_in_panel in enumerate(all_displayable_tickets):
                if idx < height -1:
                    max_len_of_panel_item_str = max(max_len_of_panel_item_str, len(f"{idx+1}. {ticket_name_in_panel}"))

        actual_panel_content_width = max(max_len_of_panel_item_str, min_panel_item_len)
        if width - (actual_panel_content_width + len(separator_char)) >= min_main_content_width:
            effective_main_width = width - (actual_panel_content_width + len(separator_char))
        else:
            effective_main_width = min_main_content_width
            actual_panel_content_width = width - effective_main_width - len(separator_char)
            if actual_panel_content_width < min_panel_item_len / 2 :
                display_right_panel = False
                effective_main_width = width
                actual_panel_content_width = 0

    if effective_main_width < 0 : effective_main_width = 0
    if effective_main_width > width : effective_main_width = width
    if not display_right_panel: effective_main_width = width; actual_panel_content_width = 0

    max_cmd_len = width -1
    max_buffer_len = max_cmd_len - len("> ")
    if max_buffer_len < 0: max_buffer_len = 0
    display_buffer = command_buffer[:max_buffer_len]
    command_line_text = "> " + display_buffer
    cursor_x = len(command_line_text)

    help_lines_definitions = {
        "full": [
            t('help_header'), t('help_switch_task'), t('help_new_task'), t('help_add_subtask'),
            t('help_hide_subtask'), t('help_add_pr'), t('help_done_subtask'), t('help_done_task'),
            t('help_add_meeting'), t('help_add_event'), t('help_add_note'), t('help_set_focus'), t('help_set_subtask_focus'), t('help_toggle_help'),
            t('help_daily_notes'), t('help_notes_view'), t('help_quit')
        ],
        "hidden": [t('help_hidden_prompt')]
    }
    current_help_lines_list = help_lines_definitions["full"] if show_help_footer else help_lines_definitions["hidden"]
    num_actual_help_lines = len(current_help_lines_list)
    footer_total_height = num_actual_help_lines + 2

    if not full_redraw:
        try:
            if width > 0: stdscr.addstr(0, 0, " " * width)
            stdscr.addstr(0, 0, t('ui_clock', now_time_str=now_time_str), curses.color_pair(COLOR_PAIR_DEFAULT))
            if display_right_panel and all_displayable_tickets:
                if 0 < effective_main_width < width:
                    try: stdscr.addstr(0, effective_main_width, separator_char)
                    except curses.error: pass
                if all_displayable_tickets:
                    ticket_name_line0 = all_displayable_tickets[0]
                    attr_line0 = curses.color_pair(COLOR_PAIR_DEFAULT)
                    if data.get("current_ticket") == ticket_name_line0:
                        attr_line0 = curses.color_pair(COLOR_PAIR_SELECTED) | curses.A_BOLD
                    elif data.get("focused_ticket") == ticket_name_line0:
                        attr_line0 = curses.color_pair(COLOR_PAIR_FOCUSED)
                    else:
                        subtasks_for_ticket0 = data.get("sub_tasks", {}).get(ticket_name_line0, {})
                        if any(st.get("pr_status") == 'attention_needed' for st in subtasks_for_ticket0.values() if isinstance(st, dict)):
                            attr_line0 = curses.color_pair(COLOR_PAIR_PR_UNHANDLED)
                        elif any(st.get("pr_status") == 'approved' for st in subtasks_for_ticket0.values() if isinstance(st, dict)):
                            attr_line0 = curses.color_pair(COLOR_PAIR_PR_APPROVED)
                        elif subtasks_for_ticket0 and all(st_details.get("done", False) for st_details in subtasks_for_ticket0.values() if isinstance(st_details, dict)):
                            attr_line0 = curses.color_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_DONE)

                    full_text_line0 = f"1. {ticket_name_line0}"
                    panel_text_start_x_calc = effective_main_width + len(separator_char)
                    available_width_in_panel_line0 = max(0, width - panel_text_start_x_calc)
                    text_to_draw_line0 = full_text_line0[:available_width_in_panel_line0]
                    actual_draw_x_line0 = width - len(text_to_draw_line0)
                    if actual_draw_x_line0 < panel_text_start_x_calc:
                        actual_draw_x_line0 = panel_text_start_x_calc
                        text_to_draw_line0 = text_to_draw_line0[:max(0,width - actual_draw_x_line0)]
                    if len(text_to_draw_line0) > 0:
                        try: stdscr.addstr(0, actual_draw_x_line0, text_to_draw_line0, attr_line0)
                        except curses.error: pass
            stdscr.addstr(height - 1, 0, " " * (width -1 if width > 0 else 0) )
            stdscr.addstr(height - 1, 0, command_line_text.ljust(width-1 if width > 0 else 0), curses.color_pair(COLOR_PAIR_DEFAULT) | curses.A_BOLD)
            curses.curs_set(1)
            stdscr.move(height - 1, min(cursor_x, width - 1 if width > 0 else 0))
            stdscr.refresh()
        except curses.error: return False
        return True

    stdscr.clear()
    stdscr.attron(curses.color_pair(COLOR_PAIR_DEFAULT))

    if display_right_panel:
        panel_text_start_col_abs = effective_main_width + len(separator_char)
        max_rows_for_ticket_list_in_panel = height -1

        if display_info_box_area:
             max_rows_for_ticket_list_in_panel -= (len(info_box_content))


        for i, ticket_name_in_panel in enumerate(all_displayable_tickets):
            if i >= max_rows_for_ticket_list_in_panel : break
            if i >= height -1 : break

            if 0 < effective_main_width < width:
                try: stdscr.addstr(i, effective_main_width, separator_char)
                except curses.error: pass

            item_attr = curses.color_pair(COLOR_PAIR_DEFAULT)
            if data.get("current_ticket") == ticket_name_in_panel:
                item_attr = curses.color_pair(COLOR_PAIR_SELECTED) | curses.A_BOLD
            elif data.get("focused_ticket") == ticket_name_in_panel:
                item_attr = curses.color_pair(COLOR_PAIR_FOCUSED)
            else:
                subtasks_for_this_panel_ticket = data.get("sub_tasks", {}).get(ticket_name_in_panel, {})
                # Check for PR status for background color
                if any(st.get("pr_status") == 'attention_needed' for st in subtasks_for_this_panel_ticket.values() if isinstance(st, dict)):
                    item_attr = curses.color_pair(COLOR_PAIR_PR_UNHANDLED)
                elif any(st.get("pr_status") == 'approved' for st in subtasks_for_this_panel_ticket.values() if isinstance(st, dict)):
                    item_attr = curses.color_pair(COLOR_PAIR_PR_APPROVED)
                elif subtasks_for_this_panel_ticket and all(st_details.get("done", False) for st_details in subtasks_for_this_panel_ticket.values() if isinstance(st_details, dict)):
                    item_attr = curses.color_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_DONE)


            full_text_for_line = f"{i+1}. {ticket_name_in_panel}"
            current_panel_content_width = actual_panel_content_width if actual_panel_content_width > 0 else 1
            text_to_draw = full_text_for_line[:current_panel_content_width]
            actual_draw_x = width - len(text_to_draw)
            if actual_draw_x < panel_text_start_col_abs:
                actual_draw_x = panel_text_start_col_abs
                text_to_draw = text_to_draw[:max(0,width - actual_draw_x)]

            if len(text_to_draw) > 0:
                try: stdscr.addstr(i, actual_draw_x, text_to_draw, item_attr)
                except curses.error: pass

        if display_info_box_area:
            info_box_start_y = max(max_rows_for_ticket_list_in_panel, i + 1 if 'i' in locals() else 0)

            for line_idx, line_content in enumerate(info_box_content):
                current_draw_y = info_box_start_y + line_idx
                if current_draw_y >= height -1 : break

                current_panel_actual_width_for_box = actual_panel_content_width if actual_panel_content_width > 0 else 1
                text_to_draw_box = line_content[:current_panel_actual_width_for_box] # Trimmed here
                if current_panel_actual_width_for_box > 0:
                    try:
                        stdscr.addstr(current_draw_y, panel_text_start_col_abs, text_to_draw_box, curses.color_pair(COLOR_PAIR_URGENT_BOX))
                    except curses.error: pass

    row = 0
    if effective_main_width > 0 :
        stdscr.addstr(row, 0, t('ui_clock', now_time_str=now_time_str)[:effective_main_width])
    row += 1

    focused_ticket = data.get("focused_ticket")
    focused_subtask = data.get("focused_subtask")
    if focused_ticket:
        if effective_main_width > 0:
            focus_text = t('ui_focused_task_prefix', name=focused_ticket)
            if focused_subtask:
                focus_text += f" / {focused_subtask}"
            stdscr.addstr(row, 0, focus_text[:effective_main_width], curses.color_pair(COLOR_PAIR_FOCUSED) | curses.A_BOLD)
        row +=1

    initial_content_start_row = row
    if effective_main_width > 0:
        stdscr.addstr(row, 0, "-" * effective_main_width)
        initial_content_start_row +=1
    row = initial_content_start_row

    content_height_val = height - initial_content_start_row - footer_total_height
    if content_height_val < 0: content_height_val = 0
    content_height_obj = [content_height_val]

    current_ticket = data.get("current_ticket")

    if current_ticket:
        paused_count = len(data.get('paused_tasks', []))
        paused_info = f" {t('ui_paused_tasks', count=paused_count)}" if paused_count > 0 else ""
        base_text = t('ui_current_task_prefix')
        if content_height_obj[0] > 0 and effective_main_width > 0:
            available_width_for_ticket_name = effective_main_width - len(base_text) - len(paused_info) -1
            if available_width_for_ticket_name < 0: available_width_for_ticket_name = 0
            ticket_display_name = current_ticket[:available_width_for_ticket_name]
            full_ticket_line = f"{base_text}{ticket_display_name}{paused_info}"
            stdscr.addstr(row, 0, full_ticket_line[:effective_main_width])
            row += 1; content_height_obj[0] -= 1

        subtask_list_to_use = current_ticket_subtask_list_for_display_arg
        if subtask_list_to_use is None:
            subtasks_dict = data.get("sub_tasks", {}).get(current_ticket, {})
            # Filter out hidden subtasks for display
            subtask_list_to_use = [(name, details) for name, details in subtasks_dict.items() if isinstance(details, dict) and not details.get("hidden", False)]


        if subtask_list_to_use:
            if content_height_obj[0] > 0 and effective_main_width > 2:
                stdscr.addstr(row, 2, t('ui_subtasks_header')[:effective_main_width-2])
                row += 1; content_height_obj[0] -= 1

            for i, (sub_task_name, sub_task_details_obj) in enumerate(subtask_list_to_use):
                if content_height_obj[0] <= 0: break
                if effective_main_width <= 4: break
                is_done = sub_task_details_obj.get("done", False)
                is_focused = sub_task_details_obj.get("focused", False)

                if is_focused:
                    status_char = "‼️"
                elif is_done:
                    status_char = "✅"
                else:
                    status_char = "[ ]"

                prefix = ""
                item_attr = curses.color_pair(COLOR_PAIR_DEFAULT)

                pr_status = sub_task_details_obj.get("pr_status")
                if pr_status == 'attention_needed':
                    item_attr = curses.color_pair(COLOR_PAIR_PR_UNHANDLED)
                elif pr_status == 'approved':
                    item_attr = curses.color_pair(COLOR_PAIR_PR_APPROVED)

                if i == selected_subtask_idx:
                    prefix = "> "
                    item_attr = curses.color_pair(COLOR_PAIR_SELECTED)

                full_prefix = f"{prefix}{i+1}. {status_char} "
                start_col = 2
                max_text_width_for_line = effective_main_width - start_col - len(full_prefix)
                if max_text_width_for_line < 0 : max_text_width_for_line = 0
                lines_used = _draw_wrapped_text(stdscr, sub_task_name, row, start_col,
                                                max_text_width_for_line, effective_main_width, content_height_obj,
                                                prefix=full_prefix,
                                                subsequent_indent_offset=len(prefix) + len(f"{i+1}. {status_char} "),
                                                attr=item_attr)
                row += lines_used
        elif content_height_obj[0] > 0 and effective_main_width > 2 and current_ticket:
            stdscr.addstr(row, 2, t('ui_no_subtasks')[:effective_main_width-2])
            row += 1; content_height_obj[0] -= 1

        notes_to_show_preview = []
        notes_title_preview = ""
        if selected_subtask_idx != -1 and 0 <= selected_subtask_idx < len(subtask_list_to_use):
            sel_sub_name, sel_sub_details = subtask_list_to_use[selected_subtask_idx]
            notes_title_preview = t('ui_subtask_notes_header', subtask=sel_sub_name)
            notes_to_show_preview = sel_sub_details.get("notes", []).copy()
            if sel_sub_details.get("pr_url"):
                notes_to_show_preview.insert(0, f"PR: {sel_sub_details.get('pr_url')}")
        elif current_ticket:
            notes_title_preview = t('ui_main_task_notes_header', task=current_ticket)
            notes_to_show_preview = data.get("notes", {}).get(current_ticket, [])

        if notes_title_preview and content_height_obj[0] > 0 and effective_main_width > 2:
            stdscr.addstr(row, 2, notes_title_preview[:effective_main_width-2])
            row += 1; content_height_obj[0] -= 1
            if not notes_to_show_preview and content_height_obj[0] > 0 :
                stdscr.addstr(row, 4, t('ui_no_notes')[:effective_main_width-4])
                row += 1; content_height_obj[0] -=1

        for note_idx, note in enumerate(notes_to_show_preview[:10]):
            if content_height_obj[0] <= 0 : break
            if effective_main_width <= 4: break
            prefix_note = f"- "
            start_col_note = 4
            max_text_width_note = effective_main_width - start_col_note - len(prefix_note)
            if max_text_width_note < 0 : max_text_width_note = 0
            lines_used_note = _draw_wrapped_text(stdscr, note, row, start_col_note,
                                            max_text_width_note, effective_main_width, content_height_obj,
                                            prefix=prefix_note, subsequent_indent_offset=len(prefix_note))
            row += lines_used_note
        if len(notes_to_show_preview) > 10 and content_height_obj[0] > 0 and effective_main_width > 7:
            stdscr.addstr(row, 4, t('ui_more_notes')[:effective_main_width-4])
            row+=1; content_height_obj[0]-=1

    else:
        paused_count = len(data.get('paused_tasks', []))
        if paused_count > 0:
            full_no_task_line = t('ui_no_active_task_paused', count=paused_count)
        else:
            full_no_task_line = t('ui_no_active_task')

        if content_height_obj[0] > 0 and effective_main_width > 0:
            stdscr.addstr(row, 0, full_no_task_line[:effective_main_width],
                          curses.color_pair(COLOR_PAIR_PAUSED) if paused_count > 0 else curses.color_pair(COLOR_PAIR_DEFAULT) )
            row += 1; content_height_obj[0] -= 1

    if content_height_obj[0] > 0 and effective_main_width > 0: row += 1; content_height_obj[0] -= 1

    def _is_valid_past_event_today(event_item, now_for_display, today_start_dt):
        try:
            dt_str = event_item.get('datetime');
            if not isinstance(dt_str, str): return False
            dt = datetime.fromisoformat(dt_str)
            return today_start_dt <= dt < now_for_display
        except (ValueError, TypeError): return False

    def get_next_occurrence(recurring_event, now):
        try:
            target_weekday = recurring_event['weekday']
            event_time_str = recurring_event['time']
            if len(event_time_str.split(':')) != 2: return None
            event_time_obj = datetime.strptime(event_time_str, "%H:%M").time()
            today_weekday = now.weekday()
            days_ahead = target_weekday - today_weekday
            if days_ahead < 0: days_ahead += 7
            elif days_ahead == 0 and now.time() >= event_time_obj: days_ahead += 7
            next_occurrence_date = (now + timedelta(days=days_ahead)).date()
            return datetime.combine(next_occurrence_date, event_time_obj)
        except (ValueError, KeyError, TypeError): return None

    todays_upcoming_events = []
    now_dt_display = datetime.now()

    for m in data.get("meetings", []):
        try:
            dt = datetime.fromisoformat(m['datetime'])
            if dt.date() == now_dt_display.date() and dt >= now_dt_display:
                todays_upcoming_events.append({'dt': dt, 'details': m.get('link', ''), 'type': 'meeting', 'recurring': False})
        except (TypeError, ValueError): continue
    for i_event_data in data.get("interruptions", []):
        try:
            dt = datetime.fromisoformat(i_event_data['datetime'])
            if dt.date() == now_dt_display.date() and dt >= now_dt_display:
                todays_upcoming_events.append({'dt': dt, 'details': i_event_data.get('message', ''), 'type': 'interruption', 'recurring': False})
        except (TypeError, ValueError): continue
    for rev in data.get("recurring_events", []):
        next_dt = get_next_occurrence(rev, now_dt_display)
        if next_dt and next_dt.date() == now_dt_display.date() and next_dt >= now_dt_display:
            todays_upcoming_events.append({'dt': next_dt, 'details': rev.get('details', ''), 'type': rev.get('type'), 'recurring': True})
    todays_upcoming_events.sort(key=lambda x: x['dt'])

    if content_height_obj[0] > 0 and effective_main_width > 0:
        stdscr.addstr(row, 0, t('ui_meetings_header')[:effective_main_width])
        row += 1; content_height_obj[0] -= 1
        meetings_shown_count = 0
        for event in todays_upcoming_events:
            if event.get('type') == 'meeting':
                if content_height_obj[0] <= 0: break
                link_details = event['details']
                link_display = link_details
                try:
                    parsed_url = urlparse(link_details)
                    if parsed_url.scheme and parsed_url.netloc and parsed_url.query:
                        link_display = urlunparse(parsed_url._replace(query=''))
                except ValueError: pass

                text_content = f"{event['dt'].strftime('%H:%M')}: {link_display} ({format_timedelta_minutes(event['dt'] - now_dt)})"
                if event['recurring']: text_content += f" ({t('recurring')})"
                lines_used = _draw_wrapped_text(stdscr, text_content, row, 2, effective_main_width-2, effective_main_width, content_height_obj, prefix="- ")
                row += lines_used; meetings_shown_count +=1

        past_meetings_today = sorted([m for m in data.get("meetings", []) if _is_valid_past_event_today(m, now_dt_display, today_start)], key=lambda x: datetime.fromisoformat(x['datetime']))
        if past_meetings_today and content_height_obj[0] > 0:
            stdscr.addstr(row, 2, t('ui_meetings_past')[:effective_main_width-2], curses.color_pair(COLOR_PAIR_GREY))
            row += 1; content_height_obj[0] -=1
            for m_past in past_meetings_today:
                if content_height_obj[0] <= 0: break
                text_content = f"{datetime.fromisoformat(m_past['datetime']).strftime('%H:%M')}: {m_past.get('link','')} ({format_timedelta_minutes(now_dt - datetime.fromisoformat(m_past['datetime']))})"
                lines_used = _draw_wrapped_text(stdscr, text_content, row, 4, effective_main_width-4, effective_main_width, content_height_obj, prefix="- ", attr=curses.color_pair(COLOR_PAIR_GREY))
                row += lines_used; meetings_shown_count +=1
        if meetings_shown_count == 0 and content_height_obj[0] > 0:
             stdscr.addstr(row, 2, t('ui_no_meetings')[:effective_main_width-2]); row += 1

    if content_height_obj[0] > 0: row += 1; content_height_obj[0] -=1

    if content_height_obj[0] > 0 and effective_main_width > 0:
        stdscr.addstr(row, 0, t('ui_other_events_header')[:effective_main_width])
        row += 1; content_height_obj[0] -= 1
        interruptions_shown_count = 0
        for event in todays_upcoming_events:
            if event.get('type') == 'interruption':
                 if content_height_obj[0] <= 0: break
                 text_content = f"{event['dt'].strftime('%H:%M')}: {event['details']} ({format_timedelta_minutes(event['dt'] - now_dt)})"
                 if event['recurring']: text_content += f" ({t('recurring')})"
                 lines_used = _draw_wrapped_text(stdscr, text_content, row, 2, effective_main_width-2, effective_main_width, content_height_obj, prefix="- ")
                 row += lines_used; interruptions_shown_count +=1

        past_interruptions_today = sorted([i for i in data.get("interruptions", []) if _is_valid_past_event_today(i, now_dt, today_start)], key=lambda x: datetime.fromisoformat(x['datetime']))
        if past_interruptions_today and content_height_obj[0] > 0:
            stdscr.addstr(row, 2, t('ui_meetings_past')[:effective_main_width-2], curses.color_pair(COLOR_PAIR_GREY))
            row += 1; content_height_obj[0] -=1
            for i_past in past_interruptions_today:
                if content_height_obj[0] <= 0: break
                text_content = f"{datetime.fromisoformat(i_past['datetime']).strftime('%H:%M')}: {i_past.get('message','')} ({format_timedelta_minutes(now_dt - datetime.fromisoformat(i_past['datetime']))})"
                lines_used = _draw_wrapped_text(stdscr, text_content, row, 4, effective_main_width-4, effective_main_width, content_height_obj, prefix="- ", attr=curses.color_pair(COLOR_PAIR_GREY))
                row += lines_used; interruptions_shown_count +=1
        if interruptions_shown_count == 0 and content_height_obj[0] > 0:
             stdscr.addstr(row, 2, t('ui_no_other_events')[:effective_main_width-2]); row += 1

    help_section_start_y = height - 1 - 1 - num_actual_help_lines
    max_desc_width_footer = effective_main_width
    if help_section_start_y >= row and effective_main_width > 0 :
        stdscr.attron(curses.color_pair(COLOR_PAIR_DEFAULT))
        for i, line_text in enumerate(current_help_lines_list):
            current_draw_y = help_section_start_y + i
            if current_draw_y < height - 2:
                indent = 2 if show_help_footer and i > 0 and not line_text.strip() == t('help_header') else 0
                if line_text.strip() == t('help_header'): indent = 0
                try:
                    stdscr.addstr(current_draw_y, indent, line_text[:max(0, max_desc_width_footer - indent)])
                except curses.error: pass
            else: break
        stdscr.attroff(curses.color_pair(COLOR_PAIR_DEFAULT))

    try:
        stdscr.addstr(height - 1, 0, " " * (width-1 if width > 0 else 0) )
        stdscr.addstr(height - 1, 0, command_line_text.ljust(width-1 if width > 0 else 0), curses.color_pair(COLOR_PAIR_DEFAULT) | curses.A_BOLD)
        curses.curs_set(1)
        stdscr.move(height - 1, min(cursor_x, width - 1 if width > 0 else 0))
    except curses.error: pass

    try:
        stdscr.attroff(curses.A_BOLD)
        for i in range(1, 11):
            stdscr.attroff(curses.color_pair(i))
    except curses.error: pass
    stdscr.refresh()
    return True

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
        curses.napms(1500)
        stdscr.addstr(notification_line, 0, " " * (width-1 if width > 0 else 0))
        stdscr.refresh()
    except curses.error: pass
    except Exception: pass


def handle_input(data, command_parts, stdscr, current_view_mode, selected_subtask_idx, selected_note_idx, current_ticket_subtask_list, all_displayable_tickets_for_cmd):
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

    elif command == 'd':
        if selected_subtask_idx != -1 and 0 <= selected_subtask_idx < len(current_ticket_subtask_list):
            sub_task_to_hide_name, sub_task_details = current_ticket_subtask_list[selected_subtask_idx]
            if current_ticket_name_val in data.get("sub_tasks", {}) and \
               sub_task_to_hide_name in data["sub_tasks"][current_ticket_name_val]:
                data["sub_tasks"][current_ticket_name_val][sub_task_to_hide_name]["hidden"] = True
                if sub_task_details.get("focused"):
                    data["sub_tasks"][current_ticket_name_val][sub_task_to_hide_name]["focused"] = False
                    data["focused_subtask"] = None # Clear global focus if this was the one
                data_was_modified = True
                show_notification(stdscr, t('cmd_info_subtask_hidden', name=sub_task_to_hide_name))
            else:
                show_notification(stdscr, t('cmd_err_subtask_not_found'))
        else:
            show_notification(stdscr, t('cmd_prompt_select_subtask_to_hide'))
        return data if data_was_modified else "NO_CHANGE"


    elif command == 'a':
        if current_ticket_name_val and len(command_parts) > 1:
            sub_task_name_cmd = " ".join(command_parts[1:])
            current_ticket_subtasks = data.setdefault("sub_tasks", {}).setdefault(current_ticket_name_val, {})
            if sub_task_name_cmd not in current_ticket_subtasks:
                current_ticket_subtasks[sub_task_name_cmd] = {"done": False, "notes": [], "hidden": False, "pr_url": None, "pr_status": None, "focused": False}
                data_was_modified = True
            else:
                show_notification(stdscr, t('cmd_err_subtask_exists', name=sub_task_name_cmd))
        elif not current_ticket_name_val: show_notification(stdscr, t('cmd_err_no_active_task_for_subtask'))
        else: show_notification(stdscr, t('cmd_usage_add_subtask'))

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
            is_currently_focused = sub_task_details.get("focused", False)

            # Unfocus all other subtasks in the current ticket
            for st_name, st_details in data["sub_tasks"][current_ticket_name_val].items():
                st_details["focused"] = False
            
            # Toggle focus for the selected subtask
            data["sub_tasks"][current_ticket_name_val][sub_task_name]["focused"] = not is_currently_focused
            
            if not is_currently_focused: # If it's now focused
                data["focused_ticket"] = current_ticket_name_val
                data["focused_subtask"] = sub_task_name
                show_notification(stdscr, t('cmd_info_subtask_focus_set', name=sub_task_name))
            else: # If it's now unfocused
                data["focused_ticket"] = None
                data["focused_subtask"] = None
                show_notification(stdscr, t('cmd_info_focus_cleared'))

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
            for ticket_name, subtasks in data.get("sub_tasks", {}).items():
                if ticket_name in completed_tickets: continue
                for st_name, st_details in subtasks.items():
                    if identifier.lower() in st_name.lower():
                        found_subtasks.append((ticket_name, st_name))
            
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
                # Clear all previous focuses
                data["focused_ticket"] = None
                data["focused_subtask"] = None
                for ticket_subtasks in data["sub_tasks"].values():
                    for st in ticket_subtasks.values():
                        st["focused"] = False
                
                # Set new focus
                data["focused_ticket"] = target_ticket
                if target_subtask:
                    data["sub_tasks"][target_ticket][target_subtask]["focused"] = True
                    data["focused_subtask"] = target_subtask
                
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
                                migrated_resumed_sub_tasks[sub_name] = {"done": bool(sub_details), "notes": [], "hidden": False, "pr_url": None, "pr_status": None, "focused": False}
                            else:
                                sub_details.setdefault("done", False); sub_details.setdefault("notes", []); sub_details.setdefault("hidden", False); sub_details.setdefault("pr_url", None); sub_details.setdefault("pr_status", None); sub_details.setdefault("focused", False)
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
                        current_subs[sub_name] = {"done": bool(sub_details), "notes": [], "hidden": False, "pr_url": None, "pr_status": None, "focused": False}
                    else:
                        sub_details.setdefault("done", False); sub_details.setdefault("notes", []); sub_details.setdefault("hidden", False); sub_details.setdefault("pr_url", None); sub_details.setdefault("pr_status", None); sub_details.setdefault("focused", False)

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

def poll_reviews_needed(config):
    """Polls for pull requests that need the user's review."""
    global pull_requests_for_review, sent_review_notifications
    
    api_token = config.get("API_TOKEN")
    user_id = config.get("USER_ID")
    review_url = config.get("STASH_REVIEW_URL")

    if not all([api_token, user_id, review_url]):
        return # Missing essential config

    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json;charset=UTF-8"}

    while True:
        try:
            response = requests.get(review_url, headers=headers, timeout=20)
            response.raise_for_status()
            prs_data = response.json()
            
            pending_reviews = []
            for pr in prs_data.get('values', []):
                for reviewer in pr.get('reviewers', []):
                    # reviewer.get('user', {}).get('id') == user_id and
                    if reviewer.get('status') == 'UNAPPROVED':
                        pending_reviews.append(pr)
                        # Handle notifications
                        if pr['id'] not in sent_review_notifications:
                            repo = f"{pr['toRef']['repository']['project']['key']}/{pr['toRef']['repository']['name']}"
                            notif_title = t('notification_review_title')
                            notif_body = t('notification_review_body', repo=repo, title=pr['title'])
                            send_desktop_notification(notif_title, notif_body)
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

def poll_pull_requests(data_lock, data_ref, config):
    api_token = config.get("API_TOKEN")
    my_user_id = config.get("USER_ID")

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

                    if original_subtask.get("hidden") or not pr_url or pr_status == 'merged':
                        continue

                    api_url = convert_to_api_url(pr_url, config)
                    if not api_url: continue

                    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json;charset=UTF-8"}
                    try:
                        response = requests.get(api_url, headers=headers, timeout=10)
                        response.raise_for_status()
                        activities = response.json()

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
                            notes_without_unhandled = [n for n in notes if not n.startswith("UNHANDLED")]
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

def convert_to_api_url(pr_url, config):
    match = re.search(r'projects/(?P<projectKey>[^/]+)/repos/(?P<repositorySlug>[^/]+)/pull-requests/(?P<pullRequestId>\d+)', pr_url)
    if match:
        parts = match.groupdict()
        return f"{config.get('STASH_URL')}/rest/api/1.0/projects/{parts['projectKey']}/repos/{parts['repositorySlug']}/pull-requests/{parts['pullRequestId']}/activities"
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

def event_notification_poller(data_lock, data_ref, config):
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

    def focus_window(window_title):
        try:
            subprocess.run(['/usr/bin/xdotool', 'search', '--name', window_title, 'windowactivate'], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass # Silently fail if xdotool is not available or fails

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
                
                if event['type'] == 'meeting':
                    rec_str = f"({t('recurring')}) " if event['recurring'] else ""
                    notification_title = t('notification_meeting_title', rec=rec_str, min=minutes_until, time=event_time_str)
                    notification_body = t('notification_meeting_body', link=event['details'])
                else: # interruption
                    rec_str = f"({t('recurring')}) " if event['recurring'] else ""
                    notification_title = t('notification_event_title', rec=rec_str, min=minutes_until, time=event_time_str)
                    notification_body = event['details']

                # 10-minute warning
                if minutes_until == 10 and (event_id, '10min') not in sent_notifications:
                    focus_window(config.get("NOTIFICATION_WINDOW_TITLE"))
                    send_desktop_notification(notification_title, notification_body)
                    sent_notifications.add((event_id, '10min'))

                # 5-minute warning
                elif minutes_until == 5 and (event_id, '5min') not in sent_notifications:
                    focus_window(config.get("NOTIFICATION_WINDOW_TITLE"))
                    send_desktop_notification(notification_title, notification_body)
                    sent_notifications.add((event_id, '5min'))
                    if event['type'] == 'meeting' and event.get('details', '').startswith('http'):
                        open_link_in_browser(event['details'], config.get("BROWSER_COMMAND"))

        time.sleep(60)


def main(stdscr):
    global COLOR_PAIR_DEFAULT, COLOR_PAIR_REVERSE, COLOR_PAIR_GREY, COLOR_PAIR_PAUSED, COLOR_PAIR_SELECTED, COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, COLOR_PAIR_URGENT_BOX, COLOR_PAIR_PR_UNHANDLED, COLOR_PAIR_PR_APPROVED, COLOR_PAIR_FOCUSED
    
    config = load_config()
    load_translations(config.get("LANGUAGE", "fi"))
    
    if not STRINGS:
        print(f"Fatal: Could not load language files. Exiting.", file=sys.stderr)
        return
    
    if config.get("API_TOKEN") == "PASTE_YOUR_BEARER_TOKEN_HERE":
        print("ERROR: API_TOKEN has not been set in config.json. Please update it and restart.", file=sys.stderr)
        return

    try:
        curses.start_color()
        bg = curses.COLOR_BLACK
        curses.init_pair(COLOR_PAIR_DEFAULT, curses.COLOR_WHITE, bg)
        curses.init_pair(COLOR_PAIR_REVERSE, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(COLOR_PAIR_GREY, curses.COLOR_BLUE, bg)
        curses.init_pair(COLOR_PAIR_PAUSED, curses.COLOR_YELLOW, bg)
        try:
            curses.init_pair(COLOR_PAIR_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, curses.COLOR_YELLOW, bg)
            curses.init_pair(COLOR_PAIR_URGENT_BOX, curses.COLOR_RED, bg)
            curses.init_pair(COLOR_PAIR_PR_UNHANDLED, curses.COLOR_WHITE, curses.COLOR_RED)
            curses.init_pair(COLOR_PAIR_PR_APPROVED, curses.COLOR_BLACK, curses.COLOR_GREEN)
            curses.init_pair(COLOR_PAIR_FOCUSED, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        except:
            curses.init_pair(COLOR_PAIR_SELECTED, curses.COLOR_BLACK, curses.COLOR_GREEN)
            curses.init_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, curses.COLOR_RED, bg)
            curses.init_pair(COLOR_PAIR_URGENT_BOX, curses.COLOR_MAGENTA, bg)
            curses.init_pair(COLOR_PAIR_PR_UNHANDLED, curses.COLOR_BLACK, curses.COLOR_RED)
            curses.init_pair(COLOR_PAIR_PR_APPROVED, curses.COLOR_WHITE, curses.COLOR_GREEN)
            curses.init_pair(COLOR_PAIR_FOCUSED, curses.COLOR_BLACK, curses.COLOR_YELLOW)

    except curses.error:
        COLOR_PAIR_DEFAULT=0; COLOR_PAIR_REVERSE=0; COLOR_PAIR_GREY=0; COLOR_PAIR_PAUSED=0; COLOR_PAIR_SELECTED=0; COLOR_PAIR_TASK_ALL_SUBTASKS_DONE=0; COLOR_PAIR_URGENT_BOX=0; COLOR_PAIR_PR_UNHANDLED=0; COLOR_PAIR_PR_APPROVED=0; COLOR_PAIR_FOCUSED=0
    except Exception:
        COLOR_PAIR_DEFAULT=0; COLOR_PAIR_REVERSE=0; COLOR_PAIR_GREY=0; COLOR_PAIR_PAUSED=0; COLOR_PAIR_SELECTED=0; COLOR_PAIR_TASK_ALL_SUBTASKS_DONE=0; COLOR_PAIR_URGENT_BOX=0; COLOR_PAIR_PR_UNHANDLED=0; COLOR_PAIR_PR_APPROVED=0; COLOR_PAIR_FOCUSED=0

    try: curses.curs_set(1)
    except curses.error: pass
    stdscr.nodelay(True)
    stdscr.keypad(True)

    data = load_data()
    data_lock = threading.Lock()
    command_buffer = ""

    current_view = VIEW_MAIN
    selected_subtask_index = -1
    selected_note_index = -1
    entity_for_dedicated_notes = None
    show_help_footer = False
    current_date_for_daily_notes = date.today()

    pr_polling_thread = threading.Thread(target=poll_pull_requests, args=(data_lock, data, config), daemon=True)
    pr_polling_thread.start()

    notification_thread = threading.Thread(target=event_notification_poller, args=(data_lock, data, config), daemon=True)
    notification_thread.start()

    review_polling_thread = threading.Thread(target=poll_reviews_needed, args=(config,), daemon=True)
    review_polling_thread.start()

    clock_refresh_interval = 1.0; last_clock_refresh_time = 0.0
    content_refresh_interval = 10.0; last_content_refresh_time = 0.0
    request_full_redraw = True
    previous_window_size = (0,0)

    ticket_name_at_loop_start = data.get("current_ticket")

    while True:
        current_time = time.time()
        try: new_height, new_width = stdscr.getmaxyx()
        except curses.error: break

        if (new_height, new_width) != previous_window_size:
            request_full_redraw = True
            previous_window_size = (new_height, new_width)
        height, width = new_height, new_width

        with data_lock:
            ticket_name_at_loop_start = data.get("current_ticket")

            completed_tickets = data.get("completed_tickets", [])
            current_ticket_subtasks_unfiltered = data.get("sub_tasks", {}).get(ticket_name_at_loop_start, {}) if ticket_name_at_loop_start else {}
            current_ticket_subtask_list_visible = []
            if isinstance(current_ticket_subtasks_unfiltered, dict):
                current_ticket_subtask_list_visible = [
                    (name, details) for name, details in current_ticket_subtasks_unfiltered.items()
                    if isinstance(details, dict) and not details.get("hidden", False)
                ]

            all_tickets_set_for_cmd = set()
            if data.get("current_ticket"): all_tickets_set_for_cmd.add(data.get("current_ticket"))
            all_tickets_set_for_cmd.update(data.get("sub_tasks", {}).keys())
            all_tickets_set_for_cmd.update(data.get("notes", {}).keys())
            for paused_item_cmd in data.get("paused_tasks", []):
                if paused_item_cmd.get("ticket"): all_tickets_set_for_cmd.add(paused_item_cmd["ticket"])
            
            all_displayable_tickets_for_handle_input = sorted([t for t in list(filter(None, all_tickets_set_for_cmd)) if t not in completed_tickets])


        key = -1
        try: key = stdscr.get_wch()
        except curses.error: pass
        except KeyboardInterrupt: break

        user_activity_caused_draw_this_cycle = False

        if key != -1:
            last_content_refresh_time = current_time
            last_clock_refresh_time = current_time
            user_activity_caused_draw_this_cycle = True

            if key == curses.KEY_BTAB:
                if current_view == VIEW_MAIN:
                    with data_lock:
                        active_main_ticket = data.get("current_ticket")
                    if selected_subtask_index != -1 and 0 <= selected_subtask_index < len(current_ticket_subtask_list_visible):
                        sub_name, _ = current_ticket_subtask_list_visible[selected_subtask_index]
                        entity_for_dedicated_notes = {"type": "subtask", "name": sub_name, "main_task_name": active_main_ticket}
                        current_view = VIEW_DEDICATED_NOTES
                    elif active_main_ticket:
                        entity_for_dedicated_notes = {"type": "task", "name": active_main_ticket}
                        current_view = VIEW_DEDICATED_NOTES
                    if current_view == VIEW_DEDICATED_NOTES:
                        command_buffer = ""; request_full_redraw = True; selected_note_index = -1
                elif current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES]:
                    current_view = VIEW_MAIN
                    entity_for_dedicated_notes = None; selected_note_index = -1
                    command_buffer = ""; request_full_redraw = True

            elif key == 27: # ESC key
                if current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES]:
                    current_view = VIEW_MAIN
                    entity_for_dedicated_notes = None; selected_note_index = -1
                    command_buffer = ""; request_full_redraw = True

            if current_view == VIEW_MAIN:
                if key == curses.KEY_LEFT:
                    current_view = VIEW_DAILY_NOTES
                    current_date_for_daily_notes = date.today()
                    command_buffer = ""; request_full_redraw = True; selected_note_index = -1
                elif key == curses.KEY_UP:
                    if current_ticket_subtask_list_visible:
                        if selected_subtask_index == 0:
                            selected_subtask_index = -1
                        elif selected_subtask_index > 0:
                            selected_subtask_index -= 1
                        request_full_redraw = True
                elif key == curses.KEY_DOWN:
                    if current_ticket_subtask_list_visible:
                        last_idx = len(current_ticket_subtask_list_visible) - 1
                        if selected_subtask_index == last_idx:
                             selected_subtask_index = -1
                        elif selected_subtask_index < last_idx:
                            selected_subtask_index += 1
                        request_full_redraw = True
                elif key == '\n' or key == curses.KEY_ENTER:
                    cmd_parts = command_buffer.split()
                    action_processed = False
                    ticket_changed = False

                    if cmd_parts:
                        with data_lock:
                            original_ticket = data.get("current_ticket")
                            handle_result = handle_input(data, cmd_parts, stdscr, current_view, selected_subtask_index, selected_note_index, current_ticket_subtask_list_visible, all_displayable_tickets_for_handle_input)
                        if handle_result is None: break
                        elif handle_result == "TOGGLE_HELP": show_help_footer = not show_help_footer
                        elif handle_result != "NO_CHANGE":
                            with data_lock:
                                data = handle_result
                                if data.get("current_ticket") != original_ticket:
                                    ticket_changed = True
                                save_data(data)
                        action_processed = True
                    elif selected_subtask_index != -1 and 0 <= selected_subtask_index < len(current_ticket_subtask_list_visible):
                        sub_task_name, sub_task_details = current_ticket_subtask_list_visible[selected_subtask_index]
                        with data_lock:
                            main_ticket = data.get("current_ticket")
                            sub_task = data["sub_tasks"][main_ticket].get(sub_task_name)
                            if sub_task:
                                sub_task["done"] = not sub_task["done"]
                                # Auto-unfocus if marked done
                                if sub_task["done"] and sub_task.get("focused"):
                                    sub_task["focused"] = False
                                    data["focused_subtask"] = None
                                    data["focused_ticket"] = None
                                save_data(data)
                        action_processed = True

                    if action_processed or ticket_changed:
                        with data_lock:
                            new_ticket = data.get("current_ticket")
                        if new_ticket != ticket_name_at_loop_start:
                            selected_subtask_index = -1
                    command_buffer = ""
                    request_full_redraw = True

                elif key not in [curses.KEY_UP, curses.KEY_DOWN, curses.KEY_BTAB, 27, curses.KEY_LEFT, curses.KEY_RIGHT]:
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

            elif current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES]:
                notes_list_size = 0
                with data_lock:
                    if current_view == VIEW_DEDICATED_NOTES and entity_for_dedicated_notes:
                        ent_type = entity_for_dedicated_notes.get("type")
                        ent_name = entity_for_dedicated_notes.get("name")
                        if ent_type == "task":
                            notes_list_size = len(data.get("notes", {}).get(ent_name, []))
                        elif ent_type == "subtask":
                            main_task = entity_for_dedicated_notes.get("main_task_name")
                            sub_details = data.get("sub_tasks",{}).get(main_task,{}).get(ent_name)
                            if sub_details: notes_list_size = len(sub_details.get("notes", []))
                    elif current_view == VIEW_DAILY_NOTES:
                        date_iso = current_date_for_daily_notes.isoformat()
                        notes_list_size = len(data.get("daily_notes", {}).get(date_iso, []))

                if key == curses.KEY_UP:
                    if selected_note_index > -1:
                        selected_note_index -= 1
                    request_full_redraw = True
                elif key == curses.KEY_DOWN:
                    if notes_list_size > 0 and selected_note_index < notes_list_size - 1:
                        selected_note_index += 1
                    request_full_redraw = True
                elif key == '\n' or key == curses.KEY_ENTER:
                    cmd_parts = command_buffer.split()
                    if cmd_parts and cmd_parts[0].lower() == 'd' and selected_note_index != -1:
                         if 0 <= selected_note_index < notes_list_size:
                            with data_lock:
                                if current_view == VIEW_DEDICATED_NOTES:
                                    ent_type = entity_for_dedicated_notes.get("type")
                                    ent_name = entity_for_dedicated_notes.get("name")
                                    if ent_type == "task":
                                        data["notes"][ent_name].pop(selected_note_index)
                                    elif ent_type == "subtask":
                                        main_task = entity_for_dedicated_notes.get("main_task_name")
                                        data["sub_tasks"][main_task][ent_name]["notes"].pop(selected_note_index)
                                elif current_view == VIEW_DAILY_NOTES:
                                    date_iso = current_date_for_daily_notes.isoformat()
                                    data["daily_notes"][date_iso].pop(selected_note_index)
                                save_data(data)
                            
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
                                    data.setdefault("notes",{}).setdefault(ent_name,[]).append(command_buffer)
                                elif ent_type == "subtask":
                                    main_task = entity_for_dedicated_notes.get("main_task_name")
                                    sub_details = data.get("sub_tasks",{}).get(main_task,{}).get(ent_name)
                                    if sub_details:
                                        sub_details.setdefault("notes",[]).append(command_buffer)
                            elif current_view == VIEW_DAILY_NOTES:
                                date_iso = current_date_for_daily_notes.isoformat()
                                data.setdefault("daily_notes", {}).setdefault(date_iso, []).append(command_buffer)
                            save_data(data)
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
                elif isinstance(key, str) and key.isprintable():
                    command_buffer += key
                    request_full_redraw = True
                elif key in [curses.KEY_BACKSPACE, 127, 8]:
                    command_buffer = command_buffer[:-1]
                    request_full_redraw = True

            # Redraw the UI after every valid keypress.
            request_full_redraw = True
            last_content_refresh_time = 0
            display_ui(stdscr, data, command_buffer, request_full_redraw, selected_subtask_index, current_view, entity_for_dedicated_notes, current_ticket_subtask_list_visible, show_help_footer, current_date_for_daily_notes, selected_note_index)
            if request_full_redraw : request_full_redraw = False

        if not user_activity_caused_draw_this_cycle:
            if current_time - last_content_refresh_time >= content_refresh_interval:
                request_full_redraw = True
            
            if request_full_redraw or (current_time - last_clock_refresh_time >= clock_refresh_interval):
                display_ui(stdscr, data, command_buffer, request_full_redraw, selected_subtask_index, current_view, entity_for_dedicated_notes, current_ticket_subtask_list_visible, show_help_footer, current_date_for_daily_notes, selected_note_index)
                last_clock_refresh_time = current_time
                if request_full_redraw:
                    last_content_refresh_time = current_time
                    request_full_redraw = False
        time.sleep(0.05)

if __name__ == "__main__":
    try:
        curses.wrapper(main)
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
        print(f"\n{t('app_closed')}")
