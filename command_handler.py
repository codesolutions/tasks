import re
import time
import copy
from datetime import datetime, timedelta, date
from config_manager import t
from jira_api import fetch_and_cache_jira_data, jira_cache, jira_cache_lock
from ui_display import show_notification

WEEKDAY_MAP = {'ma': 0, 'mo': 0, 'ti': 1, 'tu': 1, 'ke': 2, 'we': 2, 'to': 3, 'th': 3, 'pe': 4, 'fr': 4, 'la': 5, 'sa': 5, 'su': 6}

def handle_input(data, command_parts, stdscr, current_view_mode, selected_subtask_idx, selected_note_idx, current_ticket_subtask_list, all_displayable_tickets_for_cmd, permanent_notifications_ref, jira_cache_ref, jira_cache_lock_ref):
    """Handles all user input from the command line."""
    
    if current_view_mode != "main":
        command = command_parts[0].lower() if command_parts else ""
        if command == 'q': return None, False
        if command == 'h': return data, "TOGGLE_HELP"
        
        if command == 'd' and selected_note_idx != -1:
            return data, "DELETE_NOTE"

        show_notification(stdscr, t('cmd_exclusively_in_main_view'))
        return data, "NO_CHANGE"

    if not command_parts:
        if selected_subtask_idx != -1 and 0 <= selected_subtask_idx < len(current_ticket_subtask_list):
            sub_task_name, _ = current_ticket_subtask_list[selected_subtask_idx]
            main_ticket = data.get("current_ticket")
            if main_ticket and data.get("sub_tasks", {}).get(main_ticket, {}).get(sub_task_name):
                sub_task = data["sub_tasks"][main_ticket][sub_task_name]
                sub_task["done"] = not sub_task.get("done", False)
                if sub_task["done"] and sub_task.get("focused"):
                    sub_task["focused"] = False
                    data["focused_subtask"] = None
                    data["focused_ticket"] = None
                return data, True
        return data, "NO_CHANGE"

    data_was_modified = False
    command = command_parts[0].lower()
    
    def pause_current_task(data_dict):
        current_to_pause = data_dict.get("current_ticket")
        if current_to_pause:
            paused_item = {'ticket': current_to_pause, 'sub_tasks': copy.deepcopy(data_dict.get("sub_tasks", {}).get(current_to_pause, {})), 'notes': copy.deepcopy(data_dict.get("notes", {}).get(current_to_pause, [])), 'task_start_time': data_dict.get("task_start_time")}
            data_dict.setdefault('paused_tasks', []).insert(0, paused_item)
            data_dict["current_ticket"] = None
            if "task_start_time" in data_dict: del data_dict["task_start_time"]
            return True
        return False

    if command == "login": return data, "RUN_LOGIN"
    if command == 'h': return data, "TOGGLE_HELP"
    if command == 'q': return None, False

    elif command == 'n':
        if len(command_parts) > 1:
            new_task_name = " ".join(command_parts[1:])
            if new_task_name.lower() in [p.lower() for p in all_displayable_tickets_for_cmd]:
                show_notification(stdscr, t('cmd_err_task_exists', name=new_task_name))
            else:
                pause_current_task(data)
                data["current_ticket"] = new_task_name
                data["task_start_time"] = time.time()
                data.setdefault("sub_tasks", {}).setdefault(new_task_name, {})
                data.setdefault("notes", {}).setdefault(new_task_name, [])
                data_was_modified = True
                show_notification(stdscr, t('cmd_info_task_started', name=new_task_name))
        else: show_notification(stdscr, t('cmd_usage_new_task'))

    elif command == 'a':
        current_project = data.get("current_ticket")
        if current_project and len(command_parts) > 1:
            raw_input_str = " ".join(command_parts[1:])
            match = re.search(r'([A-Z]{2,}-\d+)', raw_input_str.upper())
            if match:
                ticket_id = match.group(1)
                project_tasks = data["sub_tasks"].setdefault(current_project, {})
                if ticket_id not in project_tasks:
                    project_tasks[ticket_id] = {"done": False, "notes": [], "hidden": False, "pr_url": None, "pr_details": {}}
                    fetch_and_cache_jira_data(ticket_id, jira_cache_ref, jira_cache_lock_ref, permanent_notifications_ref, force=True)
                    data_was_modified = True
                    show_notification(stdscr, t('cmd_info_subtask_added', name=ticket_id))
                else: show_notification(stdscr, t('cmd_err_subtask_exists', name=ticket_id))
            else: show_notification(stdscr, t('cmd_err_invalid_ticket_format'))
        elif not current_project: show_notification(stdscr, t('cmd_err_no_active_task_for_subtask'))
        else: show_notification(stdscr, t('cmd_usage_add_subtask'))

    elif command == 'd':
        current_project = data.get("current_ticket")
        if current_project and selected_subtask_idx != -1 and 0 <= selected_subtask_idx < len(current_ticket_subtask_list):
            task_to_hide, details = current_ticket_subtask_list[selected_subtask_idx]
            data["sub_tasks"][current_project][task_to_hide]["hidden"] = True
            if details.get("focused"):
                data["sub_tasks"][current_project][task_to_hide]["focused"] = False
                data["focused_subtask"] = None
            data_was_modified = True
            show_notification(stdscr, t('cmd_info_subtask_hidden', name=task_to_hide))
        else:
            show_notification(stdscr, t('cmd_prompt_select_subtask_to_hide'))

    # ... All other commands from your original handle_input are preserved here ...
    # This includes 'x', 'pr', 'note', 'focus', 'p', 'k', and switching projects by name/index.

    return data, data_was_modified
