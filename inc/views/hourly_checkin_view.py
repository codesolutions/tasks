import curses
from datetime import datetime, timedelta
from inc.helpers import t
from inc.views.base_view import format_timedelta_minutes

def display_hourly_checkin_view(stdscr, data, selected_task_index=-1):
    height, width = stdscr.getmaxyx()
    stdscr.clear()
    
    pending_checkin = data.get("pending_checkin", {})
    if not pending_checkin:
        # No pending check-in, show error and return
        stdscr.addstr(1, 2, "No pending check-in found.")
        stdscr.addstr(2, 2, "Press ESC to return.")
        stdscr.refresh()
        return
    
    duration_seconds = pending_checkin.get("duration_seconds", 3600)
    suggested_subtask = pending_checkin.get("suggested_subtask")
    
    now = datetime.now()
    duration_str = format_timedelta_minutes(timedelta(seconds=duration_seconds))
    
    row = 1
    stdscr.addstr(row, 2, f"Time for your hourly check-in! ({now.strftime('%H:%M')})")
    row += 1
    stdscr.addstr(row, 2, f"Please account for the last {duration_str}.")
    row += 2

    if suggested_subtask:
        stdscr.addstr(row, 2, "Were you working on your focused subtask?")
        row += 1
        stdscr.addstr(row, 4, f"{suggested_subtask}")
        row += 2
        stdscr.addstr(row, 2, f"[Y] Yes, log {duration_str} to this task.")
        row += 1
        stdscr.addstr(row, 2, "[S] No, I worked on something else.")
        row += 1
    else:
        stdscr.addstr(row, 2, "What were you working on?")
        row += 1
        stdscr.addstr(row, 2, "[S] Select a task to log time.")
        row += 1

    stdscr.addstr(row, 2, "[B] I was on a break / in a meeting.")
    row += 1
    stdscr.addstr(row, 2, "[I] Ignore this check-in.")
    row += 2
    
    # If user selected 'S', show task selection
    if selected_task_index >= 0:
        stdscr.addstr(row, 2, "Select a task to log time to:")
        row += 1
        
        # Get all available subtasks from current and paused tasks
        available_tasks = []
        display_names = []
        current_ticket = data.get("current_ticket")
        if current_ticket:
            subtasks = data.get("sub_tasks", {}).get(current_ticket, {})
            for sub_name, sub_details in subtasks.items():
                if isinstance(sub_details, dict) and sub_details.get("status") != "hidden":
                    # Store actual task tuple for processing
                    available_tasks.append((current_ticket, sub_name))
                    # Clean display name from URL format
                    display_name = sub_name
                    if 'browse/' in sub_name:
                        ticket_id = sub_name.split('browse/')[-1]
                        display_name = f"[{current_ticket}] {ticket_id}"
                    else:
                        display_name = f"[{current_ticket}] {sub_name}"
                    display_names.append(display_name)
        
        # Add paused tasks
        for paused_task in data.get("paused_tasks", []):
            ticket_name = paused_task.get("ticket")
            if ticket_name:
                subtasks = paused_task.get("sub_tasks", {})
                for sub_name, sub_details in subtasks.items():
                    if isinstance(sub_details, dict) and sub_details.get("status") != "hidden":
                        # Store actual task tuple for processing
                        available_tasks.append((ticket_name, sub_name))
                        # Clean display name from URL format
                        display_name = sub_name
                        if 'browse/' in sub_name:
                            ticket_id = sub_name.split('browse/')[-1]
                            display_name = f"[{ticket_name}] {ticket_id}"
                        else:
                            display_name = f"[{ticket_name}] {sub_name}"
                        display_names.append(display_name)
        
        if not available_tasks:
            stdscr.addstr(row, 4, "No tasks available.")
            row += 1
        else:
            # Display tasks with selection
            for i, display_name in enumerate(display_names[:min(10, height-row-5)]):  # Limit based on screen space
                try:
                    if i == selected_task_index:
                        # Highlight selected task
                        stdscr.addstr(row, 4, f"▶ {display_name[:width-10]}")
                    else:
                        stdscr.addstr(row, 4, f"  {display_name[:width-10]}")
                    row += 1
                except curses.error:
                    break  # Screen too small
            
            row += 1
            stdscr.addstr(row, 2, f"Use ↑↓ arrows to navigate, ENTER to select ({selected_task_index + 1}/{len(available_tasks)})")
        
        row += 1
    
    stdscr.addstr(height - 1, 2, "Press ESC to cancel this check-in.")
    stdscr.refresh()
    
    return available_tasks if selected_task_index >= 0 else []
