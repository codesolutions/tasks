import curses
from datetime import datetime, date, timedelta
from inc.helpers import t
from inc.views.base_view import format_timedelta_minutes

def display_time_log_view(stdscr, data, current_date_for_log):
    height, width = stdscr.getmaxyx()
    stdscr.clear()

    row = 0
    stdscr.addstr(row, 0, t('ui_clock', now_time_str=datetime.now().strftime("%H:%M:%S")))
    row += 1

    date_str_iso = current_date_for_log.isoformat()
    weekday_str = t('weekdays')[current_date_for_log.weekday()]
    title = f"Time Log for {weekday_str}, {date_str_iso}"
    stdscr.addstr(row, 0, title)
    stdscr.addstr(row, width - 20, "< Left | Right >")
    row += 2

    # Get time log entries for this date (new format)
    time_entries = data.get("time_log", {}).get(date_str_iso, [])
    
    # Calculate totals by type and subtask
    total_task_seconds = 0
    total_break_seconds = 0
    total_meeting_seconds = 0
    subtask_totals = {}
    
    for entry in time_entries:
        entry_type = entry.get("type", "task")
        subtask = entry.get("subtask", "Unknown")
        seconds = entry.get("seconds", 0)
        
        if entry_type == "task":
            total_task_seconds += seconds
            if subtask:
                subtask_totals[subtask] = subtask_totals.get(subtask, 0) + seconds
        elif entry_type == "break":
            total_break_seconds += seconds
        elif entry_type == "meeting":
            total_meeting_seconds += seconds
    
    total_seconds = total_task_seconds + total_break_seconds + total_meeting_seconds
    
    # Display totals
    if total_seconds > 0:
        stdscr.addstr(row, 0, f"Total logged time: {format_timedelta_minutes(timedelta(seconds=total_seconds))}")
        row += 1
        
        if total_task_seconds > 0:
            stdscr.addstr(row, 2, f"• Tasks: {format_timedelta_minutes(timedelta(seconds=total_task_seconds))}")
            row += 1
        if total_meeting_seconds > 0:
            stdscr.addstr(row, 2, f"• Meetings: {format_timedelta_minutes(timedelta(seconds=total_meeting_seconds))}")
            row += 1
        if total_break_seconds > 0:
            stdscr.addstr(row, 2, f"• Breaks: {format_timedelta_minutes(timedelta(seconds=total_break_seconds))}")
            row += 1
        row += 1
    else:
        stdscr.addstr(row, 0, "No time logged for this day.")
        row += 2

    # Display subtask breakdown
    if subtask_totals:
        stdscr.addstr(row, 0, "Task Breakdown:")
        row += 1
        
        # Sort by time spent (descending)
        sorted_subtasks = sorted(subtask_totals.items(), key=lambda x: x[1], reverse=True)
        
        for subtask, seconds in sorted_subtasks:
            if row >= height - 3:  # Leave space for footer
                stdscr.addstr(row, 2, "... (more entries)")
                break
            time_str = format_timedelta_minutes(timedelta(seconds=seconds))
            
            # Extract clean display name from subtask identifier
            display_name = subtask
            if subtask.startswith('[') and '] ' in subtask:
                # Format: '[ProjectName] TICKET-123' -> show as is
                display_name = subtask
            elif 'browse/' in subtask:
                # Old URL format - extract just the ticket ID
                ticket_id = subtask.split('browse/')[-1]
                project_match = subtask.split('/browse/')[0].split('/')[-1] if '/' in subtask else None
                if project_match:
                    display_name = f"[{project_match}] {ticket_id}"
                else:
                    display_name = ticket_id
            
            # Truncate if too long
            display_name = display_name[:width-20] if len(display_name) > width-20 else display_name
            stdscr.addstr(row, 2, f"• {display_name}: {time_str}")
            row += 1
    
    # Show work session status
    work_session = data.get("work_session", {})
    if work_session.get("active") and current_date_for_log == date.today():
        row += 1
        stdscr.addstr(row, 0, "Work session is active", curses.color_pair(3))
        
        current_timer_start = work_session.get("current_timer_start_ts")
        if current_timer_start:
            elapsed = datetime.now().timestamp() - current_timer_start
            elapsed_str = format_timedelta_minutes(timedelta(seconds=int(elapsed)))
            stdscr.addstr(row, 25, f"(current: {elapsed_str})")
        row += 1

    stdscr.addstr(height - 1, 2, "Press ESC to return to main view | Left/Right to navigate dates")
    stdscr.refresh()
