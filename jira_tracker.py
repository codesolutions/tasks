#!/usr/bin/env python3
"""
Terminal Project Tracker - Main Application

A modern, modular terminal-based project tracking application.
All functionality has been refactored into separate modules to eliminate code duplication.
"""

import curses
import sys
import time
import logging
import threading
import copy
from datetime import datetime, date, timedelta

# Configuration and core modules
import inc.config_manager
from inc.helpers import t

# Constants and utilities
from inc.utils.constants import (
    LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT, CACHE_DIR,
    COLOR_PAIR_DEFAULT, COLOR_PAIR_REVERSE, COLOR_PAIR_GREY, COLOR_PAIR_PAUSED,
    COLOR_PAIR_SELECTED, COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN,
    COLOR_PAIR_URGENT_BOX, COLOR_PAIR_PR_UNHANDLED, COLOR_PAIR_PR_APPROVED, COLOR_PAIR_FOCUSED,
    COLOR_PAIR_PERMANENT_NOTIFICATION, COLOR_PAIR_STANDOUT, COLOR_PAIR_NEW_COMMENT,
    VIEW_MAIN, VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES, VIEW_TIME_LOG, VIEW_HOURLY_CHECKIN
)

# Core functionality
from inc.core.data_manager import data_manager
from inc.commands import initialize_commands
from inc.core.command_handler import handle_input_new

# Integrations
from inc.integrations.notification_service import send_desktop_notification
from inc.utils.formatters import focus_window
from inc.integrations.calendar_poller import calendar_poller
from inc.integrations.web_monitor import web_monitor
from inc.integrations.pr_monitor import poll_pull_requests, poll_reviews_needed
from inc.integrations.event_poller import event_notification_poller
from inc.views.base_view import show_notification, show_permanent_notification

# JIRA integration
from inc.jira import (
    load_jira_cache, jira_queue_worker, get_and_save_web_session
)

# Time tracking
from inc.time_tracker import HourlyCheckinScheduler, note_user_activity

# Utilities
from inc.utils.formatters import format_subtask_for_title

# Views - UI rendering is now modularized
from inc.views.main_view import display_main_view
from inc.views.dedicated_notes_view import display_dedicated_notes_view
from inc.views.daily_notes_view import display_daily_notes_view
from inc.views.time_log_view import display_time_log_view
from inc.views.hourly_checkin_view import display_hourly_checkin_view

# Configure logging
logging.basicConfig(
    filename=LOG_FILE,
    filemode='a',
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    level=logging.DEBUG
)

# Global state variables (legacy compatibility)
sent_notifications = set()
pull_requests_for_review = []
reviews_lock = threading.Lock()
sent_review_notifications = set()
permanent_notifications = []
app_data = {}

# External calendar integration
external_meetings = []
external_meetings_lock = threading.Lock()
web_change_notifications = []


def initialize_application():
    """Initialize all application components."""
    # Ensure cache directory exists
    import os
    os.makedirs(CACHE_DIR, exist_ok=True)
    
    # Setup locale
    import locale
    try:
        locale.setlocale(locale.LC_ALL, '')
    except locale.Error as e:
        print(f"Warning: Could not set locale ({e}). Non-ASCII characters may not work correctly.", file=sys.stderr)


def setup_colors():
    """Initialize curses color pairs."""
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
    except:
        pass  # Continue without colors if not supported


