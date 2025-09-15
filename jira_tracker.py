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
import inc.helpers
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
        
        # Initialize state variables 
        ticket_name_at_loop_start = None
        current_ticket_subtask_list_visible = []
        all_displayable_tickets = []
        
        # Get current state and update lists dynamically
        def update_current_state():
            """Update current state variables from app data"""
            nonlocal ticket_name_at_loop_start, current_ticket_subtask_list_visible, all_displayable_tickets
            
            ticket_name_at_loop_start = app_data.get("current_ticket")
            
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
        
        with data_lock:
            # Handle auto-end daily summary (takes priority over everything else)
            if app_data.get("show_auto_end_summary"):
                from inc.views.daily_summary_view import show_daily_summary
                show_daily_summary(stdscr, app_data, auto_end=True)
                app_data.pop("show_auto_end_summary", None)  # Clear the flag
                data_manager.save_data(app_data)
                
                # If we were in check-in view, return to main
                if current_view == VIEW_HOURLY_CHECKIN:
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""
                
                request_full_redraw = True
            
            # Handle hourly check-in (only if not in other views and no auto-end summary)
            elif app_data.get("pending_checkin") and current_view == VIEW_MAIN:
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
            
            # Initial state update
            update_current_state()
        
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
                        
                        # Mark Jira comments as read when switching to subtask notes view
                        jira_ticket_id = inc.helpers.get_jira_ticket_from_url(sub_name)
                        if jira_ticket_id:
                            with jira_cache_lock:
                                if jira_ticket_id in jira_cache and (jira_cache[jira_ticket_id].get('new_jira_comment') or jira_cache[jira_ticket_id].get('new_trello_comment')):
                                    jira_cache[jira_ticket_id]['new_jira_comment'] = False
                                    jira_cache[jira_ticket_id]['new_trello_comment'] = False
                                    from inc.jira import save_jira_cache
                                    save_jira_cache(jira_cache, jira_cache_lock)
                        
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
                        # Handle Jira comment marking as read
                        if selected_subtask_index != -1:
                            sub_task_name, _ = current_ticket_subtask_list_visible[selected_subtask_index]
                            jira_ticket_id = inc.helpers.get_jira_ticket_from_url(sub_task_name)
                            with jira_cache_lock:
                                if jira_ticket_id in jira_cache and (jira_cache[jira_ticket_id].get('new_jira_comment') or jira_cache[jira_ticket_id].get('new_trello_comment')):
                                    jira_cache[jira_ticket_id]['new_jira_comment'] = False
                                    jira_cache[jira_ticket_id]['new_trello_comment'] = False
                                    from inc.jira import save_jira_cache
                                    save_jira_cache(jira_cache, jira_cache_lock)
                
                elif key == curses.KEY_DOWN:
                    if current_ticket_subtask_list_visible:
                        last_idx = len(current_ticket_subtask_list_visible) - 1
                        if selected_subtask_index < last_idx:
                            selected_subtask_index += 1
                        else:
                            selected_subtask_index = -1
                        request_full_redraw = True
                        # Handle Jira comment marking as read
                        if selected_subtask_index != -1:
                            sub_task_name, _ = current_ticket_subtask_list_visible[selected_subtask_index]
                            jira_ticket_id = inc.helpers.get_jira_ticket_from_url(sub_task_name)
                            with jira_cache_lock:
                                if jira_ticket_id in jira_cache and (jira_cache[jira_ticket_id].get('new_jira_comment') or jira_cache[jira_ticket_id].get('new_trello_comment')):
                                    jira_cache[jira_ticket_id]['new_jira_comment'] = False
                                    jira_cache[jira_ticket_id]['new_trello_comment'] = False
                                    from inc.jira import save_jira_cache
                                    save_jira_cache(jira_cache, jira_cache_lock)
                
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
                                # Refresh state after subtask status change
                                update_current_state()
                                request_full_redraw = True
                    
                    # Handle command execution
                    elif cmd_parts:
                        with data_lock:
                            original_ticket = app_data.get("current_ticket")
                            
                            # Create command context for the new command system
                            from inc.commands.base_command import CommandContext
                            from inc.core.command_handler import command_handler
                            
                            context = CommandContext(
                                stdscr=stdscr,
                                selected_subtask_idx=selected_subtask_index,
                                current_view=current_view,
                                show_help_footer=show_help_footer,
                                current_ticket_subtask_list=current_ticket_subtask_list_visible
                            )
                            context.selected_note_idx = selected_note_index
                            
                            # Handle the command with better error handling
                            command_buffer_str = " ".join(cmd_parts)
                            result = command_handler.handle_command(command_buffer_str, app_data, context)
                        
                        # Show notification for command result
                        if result.message:
                            show_notification(stdscr, result.message)
                        
                        # Handle various result types
                        if result.quit_requested:
                            break
                        elif result.restart_for_login:
                            permanent_notifications = []
                            return "RESTART_FOR_LOGIN"
                        elif result.toggle_help:
                            show_help_footer = not show_help_footer
                        elif result.view_change == "time_log":
                            current_view = VIEW_TIME_LOG
                            current_date_for_daily_notes = date.today()
                            command_buffer = ""
                            request_full_redraw = True
                            selected_note_index = -1
                        elif result.data_modified:
                            with data_lock:
                                if app_data.get("current_ticket") != original_ticket:
                                    selected_subtask_index = -1
                                data_manager.save_data(app_data)
                                # Refresh state after data modification
                                update_current_state()
                    
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
            
            # Handle other view inputs - restore full functionality  
            elif current_view in [VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES, VIEW_TIME_LOG]:
                # Get proper notes list size for bounds checking
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
                            if sub_details: 
                                notes_list_size = len(sub_details.get("notes", []))
                    elif current_view == VIEW_DAILY_NOTES:
                        date_iso = current_date_for_daily_notes.isoformat()
                        notes_list_size = len(app_data.get("daily_notes", {}).get(date_iso, []))
                    elif current_view == VIEW_TIME_LOG:
                        # Handle time log navigation
                        pass
                
                # Navigation
                if key == curses.KEY_UP:
                    if selected_note_index > -1:
                        selected_note_index -= 1
                    request_full_redraw = True
                elif key == curses.KEY_DOWN:
                    if notes_list_size > 0 and selected_note_index < notes_list_size - 1:
                        selected_note_index += 1
                    request_full_redraw = True
                elif key == curses.KEY_LEFT and current_view == VIEW_TIME_LOG:
                    # Navigate to previous day
                    current_date_for_daily_notes = current_date_for_daily_notes - timedelta(days=1)
                    request_full_redraw = True
                elif key == curses.KEY_RIGHT and current_view == VIEW_TIME_LOG:
                    # Navigate to next day  
                    current_date_for_daily_notes = current_date_for_daily_notes + timedelta(days=1)
                    request_full_redraw = True
                elif key == curses.KEY_LEFT and current_view == VIEW_DAILY_NOTES:
                    # Navigate to previous day
                    current_date_for_daily_notes = current_date_for_daily_notes - timedelta(days=1)
                    request_full_redraw = True
                elif key == curses.KEY_RIGHT and current_view == VIEW_DAILY_NOTES:
                    # Navigate to next day
                    current_date_for_daily_notes = current_date_for_daily_notes + timedelta(days=1)
                    request_full_redraw = True
                # Scrolling support
                elif key == curses.KEY_NPAGE:  # Page Down
                    notes_scroll_offset += 10
                    request_full_redraw = True
                elif key == curses.KEY_PPAGE:  # Page Up
                    notes_scroll_offset = max(0, notes_scroll_offset - 10)
                    request_full_redraw = True
                elif key == curses.KEY_MOUSE:  # Mouse wheel support
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
                # Command handling for notes views
                elif key == '\n' or key == curses.KEY_ENTER:
                    cmd_parts = command_buffer.split()
                    if cmd_parts:
                        # Handle note deletion
                        if cmd_parts[0].lower() == 'd' and selected_note_index != -1:
                            if 0 <= selected_note_index < notes_list_size:
                                with data_lock:
                                    if current_view == VIEW_DEDICATED_NOTES:
                                        ent_type = entity_for_dedicated_notes.get("type")
                                        ent_name = entity_for_dedicated_notes.get("name")
                                        if ent_type == "task":
                                            if ent_name in app_data.get("notes", {}):
                                                app_data["notes"][ent_name].pop(selected_note_index)
                                                show_notification(stdscr, "Note deleted")
                                        elif ent_type == "subtask":
                                            main_task = entity_for_dedicated_notes.get("main_task_name")
                                            if main_task in app_data.get("sub_tasks", {}) and ent_name in app_data["sub_tasks"][main_task]:
                                                app_data["sub_tasks"][main_task][ent_name]["notes"].pop(selected_note_index)
                                                show_notification(stdscr, "Note deleted")
                                    elif current_view == VIEW_DAILY_NOTES:
                                        date_iso = current_date_for_daily_notes.isoformat()
                                        if date_iso in app_data.get("daily_notes", {}):
                                            app_data["daily_notes"][date_iso].pop(selected_note_index)
                                            show_notification(stdscr, "Daily note deleted")
                                    data_manager.save_data(app_data)
                                    selected_note_index = min(selected_note_index, max(0, notes_list_size - 2))
                        else:
                            # Add new note
                            note_text = " ".join(cmd_parts)
                            with data_lock:
                                if current_view == VIEW_DEDICATED_NOTES and entity_for_dedicated_notes:
                                    ent_type = entity_for_dedicated_notes.get("type")
                                    ent_name = entity_for_dedicated_notes.get("name")
                                    if ent_type == "task":
                                        app_data.setdefault("notes", {}).setdefault(ent_name, []).append(note_text)
                                        show_notification(stdscr, f"Note added to {ent_name}")
                                    elif ent_type == "subtask":
                                        main_task = entity_for_dedicated_notes.get("main_task_name")
                                        app_data.setdefault("sub_tasks", {}).setdefault(main_task, {}).setdefault(ent_name, {}).setdefault("notes", []).append(note_text)
                                        show_notification(stdscr, f"Note added to {ent_name}")
                                elif current_view == VIEW_DAILY_NOTES:
                                    date_iso = current_date_for_daily_notes.isoformat()
                                    app_data.setdefault("daily_notes", {}).setdefault(date_iso, []).append(note_text)
                                    show_notification(stdscr, f"Daily note added for {current_date_for_daily_notes.strftime('%Y-%m-%d')}")
                                data_manager.save_data(app_data)
                    command_buffer = ""
                    request_full_redraw = True
                # Text input
                elif isinstance(key, str) and key.isprintable():
                    max_len = (width - 1) - len("> ") if width > 0 else 0
                    if len(command_buffer) < max_len:
                        command_buffer += key
                    request_full_redraw = True
                elif key in [curses.KEY_BACKSPACE, 127, 8]:
                    command_buffer = command_buffer[:-1]
                    request_full_redraw = True
            
            # Handle hourly checkin view inputs - restore missing functionality
            elif current_view == VIEW_HOURLY_CHECKIN:
                if key in ['Y', 'y']:
                    # User worked on suggested task
                    with data_lock:
                        from inc.time_tracker import add_time_entry, start_focus_timer
                        
                        # Stop any currently running timer without logging it
                        # (the hourly check-in duration will replace it)
                        work_session = app_data.get("work_session", {})
                        if work_session.get("active") and not work_session.get("paused"):
                            work_session.pop("current_timer_start_ts", None)
                        
                        # Log the time from the hourly check-in
                        pending = app_data.get("pending_checkin", {})
                        if pending:
                            # Calculate actual time worked since timer started
                            timer_start_ts = pending.get("timer_start_ts")
                            if timer_start_ts:
                                actual_work_seconds = int(datetime.now().timestamp() - timer_start_ts)
                            else:
                                # Fallback - shouldn't happen with new system
                                actual_work_seconds = pending.get("duration_seconds", 3600)
                            
                            suggested_subtask = pending.get("suggested_subtask")
                            if suggested_subtask:
                                add_time_entry(app_data, entry_type="task", subtask=suggested_subtask, seconds=actual_work_seconds)
                                data_manager.save_data(app_data)
                                
                                # Restart timer for the suggested subtask
                                start_focus_timer(app_data)
                        
                        app_data["pending_checkin"] = None
                        data_manager.save_data(app_data)
                    
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""
                    request_full_redraw = True
                
                elif key in ['S', 's']:
                    # User wants to select a different task - start selection mode
                    with data_lock:
                        from inc.time_tracker import stop_focus_timer_and_log
                        
                        # Stop current timer and log time before entering selection mode
                        stop_focus_timer_and_log(app_data)
                        
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
                        from inc.time_tracker import add_time_entry, stop_focus_timer_and_log
                        
                        # Stop current timer and log time before logging break time
                        stop_focus_timer_and_log(app_data)
                        
                        # Now log the break time from the hourly check-in
                        pending = app_data.get("pending_checkin", {})
                        if pending:
                            # Calculate actual time since timer started
                            timer_start_ts = pending.get("timer_start_ts")
                            if timer_start_ts:
                                actual_work_seconds = int(datetime.now().timestamp() - timer_start_ts)
                            else:
                                # Fallback - shouldn't happen with new system
                                actual_work_seconds = pending.get("duration_seconds", 3600)
                            
                            add_time_entry(app_data, entry_type="break", subtask=None, seconds=actual_work_seconds)
                            data_manager.save_data(app_data)
                        
                        app_data["pending_checkin"] = None
                        data_manager.save_data(app_data)
                    
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""
                    request_full_redraw = True
                
                elif key in ['I', 'i']:
                    # Ignore this check-in
                    with data_lock:
                        app_data["pending_checkin"] = None
                        data_manager.save_data(app_data)
                    
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""
                    request_full_redraw = True
                
                elif key == curses.KEY_UP and selected_checkin_task_index > 0:
                    selected_checkin_task_index -= 1
                    request_full_redraw = True
                
                elif key == curses.KEY_DOWN:
                    # Get available tasks for bounds checking
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
                
                elif key in ['\n', curses.KEY_ENTER]:
                    if selected_checkin_task_index >= 0:
                        # User confirmed selection of a task
                        available_tasks = []
                        with data_lock:
                            from inc.time_tracker import add_time_entry, start_focus_timer
                            
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
                                    # Calculate actual time worked since timer started
                                    timer_start_ts = pending.get("timer_start_ts")
                                    if timer_start_ts:
                                        actual_work_seconds = int(datetime.now().timestamp() - timer_start_ts)
                                    else:
                                        # Fallback - shouldn't happen with new system
                                        actual_work_seconds = pending.get("duration_seconds", 3600)
                                    
                                    # Use the actual subtask identifier, which will be normalized by add_time_entry
                                    add_time_entry(app_data, entry_type="task", subtask=selected_subtask, seconds=actual_work_seconds)
                                    data_manager.save_data(app_data)
                                    
                                    # Update focus to the selected subtask and start timer
                                    app_data["focused_ticket"] = selected_ticket
                                    app_data["focused_subtask"] = selected_subtask
                                    start_focus_timer(app_data)
                                
                                app_data["pending_checkin"] = None
                                data_manager.save_data(app_data)
                    else:
                        # User pressed Enter without selecting a task - cancel the check-in
                        with data_lock:
                            app_data["pending_checkin"] = None
                            data_manager.save_data(app_data)
                    
                    current_view = VIEW_MAIN
                    selected_checkin_task_index = -1
                    command_buffer = ""
                    request_full_redraw = True
        
        # Fetch current meetings from calendar poller
        with external_meetings_lock:
            external_meetings[:] = calendar_poller.get_meetings()
        
        # Render UI with error handling to prevent crashes
        if user_activity_caused_draw_this_cycle or request_full_redraw or \
           (current_time - last_clock_refresh_time >= clock_refresh_interval):
            
            try:
                display_ui(stdscr, app_data, command_buffer, request_full_redraw, selected_subtask_index, 
                          current_view, entity_for_dedicated_notes, current_ticket_subtask_list_visible, 
                          show_help_footer, current_date_for_daily_notes, selected_note_index, 
                          jira_cache, jira_cache_lock, notes_scroll_offset, selected_checkin_task_index)
            except curses.error as e:
                # Handle curses errors gracefully - usually from window resize
                request_full_redraw = True
                try:
                    stdscr.clear()
                    stdscr.refresh()
                except curses.error:
                    pass
            
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
