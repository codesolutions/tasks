import re
import time
import copy
from datetime import datetime, timedelta, date
from config_manager import t
from jira_api import fetch_and_cache_jira_data
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
    current_project = data.get("current_ticket")

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

    elif command == 'x':
        if current_project:
            if current_project not in data.get("completed_tickets", []):
                data.setdefault("completed_tickets", []).append(current_project)
            if data.get("focused_ticket") == current_project:
                data["focused_ticket"] = None
                data["focused_subtask"] = None
            data["current_ticket"] = None
            if "task_start_time" in data:
                del data["task_start_time"]
            data_was_modified = True
            show_notification(stdscr, t('cmd_info_task_completed_and_hidden', name=current_project))
        else:
            show_notification(stdscr, t('cmd_err_no_active_task_to_complete'))

    elif command == 'f':
        if current_project and selected_subtask_idx != -1 and \
           0 <= selected_subtask_idx < len(current_ticket_subtask_list):
            sub_task_name, sub_task_details = current_ticket_subtask_list[selected_subtask_idx]
            is_currently_focused = sub_task_details.get("focused", False)

            # Unfocus all other subtasks in the current ticket
            for st_name, st_details in data["sub_tasks"][current_project].items():
                st_details["focused"] = False

            # Toggle focus for the selected subtask
            data["sub_tasks"][current_project][sub_task_name]["focused"] = not is_currently_focused

            if not is_currently_focused: # If it's now focused
                data["focused_ticket"] = current_project
                data["focused_subtask"] = sub_task_name
                show_notification(stdscr, t('cmd_info_subtask_focus_set', name=sub_task_name))
            else: # If it's now unfocused
                data["focused_ticket"] = None
                data["focused_subtask"] = None
                show_notification(stdscr, t('cmd_info_focus_cleared'))

            data_was_modified = True
        else:
            show_notification(stdscr, t('cmd_prompt_select_subtask_for_focus'))

    elif command == 'pr':
        if current_project and selected_subtask_idx != -1 and \
           0 <= selected_subtask_idx < len(current_ticket_subtask_list):
            if len(command_parts) > 1:
                pr_url = " ".join(command_parts[1:])
                sub_task_to_modify_name, _ = current_ticket_subtask_list[selected_subtask_idx]
                if current_project in data.get("sub_tasks", {}) and \
                   sub_task_to_modify_name in data["sub_tasks"][current_project]:
                    data["sub_tasks"][current_project][sub_task_to_modify_name]["pr_url"] = pr_url
                    data["sub_tasks"][current_project][sub_task_to_modify_name]["pr_status"] = None # Reset status
                    data_was_modified = True
                    show_notification(stdscr, t('cmd_info_pr_added', name=sub_task_to_modify_name))
                else:
                    show_notification(stdscr, t('cmd_err_subtask_not_found'))
            else:
                show_notification(stdscr, t('cmd_usage_add_pr'))
        else:
            show_notification(stdscr, t('cmd_prompt_select_subtask_for_pr'))

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
                return data, data_was_modified

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
                        return data, data_was_modified

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
        if not current_project:
            show_notification(stdscr, t('cmd_err_no_active_task_for_note'))
        if len(command_parts) > 1:
            note_text_cmd = " ".join(command_parts[1:])
            if selected_subtask_idx != -1 and 0 <= selected_subtask_idx < len(current_ticket_subtask_list):
                selected_sub_task_name_cmd, _ = current_ticket_subtask_list[selected_subtask_idx]
                if current_project in data.get("sub_tasks", {}):
                    sub_task_details_cmd = data["sub_tasks"][current_project].get(selected_sub_task_name_cmd)
                    if sub_task_details_cmd and isinstance(sub_task_details_cmd, dict):
                        sub_task_details_cmd.setdefault("notes", []).append(note_text_cmd)
                        data_was_modified = True
                        show_notification(stdscr, t('cmd_info_note_added_to_subtask', name=selected_sub_task_name_cmd))
                    else: show_notification(stdscr, t('cmd_err_subtask_details_not_found', name=selected_sub_task_name_cmd))
                else: show_notification(stdscr, t('cmd_err_main_task_details_not_found', name=current_project))
            else:
                data.setdefault("notes", {}).setdefault(current_project, []).append(note_text_cmd)
                data_was_modified = True
                show_notification(stdscr, t('cmd_info_note_added_to_task', name=current_project))
        else: show_notification(stdscr, t('cmd_usage_add_note'))

    elif command == 'p' or command == 'k':
        event_type = 'meeting' if command == 'p' else 'interruption'
        usage_msg = t('cmd_usage_add_meeting_event', command=command)
        if len(command_parts) < 3:
            show_notification(stdscr, usage_msg)
            return data, data_was_modified
        arg1 = command_parts[1].lower()
        is_recurring = arg1 in WEEKDAY_MAP
        if is_recurring:
            if len(command_parts) < 4:
                 show_notification(stdscr, usage_msg)
                 return data, data_was_modified
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

    elif len(command_parts) > 0 :
        identifier = " ".join(command_parts)
        target_ticket_name_to_activate = None

        try:
            target_idx_1_based = int(identifier)
            if 1 <= target_idx_1_based <= len(all_displayable_tickets_for_cmd):
                target_ticket_name_to_activate = all_displayable_tickets_for_cmd[target_idx_1_based - 1]
            else:
                show_notification(stdscr, t('cmd_err_invalid_index', index=target_idx_1_based))
                return data, data_was_modified
        except ValueError:
            matches = []
            for t_name in all_displayable_tickets_for_cmd:
                if identifier.lower() in t_name.lower():
                    matches.append(t_name)
            if len(matches) == 0:
                show_notification(stdscr, t('cmd_err_unknown_command_or_ticket', id=identifier))
                return data, data_was_modified
            elif len(matches) == 1:
                target_ticket_name_to_activate = matches[0]
            else:
                options_str = ", ".join([f"'{name}'" for name in matches[:3]])
                if len(matches) > 3: options_str += "..."
                show_notification(stdscr, t('cmd_err_multiple_tickets_found', options=options_str))
                return data, data_was_modified

        if target_ticket_name_to_activate:
            if data.get("current_ticket") == target_ticket_name_to_activate:
                show_notification(stdscr, t('cmd_err_task_already_active', name=target_ticket_name_to_activate))
                return data, data_was_modified
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

    return data, data_was_modified