def display_ui(stdscr, data, command_buffer="", full_redraw=False, selected_subtask_idx=-1,
               current_view_mode=VIEW_MAIN, entity_for_dedicated_notes=None,
               current_ticket_subtask_list_for_display_arg=None, show_help_footer=True,
               current_date_for_daily_notes_arg=None, selected_note_idx=-1,
               jira_cache=None, jira_cache_lock=None, notes_scroll_offset=0,
               selected_checkin_task_idx=-1):
    """Dispatch UI rendering to appropriate view modules."""
    
    # All UI rendering is now handled by modular view classes
    if current_view_mode == VIEW_DEDICATED_NOTES:
        return display_dedicated_notes_view(
            stdscr, data, command_buffer, entity_for_dedicated_notes, 
            show_help_footer, selected_note_idx, jira_cache, jira_cache_lock, notes_scroll_offset
        )
    elif current_view_mode == VIEW_DAILY_NOTES:
        return display_daily_notes_view(
            stdscr, data, command_buffer, current_date_for_daily_notes_arg, 
            show_help_footer, selected_note_idx
        )
    elif current_view_mode == VIEW_TIME_LOG:
        return display_time_log_view(stdscr, data, current_date_for_daily_notes_arg)
    elif current_view_mode == VIEW_HOURLY_CHECKIN:
        return display_hourly_checkin_view(stdscr, data, selected_checkin_task_idx)
    
    # Default to main view
    return display_main_view(
        stdscr, data, command_buffer, full_redraw, selected_subtask_idx, 
        current_view_mode, entity_for_dedicated_notes, 
        current_ticket_subtask_list_for_display_arg, show_help_footer, 
        current_date_for_daily_notes_arg, selected_note_idx, 
        jira_cache, jira_cache_lock, reviews_lock, external_meetings_lock, notes_scroll_offset,
        pull_requests_for_review, permanent_notifications, web_change_notifications, external_meetings
    )


# All utility functions are now imported from their respective modules


