"""
Daily summary view for displaying time tracking summaries.

This module provides functionality to display comprehensive time tracking 
summaries when the work day ends, either manually or automatically.
"""

import curses
from datetime import datetime, date, timedelta
from inc.helpers import t
from inc.views.base_view import format_timedelta_minutes
from inc.time_tracker import get_total_seconds_for_date

def display_daily_summary_view(stdscr, data, target_date=None):
    """
    Display a comprehensive daily summary of tracked time.
    
    Args:
        stdscr: The curses screen object
        data: Application data dictionary
        target_date: Date to show summary for (defaults to today)
    
    Returns:
        None
    """
    height, width = stdscr.getmaxyx()
    stdscr.clear()
    
    if target_date is None:
        target_date = date.today()
    
    date_iso = target_date.isoformat()
    time_log = data.get("time_log", {}).get(date_iso, [])
    
    if not time_log:
        stdscr.addstr(1, 2, f"📊 Daily Summary for {target_date.strftime('%A, %B %d, %Y')}")
        stdscr.addstr(3, 4, "No time entries found for this day.")
        stdscr.addstr(height - 1, 2, "Press any key to continue...")
        stdscr.refresh()
        return
    
    row = 1
    stdscr.addstr(row, 2, f"📊 Daily Summary for {target_date.strftime('%A, %B %d, %Y')}")
    row += 2
    
    # Calculate totals by category
    task_entries = []
    break_entries = []
    meeting_entries = []
    total_task_seconds = 0
    total_break_seconds = 0
    total_meeting_seconds = 0
    
    for entry in time_log:
        entry_type = entry.get("type", "task")
        seconds = entry.get("seconds", 0)
        subtask = entry.get("subtask", "Unknown")
        comment = entry.get("comment", "")
        
        if entry_type == "task":
            task_entries.append((subtask, seconds, comment))
            total_task_seconds += seconds
        elif entry_type == "break":
            break_entries.append((seconds, comment))
            total_break_seconds += seconds
        elif entry_type == "meeting":
            meeting_entries.append((seconds, comment))
            total_meeting_seconds += seconds
    
    # Display work summary
    if task_entries:
        total_task_time = format_timedelta_minutes(timedelta(seconds=total_task_seconds))
        stdscr.addstr(row, 2, f"🚀 Work Time: {total_task_time}")
        row += 1
        
        # Group by subtask and sum time
        subtask_totals = {}
        for subtask, seconds, comment in task_entries:
            if subtask in subtask_totals:
                subtask_totals[subtask]['seconds'] += seconds
                if comment and comment not in subtask_totals[subtask]['comments']:
                    subtask_totals[subtask]['comments'].append(comment)
            else:
                subtask_totals[subtask] = {
                    'seconds': seconds,
                    'comments': [comment] if comment else []
                }
        
        # Sort by time spent (descending)
        sorted_subtasks = sorted(subtask_totals.items(), key=lambda x: x[1]['seconds'], reverse=True)
        
        for subtask, info in sorted_subtasks[:min(10, height-row-8)]:  # Show top 10 or fit screen
            time_spent = format_timedelta_minutes(timedelta(seconds=info['seconds']))
            # Clean up subtask display
            display_name = subtask
            if subtask.startswith('[') and '] ' in subtask:
                display_name = subtask.split('] ', 1)[1]
                if 'browse/' in display_name:
                    display_name = display_name.split('browse/')[-1]
            
            stdscr.addstr(row, 4, f"• {display_name}: {time_spent}")
            row += 1
            
            # Show comments if any and space permits
            if info['comments'] and row < height - 6:
                for comment in info['comments'][:2]:  # Show max 2 comments per task
                    if comment.strip():
                        comment_text = comment[:width-12] if len(comment) > width-12 else comment
                        stdscr.addstr(row, 6, f"💭 {comment_text}")
                        row += 1
        
        row += 1
    
    # Display break time if any
    if break_entries:
        total_break_time = format_timedelta_minutes(timedelta(seconds=total_break_seconds))
        stdscr.addstr(row, 2, f"☕ Break Time: {total_break_time}")
        row += 1
        
        # Show break comments if any
        break_comments = [comment for _, comment in break_entries if comment.strip()]
        if break_comments and row < height - 4:
            for comment in break_comments[:3]:  # Show max 3 break comments
                comment_text = comment[:width-10] if len(comment) > width-10 else comment
                stdscr.addstr(row, 4, f"💭 {comment_text}")
                row += 1
        row += 1
    
    # Display meeting time if any
    if meeting_entries:
        total_meeting_time = format_timedelta_minutes(timedelta(seconds=total_meeting_seconds))
        stdscr.addstr(row, 2, f"👥 Meeting Time: {total_meeting_time}")
        row += 1
        
        # Show meeting comments if any
        meeting_comments = [comment for _, comment in meeting_entries if comment.strip()]
        if meeting_comments and row < height - 4:
            for comment in meeting_comments[:3]:  # Show max 3 meeting comments
                comment_text = comment[:width-10] if len(comment) > width-10 else comment
                stdscr.addstr(row, 4, f"💭 {comment_text}")
                row += 1
        row += 1
    
    # Display overall total
    overall_total_seconds = total_task_seconds + total_break_seconds + total_meeting_seconds
    if overall_total_seconds > 0:
        overall_total_time = format_timedelta_minutes(timedelta(seconds=overall_total_seconds))
        stdscr.addstr(row, 2, f"⏱️  Total Time Logged: {overall_total_time}")
        row += 2
    
    # Show productivity insights
    if total_task_seconds > 0 and overall_total_seconds > 0:
        productivity_ratio = (total_task_seconds / overall_total_seconds) * 100
        stdscr.addstr(row, 2, f"📈 Productivity: {productivity_ratio:.1f}% work time")
        row += 1
    
    # Footer
    stdscr.addstr(height - 1, 2, "Press any key to continue...")
    stdscr.refresh()


def show_daily_summary(stdscr, data, target_date=None, auto_end=False):
    """
    Show daily summary and wait for user input.
    
    Args:
        stdscr: The curses screen object
        data: Application data dictionary
        target_date: Date to show summary for (defaults to today)
        auto_end: Whether this summary was triggered by automatic end-of-day
    
    Returns:
        None
    """
    # Add a header message if this was an automatic end
    if auto_end:
        height, width = stdscr.getmaxyx()
        stdscr.clear()
        stdscr.addstr(1, 2, "🌙 Work day automatically ended due to inactivity")
        stdscr.addstr(2, 2, "Here's your daily summary:")
        stdscr.addstr(4, 2, "Press any key to view summary...")
        stdscr.refresh()
        
        # Wait for user acknowledgment - disable nodelay to ensure we wait
        try:
            stdscr.nodelay(False)
            stdscr.getch()
            stdscr.nodelay(True)
        except curses.error:
            try:
                stdscr.nodelay(True)
            except curses.error:
                pass
    
    # Show the actual summary
    display_daily_summary_view(stdscr, data, target_date)
    
    # Wait for user to dismiss - disable nodelay to ensure we wait
    try:
        stdscr.nodelay(False)  # Ensure we wait for user input
        stdscr.getch()
        stdscr.nodelay(True)   # Restore nodelay mode
    except curses.error:
        try:
            stdscr.nodelay(True)  # Restore nodelay mode even on error
        except curses.error:
            pass