def main(stdscr):
    """Main application loop - now much cleaner with modular components."""
    global app_data, permanent_notifications
    
    # Initialize application
    initialize_application()
    
    # Validate configuration
    if not inc.config_manager.STRINGS:
        print("Fatal: Could not load language files. Exiting.", file=sys.stderr)
        return "EXIT"

    if inc.config_manager.config.get("API_TOKEN") == "PASTE_YOUR_BEARER_TOKEN_HERE":
        print("ERROR: API_TOKEN has not been set in config.json. Please update it and restart.", file=sys.stderr)
        return "EXIT"

    # Setup curses
    setup_colors()
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    stdscr.nodelay(True)
    stdscr.keypad(True)
    
    # Enable mouse support
    try:
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    except:
        pass

    # Load application data
    app_data = data_manager.load_data()
    jira_cache = load_jira_cache()
    jira_cache_lock = threading.Lock()
    data_lock = threading.Lock()
    
    # Initialize command system
    initialize_commands()
    
    # Initialize view state
    command_buffer = ""
    current_view = VIEW_MAIN
    selected_subtask_index = -1
    selected_note_index = -1
    entity_for_dedicated_notes = None
    show_help_footer = False
    current_date_for_daily_notes = date.today()
    selected_checkin_task_index = -1
    notes_scroll_offset = 0
    
    # Start background services using the modular components
    stop_event = threading.Event()
    
    # Time tracking scheduler
    time_scheduler = HourlyCheckinScheduler(app_data, inc.config_manager.config, data_lock)
    time_scheduler.start()
    
    # JIRA integration
    jira_thread = threading.Thread(
        target=jira_queue_worker, 
        args=(stop_event, permanent_notifications, jira_cache, jira_cache_lock), 
        daemon=True
    )
    jira_thread.start()
    
    # Start integrated polling services
    # Note: These should be refactored to use the integration modules
    pr_polling_thread = threading.Thread(target=poll_pull_requests, args=(data_lock, app_data), daemon=True)
    pr_polling_thread.start()
    
    notification_thread = threading.Thread(target=event_notification_poller, args=(data_lock, app_data), daemon=True)
    notification_thread.start()
    
    review_polling_thread = threading.Thread(target=poll_reviews_needed, daemon=True)
    review_polling_thread.start()
    
    # Start modular services
    calendar_poller.start()
    web_monitor.start()
    
    # Main application loop - much cleaner now!
    clock_refresh_interval = 1.0
    last_clock_refresh_time = 0.0
    content_refresh_interval = 120.0
    last_content_refresh_time = 0.0
    request_full_redraw = True
    previous_window_size = (0, 0)
    
    while True:
        current_time = time.time()
        
        try:
            new_height, new_width = stdscr.getmaxyx()
        except curses.error:
            break
        
        if (new_height, new_width) != previous_window_size:
            request_full_redraw = True
            previous_window_size = (new_height, new_width)
        
        height, width = new_height, new_width
        
        # Get current state
        with data_lock:
            ticket_name_at_loop_start = app_data.get("current_ticket")
            
            # Handle hourly check-in
            if app_data.get("pending_checkin") and current_view == VIEW_MAIN:
                current_view = VIEW_HOURLY_CHECKIN
                selected_checkin_task_index = -1
                command_buffer = ""
                request_full_redraw = True
                
                # Send notification
                pending_checkin = app_data.get("pending_checkin", {})
                duration_minutes = pending_checkin.get("duration_seconds", 3600) // 60
                send_desktop_notification(
                    "⏰ Hourly Check-in Time!",
                    f"Please account for the last {duration_minutes} minutes. What were you working on?"
                )
                
                window_title = inc.config_manager.config.get("NOTIFICATION_WINDOW_TITLE")
                if window_title:
                    focus_window(window_title)
            
            # Build current subtask list
            current_ticket_subtasks = app_data.get("sub_tasks", {}).get(ticket_name_at_loop_start, {}) if ticket_name_at_loop_start else {}
            current_ticket_subtask_list_visible = []
            if isinstance(current_ticket_subtasks, dict):
                show_hidden = app_data.get("show_hidden_tasks", False)
                current_ticket_subtask_list_visible = [
                    (name, details) for name, details in current_ticket_subtasks.items()
                    if isinstance(details, dict) and (show_hidden or details.get("status") != "hidden")
                ]
            
            # Build displayable tickets list for commands
            completed_tickets = app_data.get("completed_tickets", [])
            all_tickets_set = set()
            if app_data.get("current_ticket"):
                all_tickets_set.add(app_data.get("current_ticket"))
            all_tickets_set.update(app_data.get("sub_tasks", {}).keys())
            all_tickets_set.update(app_data.get("notes", {}).keys())
            for paused_item in app_data.get("paused_tasks", []):
                if paused_item.get("ticket"):
                    all_tickets_set.add(paused_item["ticket"])
            
            all_displayable_tickets = sorted([
                t for t in filter(None, all_tickets_set) 
                if t not in completed_tickets
            ])
        
        # Handle input
        key = -1
        try:
            key = stdscr.get_wch()
        except curses.error:
            pass
        except KeyboardInterrupt:
            break
        
        user_activity_caused_draw_this_cycle = False
        
        if key != -1:
            last_content_refresh_time = current_time
            last_clock_refresh_time = current_time
            user_activity_caused_draw_this_cycle = True
            
            # Note user activity for time tracking
            with data_lock:
                note_user_activity(app_data)
            
            # Handle view switching
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
                        command_buffer = ""
                        request_full_redraw = True
                        selected_note_index = -1
                        notes_scroll_offset = 0
                elif current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES, VIEW_TIME_LOG]:
                    current_view = VIEW_MAIN
                    entity_for_dedicated_notes = None
                    selected_note_index = -1
                    command_buffer = ""
                    request_full_redraw = True
            
            elif key == 27:  # ESC key
                if current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES, VIEW_TIME_LOG]:
                    current_view = VIEW_MAIN
                    entity_for_dedicated_notes = None
                    selected_note_index = -1
                    command_buffer = ""
                    request_full_redraw = True
                elif current_view == VIEW_HOURLY_CHECKIN:
                    with data_lock:
                        app_data["pending_checkin"] = None
                        data_manager.save_data(app_data)
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""
                    request_full_redraw = True
            
            # Handle main view input
            if current_view == VIEW_MAIN:
                if key == curses.KEY_LEFT:
                    current_view = VIEW_DAILY_NOTES
                    current_date_for_daily_notes = date.today()
                    command_buffer = ""
                    request_full_redraw = True
                    selected_note_index = -1
                
                elif key == curses.KEY_UP:
                    if current_ticket_subtask_list_visible:
                        if selected_subtask_index > -1:
                            selected_subtask_index -= 1
                        request_full_redraw = True
                
                elif key == curses.KEY_DOWN:
                    if current_ticket_subtask_list_visible:
                        last_idx = len(current_ticket_subtask_list_visible) - 1
                        if selected_subtask_index < last_idx:
                            selected_subtask_index += 1
                        else:
                            selected_subtask_index = -1
                        request_full_redraw = True
                
                elif key == '\n' or key == curses.KEY_ENTER:
                    cmd_parts = command_buffer.split()
                    
                    # Handle empty command (toggle subtask status)
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
                                    next_index = 0
                                app_data["sub_tasks"][ticket_name_at_loop_start][sub_task_name]["status"] = status_cycle[next_index]
                                data_manager.save_data(app_data)
                                request_full_redraw = True
                    
                    # Handle command execution
                    elif cmd_parts:
                        with data_lock:
                            original_ticket = app_data.get("current_ticket")
                            handle_result = handle_input_new(
                                app_data, cmd_parts, stdscr, current_view, selected_subtask_index, 
                                selected_note_index, current_ticket_subtask_list_visible, all_displayable_tickets
                            )
                        
                        if handle_result is None:
                            break
                        elif handle_result == "RESTART_FOR_LOGIN":
                            permanent_notifications = []
                            return "RESTART_FOR_LOGIN"
                        elif handle_result == "TOGGLE_HELP":
                            show_help_footer = not show_help_footer
                        elif handle_result == "VIEW_TIME_LOG":
                            current_view = VIEW_TIME_LOG
                            current_date_for_daily_notes = date.today()
                            command_buffer = ""
                            request_full_redraw = True
                            selected_note_index = -1
                        elif handle_result != "NO_CHANGE":
                            with data_lock:
                                app_data = handle_result
                                if app_data.get("current_ticket") != original_ticket:
                                    selected_subtask_index = -1
                                data_manager.save_data(app_data)
                    
                    command_buffer = ""
                    request_full_redraw = True
                
                # Handle typing
                elif isinstance(key, str) and key.isprintable():
                    max_len = (width - 1) - len("> ") if width > 0 else 0
                    if len(command_buffer) < max_len:
                        command_buffer += key
                    else:
                        try:
                            curses.beep()
                        except:
                            pass
                
                elif key in [curses.KEY_BACKSPACE, 127, 8]:
                    command_buffer = command_buffer[:-1]
                
                elif key == curses.KEY_RESIZE:
                    request_full_redraw = True
            
            # Handle other view inputs (simplified - full implementation in view modules)
            elif current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES, VIEW_TIME_LOG]:
                # Note navigation, editing, etc. handled by view modules
                if key == curses.KEY_UP:
                    if selected_note_index > -1:
                        selected_note_index -= 1
                    request_full_redraw = True
                elif key == curses.KEY_DOWN:
                    selected_note_index += 1  # Simplified - actual bounds checking in views
                    request_full_redraw = True
                elif isinstance(key, str) and key.isprintable():
                    command_buffer += key
                    request_full_redraw = True
                elif key in [curses.KEY_BACKSPACE, 127, 8]:
                    command_buffer = command_buffer[:-1]
                    request_full_redraw = True
        
        # Render UI
        if user_activity_caused_draw_this_cycle or request_full_redraw or \
           (current_time - last_clock_refresh_time >= clock_refresh_interval):
            
            display_ui(stdscr, app_data, command_buffer, request_full_redraw, selected_subtask_index, 
                      current_view, entity_for_dedicated_notes, current_ticket_subtask_list_visible, 
                      show_help_footer, current_date_for_daily_notes, selected_note_index, 
                      jira_cache, jira_cache_lock, notes_scroll_offset, selected_checkin_task_index)
            
            last_clock_refresh_time = current_time
            if request_full_redraw:
                last_content_refresh_time = current_time
                request_full_redraw = False
        
        time.sleep(0.05)
    
    return "EXIT"


if __name__ == "__main__":
    # Application entry point
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
                curses.nocbreak()
                curses.echo()
                curses.endwin()
            except:
                pass
        except Exception as e:
            import traceback
            print(t('error_unexpected', e=e), file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        finally:
            try:
                if 'stdscr' in locals() and 'curses' in sys.modules and not sys.modules['curses'].isendwin():
                    curses.nocbreak()
                    if hasattr(stdscr, 'keypad'):
                        stdscr.keypad(False)
                    curses.echo()
                    curses.endwin()
                elif 'curses' in sys.modules and not sys.modules['curses'].isendwin():
                    curses.nocbreak()
                    curses.echo()
                    curses.endwin()
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
            
            print("\\nLogin process finished. Restarting application in 3 seconds...")
            time.sleep(3)
            continue
        elif result is None:
            continue
        else:
            break
    
    print(f"\\n{t('app_closed')}")
