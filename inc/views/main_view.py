import curses
import time
from datetime import datetime, timedelta, date
from urllib.parse import urlparse, urlunparse
import re
import copy

from inc.helpers import t
import inc.helpers
from inc.views.base_view import _draw_wrapped_text, format_timedelta_minutes
from inc.config_manager import config
from inc.jira import (
    load_jira_cache,
    jira_queue_worker,  # Import the new worker
    jira_request_queue, # Import the queue
    jira_in_flight,     # Import the in-flight tracker
    get_and_save_web_session,  # old
    # jira_data_poller, # old
    config as jira_config
)
from inc.views.dedicated_notes_view import display_dedicated_notes_view
from inc.views.daily_notes_view import display_daily_notes_view
from inc.views.time_log_view import display_time_log_view
from inc.views.hourly_checkin_view import display_hourly_checkin_view

from inc.views.base_view import show_permanent_notification

(COLOR_PAIR_DEFAULT, COLOR_PAIR_REVERSE, COLOR_PAIR_GREY, COLOR_PAIR_PAUSED,
 COLOR_PAIR_SELECTED, COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN, COLOR_PAIR_URGENT_BOX,
 COLOR_PAIR_PR_UNHANDLED, COLOR_PAIR_PR_APPROVED, COLOR_PAIR_FOCUSED,
 COLOR_PAIR_PERMANENT_NOTIFICATION, COLOR_PAIR_STANDOUT, COLOR_PAIR_NEW_COMMENT, COLOR_PAIR_HELP_OVERLAY) = range(1, 16)

VIEW_MAIN = "main"
VIEW_DEDICATED_NOTES = "dedicated_notes"
VIEW_DAILY_NOTES = "daily_notes"
VIEW_TIME_LOG = "time_log"
VIEW_HOURLY_CHECKIN = "hourly_checkin"

def display_main_view(stdscr, data, command_buffer="", full_redraw=False, selected_subtask_idx=-1,
               current_view_mode=VIEW_MAIN, entity_for_dedicated_notes=None,
               current_ticket_subtask_list_for_display_arg=None, show_help_footer=True,
               current_date_for_daily_notes_arg=None, selected_note_idx=-1,
               jira_cache=None, jira_cache_lock=None, reviews_lock=None, external_meetings_lock=None, notes_scroll_offset=0,
               pull_requests_for_review=[], permanent_notifications=[], web_change_notifications=[], external_meetings=[],
               selected_checkin_task_idx=-1):


    if current_view_mode == VIEW_DEDICATED_NOTES:
        return display_dedicated_notes_view(stdscr, data, command_buffer, entity_for_dedicated_notes, show_help_footer, selected_note_idx, jira_cache, jira_cache_lock, notes_scroll_offset)
    if current_view_mode == VIEW_DAILY_NOTES:
        return display_daily_notes_view(stdscr, data, command_buffer, current_date_for_daily_notes_arg, show_help_footer, selected_note_idx)
    if current_view_mode == VIEW_TIME_LOG:
        return display_time_log_view(stdscr, data, current_date_for_daily_notes_arg)
    if current_view_mode == VIEW_HOURLY_CHECKIN:
        return display_hourly_checkin_view(stdscr, data, selected_checkin_task_idx)

    try:
        height, width = stdscr.getmaxyx()
    except curses.error: return False

    # stdscr.bkgd(' ', curses.color_pair(COLOR_PAIR_HELP_OVERLAY))

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

    # To avoid locking frequently, we make a quick copy of the cache for this render pass.
    with jira_cache_lock:
        cache_copy = jira_cache.copy()

    # Define the cache timeout (10 minutes = 600 seconds) as you suggested
    JIRA_CACHE_TIMEOUT = 60
    now = time.time()

    display_right_panel = bool(all_displayable_tickets)
    separator_char = "|"
    effective_main_width = width
    min_main_content_width = 35
    min_panel_item_len = 8
    actual_panel_content_width = 0

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

    from inc.commands.command_registry import get_command_help
    
    help_lines_definitions = {
        "full": get_command_help().split('\n'),
        "hidden": [t('help_hidden_prompt')]
    }
    current_help_lines_list = help_lines_definitions["full"] if show_help_footer else help_lines_definitions["hidden"]
    num_actual_help_lines = len(current_help_lines_list)
    footer_total_height = num_actual_help_lines + 2

    show_permanent_notification(stdscr, permanent_notifications)

    #if full_redraw:
    #    stdscr.clear()

    # Define dimensions for the main content window
    # We place it at y=1 to leave space for the clock at the top
    #main_win_h = height - 1 - footer_total_height
    #main_win_w = effective_main_width

    # Create the new window for the main content
    # We only create the window if there's enough space for it
    #if main_win_h > 2 and main_win_w > 2:
    #    main_win = curses.newwin(main_win_h, main_win_w, 1, 1)
    #    main_win.box() # Draw a border around the new window
    #else:
        # If the screen is too small, we'll draw directly on stdscr as a fallback
    #    main_win = stdscr

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

                        #permanent_notifications_ref.remove(t('jira_login_prompt'))


                        subtasks_for_ticket0 = data.get("sub_tasks", {}).get(ticket_name_line0, {})
                        from inc.integrations.pr_notifications import update_permanent_notifications
                        update_permanent_notifications(permanent_notifications, ticket_name_line0, subtasks_for_ticket0)
                        
                        if any(st.get("pr_status") == 'attention_needed' for st in subtasks_for_ticket0.values() if isinstance(st, dict)):
                            attr_line0 = curses.color_pair(COLOR_PAIR_PR_UNHANDLED)
                        elif any(st.get("pr_status") == 'approved' for st in subtasks_for_ticket0.values() if isinstance(st, dict)):
                            attr_line0 = curses.color_pair(COLOR_PAIR_PR_APPROVED)
                        elif subtasks_for_ticket0 and all(st_details.get("status") == "hidden" for st_details in subtasks_for_ticket0.values() if isinstance(st_details, dict)):
                            attr_line0 = curses.color_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN)
                        elif (subtasks_for_ticket0 and 
                                not any(st_details.get("status") == "todo" for st_details in subtasks_for_ticket0.values() if isinstance(st_details, dict)) and
                                not any(st_details.get("status") == "in_progress" for st_details in subtasks_for_ticket0.values() if isinstance(st_details, dict)) and 
                                any(st_details.get("status") == "done" for st_details in subtasks_for_ticket0.values() if isinstance(st_details, dict))
                        ):
                            attr_line0 = curses.color_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_DONE)
                        elif not subtasks_for_ticket0:
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
                cached_item = cache_copy.get(inc.helpers.get_jira_ticket_from_url(ticket_name_in_panel))

                # Check for PR status for background color and update notifications
                from inc.integrations.pr_notifications import update_permanent_notifications
                update_permanent_notifications(permanent_notifications, ticket_name_in_panel, subtasks_for_this_panel_ticket)
                
                if any(st.get("pr_status") == 'attention_needed' for st in subtasks_for_this_panel_ticket.values() if isinstance(st, dict)):
                    item_attr = curses.color_pair(COLOR_PAIR_PR_UNHANDLED)
                elif any(st.get("pr_status") == 'approved' for st in subtasks_for_this_panel_ticket.values() if isinstance(st, dict)):
                    item_attr = curses.color_pair(COLOR_PAIR_PR_APPROVED)
                elif cached_item and (cached_item.get('new_jira_comment') or cached_item.get('new_trello_comment')):
                    item_attr = curses.color_pair(COLOR_PAIR_NEW_COMMENT)
                elif subtasks_for_this_panel_ticket and all(st_details.get("status") == "hidden" for st_details in subtasks_for_this_panel_ticket.values() if isinstance(st_details, dict)):
                    item_attr = curses.color_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN)
                elif (subtasks_for_this_panel_ticket and 
                        not any(st_details.get("status") == "todo" for st_details in subtasks_for_this_panel_ticket.values() if isinstance(st_details, dict)) and
                        not any(st_details.get("status") == "in_progress" for st_details in subtasks_for_this_panel_ticket.values() if isinstance(st_details, dict)) and 
                        any(st_details.get("status") == "done" for st_details in subtasks_for_this_panel_ticket.values() if isinstance(st_details, dict))
                ):
                    item_attr = curses.color_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_DONE)
                
                elif not subtasks_for_this_panel_ticket:
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

    row = 0
    if effective_main_width > 0 :
        stdscr.addstr(row, 0, t('ui_clock', now_time_str=now_time_str)[:effective_main_width])
    row += 1

    content_height_val = height - (row + 1) - footer_total_height # +1 for the separator
    if content_height_val < 0: content_height_val = 0
    content_height_obj = [content_height_val]

    focused_ticket = data.get("focused_ticket")
    focused_subtask = data.get("focused_subtask")
    if focused_ticket:
        if effective_main_width > 0:
            focus_text = t('ui_focused_task_prefix', name=focused_ticket)
            if focused_subtask:
                focus_text += f" / {focused_subtask}"
            lines_used = _draw_wrapped_text(stdscr, focus_text, row, 0, effective_main_width, effective_main_width, content_height_obj, attr=curses.color_pair(COLOR_PAIR_FOCUSED) | curses.A_BOLD)
            row += lines_used

    if web_change_notifications:
        if effective_main_width > 0:
            header_text = t('ui_web_changes_header')
            lines_used = _draw_wrapped_text(stdscr, header_text, row, 0, effective_main_width, effective_main_width, content_height_obj, attr=curses.color_pair(COLOR_PAIR_URGENT_BOX))
            row += lines_used
        for i, notification in enumerate(web_change_notifications):
            if content_height_obj[0] <= 0: break
            lines_used = _draw_wrapped_text(stdscr, f"{i+1}. {notification}", row, 0, effective_main_width, effective_main_width, content_height_obj, prefix="", attr=curses.color_pair(COLOR_PAIR_URGENT_BOX))
            row += lines_used

    with reviews_lock:
        if pull_requests_for_review:
            if effective_main_width > 0:
                header_text = t('ui_reviews_header')
                lines_used = _draw_wrapped_text(stdscr, header_text, row, 0, effective_main_width, effective_main_width, content_height_obj, attr=curses.color_pair(COLOR_PAIR_URGENT_BOX))
                row += lines_used
            for pr in pull_requests_for_review:
                if content_height_obj[0] <= 0: break
                repo_name = f"{pr['toRef']['repository']['project']['key']}/{pr['toRef']['repository']['name']}"
                line1 = f" ** {pr['title']} ** "
                lines_used = _draw_wrapped_text(stdscr, line1, row, 0, effective_main_width, effective_main_width, content_height_obj, prefix="", attr=curses.color_pair(COLOR_PAIR_URGENT_BOX))
                row += lines_used
                if content_height_obj[0] <= 0: break
                line2 = f" {pr['links']['self'][0]['href']}"
                lines_used = _draw_wrapped_text(stdscr, line2, row, 0, effective_main_width, effective_main_width, content_height_obj, prefix="", attr=curses.color_pair(COLOR_PAIR_URGENT_BOX))
                row += lines_used

    #jira_box_lines = read_jira_box_content(max_lines=10)
    #if jira_box_lines:
    #    for line in jira_box_lines:
    #        if content_height_obj[0] <= 0: break
    #        lines_used = _draw_wrapped_text(stdscr, line, row, 0, effective_main_width, effective_main_width, content_height_obj, attr=curses.color_pair(COLOR_PAIR_URGENT_BOX))
    #        row += lines_used

    #or jira_box_lines
    if pull_requests_for_review  or web_change_notifications:
        if effective_main_width > 0:
            lines_used = _draw_wrapped_text(stdscr, "---", row, 0, effective_main_width, effective_main_width, content_height_obj)
            row += lines_used

    initial_content_start_row = row
    if effective_main_width > 0:
        stdscr.addstr(row, 0, "-" * effective_main_width)
        initial_content_start_row +=1
    row = initial_content_start_row

    content_height_obj = [height - initial_content_start_row - footer_total_height]
    current_ticket = data.get("current_ticket")

    if current_ticket:
        # Add work session status and daily total
        work_session = data.get("work_session", {})
        today_str = date.today().isoformat()
        today_time_entries = data.get("time_log", {}).get(today_str, [])
        
        # Calculate today's total time
        today_total_seconds = sum(entry.get("seconds", 0) for entry in today_time_entries if entry.get("type") == "task")
        today_total_str = format_timedelta_minutes(timedelta(seconds=today_total_seconds)) if today_total_seconds > 0 else "0m"
        
        # Work session status indicator
        work_status = ""
        if work_session.get("active"):
            current_timer_start = work_session.get("current_timer_start_ts")
            if current_timer_start:
                elapsed = datetime.now().timestamp() - current_timer_start
                elapsed_str = format_timedelta_minutes(timedelta(seconds=int(elapsed)))
                work_status = f" [⏱️  {elapsed_str}]"
            else:
                work_status = " [⏱️ active]"
        
        base_text = t('ui_current_task_prefix')
        time_info = f" (today: {today_total_str}{work_status})"
        
        if content_height_obj[0] > 0 and effective_main_width > 0:
            available_width_for_ticket_name = effective_main_width - len(base_text) - len(time_info) - 1
            if available_width_for_ticket_name < 0: available_width_for_ticket_name = 0
            ticket_display_name = current_ticket[:available_width_for_ticket_name]
            full_ticket_line = f"{base_text}{ticket_display_name}{time_info}"
            stdscr.addstr(row, 0, full_ticket_line[:effective_main_width])
            row += 1; content_height_obj[0] -= 1

        subtask_list_to_use = current_ticket_subtask_list_for_display_arg
        if subtask_list_to_use is None:
            subtasks_dict = data.get("sub_tasks", {}).get(current_ticket, {})
            show_hidden = data.get("show_hidden_tasks", False)
            # Filter out hidden subtasks for display
            subtask_list_to_use = [(name, details) for name, details in subtasks_dict.items() if isinstance(details, dict) and (show_hidden or not details.get("status") == "hidden")]


        if subtask_list_to_use:
            if content_height_obj[0] > 0 and effective_main_width > 2:
                stdscr.addstr(row, 2, t('ui_subtasks_header')[:effective_main_width-2])
                row += 1; content_height_obj[0] -= 1

            for i, (sub_task_name, sub_task_details_obj) in enumerate(subtask_list_to_use):
                if content_height_obj[0] <= 0: break
                if effective_main_width <= 4: break

                jira_ticket_id = inc.helpers.get_jira_ticket_from_url(sub_task_name)
                cached_item = cache_copy.get(jira_ticket_id)
                status = sub_task_details_obj.get("status", "todo")
                status_char = ""
                if status == "focused":
                    status_char = "‼️"
                elif status == "done":
                    status_char = "✅"
                elif status == "in_progress":
                    status_char = "🚧"
                elif status == "hidden":
                    status_char = "🙈"
                else:
                    status_char = "[ ]"

                display_text = jira_ticket_id
                
                # Calculate time logged for this subtask today
                subtask_total_seconds = sum(
                    entry.get("seconds", 0) for entry in today_time_entries 
                    if entry.get("type") == "task" and entry.get("subtask") == jira_ticket_id
                )
                if subtask_total_seconds > 0:
                    time_str = format_timedelta_minutes(timedelta(seconds=subtask_total_seconds))
                    display_text += f" ({time_str})"
                
                item_attr = curses.color_pair(COLOR_PAIR_DEFAULT)

                if jira_ticket_id != sub_task_name:
                    cached_item = cache_copy.get(jira_ticket_id)
                    should_fetch = not cached_item or (now - cached_item.get('timestamp', 0)) > JIRA_CACHE_TIMEOUT

                    if should_fetch and jira_ticket_id not in jira_in_flight:
                        jira_in_flight.add(jira_ticket_id)
                        jira_request_queue.put(jira_ticket_id)

                    if cached_item:
                        status = cached_item.get('data', {}).get('fields', {}).get('status', {}).get('name', 'N/A')
                        display_text += f" [{status}]"

                pr_status = sub_task_details_obj.get("pr_status")
                
                # Check PR status from v2 schema first if available
                pr_details_v2 = sub_task_details_obj.get("pr_details", {})
                if pr_details_v2 and pr_details_v2.get('version') == 2:
                    pr_meta = pr_details_v2.get('meta', {})
                    if pr_meta.get('state') == 'MERGED':
                        item_attr = curses.color_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_DONE)  # Green for merged
                    elif pr_status == 'attention_needed':
                        item_attr = curses.color_pair(COLOR_PAIR_PR_UNHANDLED)  # Red for attention needed
                    elif pr_status == 'approved':
                        item_attr = curses.color_pair(COLOR_PAIR_PR_APPROVED)  # Green for approved
                    # Fall through to other conditions if no PR status
                elif pr_status == 'attention_needed':
                    item_attr = curses.color_pair(COLOR_PAIR_PR_UNHANDLED)
                elif pr_status == 'approved':
                    item_attr = curses.color_pair(COLOR_PAIR_PR_APPROVED)
                elif pr_status == 'merged':
                    item_attr = curses.color_pair(COLOR_PAIR_TASK_ALL_SUBTASKS_DONE)
                elif cached_item and (cached_item.get('new_jira_comment') or cached_item.get('new_trello_comment')):
                    item_attr = curses.color_pair(COLOR_PAIR_NEW_COMMENT)

                if i == selected_subtask_idx:
                    item_attr = curses.color_pair(COLOR_PAIR_SELECTED)

                prefix = ">" if i == selected_subtask_idx else ""
                full_prefix = f"{prefix}{' ' if prefix else ''}{i+1}. {status_char} "

                start_col = 2
                max_text_width_for_line = effective_main_width - start_col - len(full_prefix)
                if max_text_width_for_line < 0 : max_text_width_for_line = 0

                lines_used = _draw_wrapped_text(stdscr, display_text, row, start_col,
                                                max_text_width_for_line, effective_main_width, content_height_obj,
                                                prefix=full_prefix,
                                                subsequent_indent_offset=len(prefix) + len(f" {i+1}. {status_char} "),
                                                attr=item_attr)
                row += lines_used


        elif content_height_obj[0] > 0 and effective_main_width > 2 and current_ticket:
            stdscr.addstr(row, 2, t('ui_no_subtasks')[:effective_main_width-2])
            row += 1; content_height_obj[0] -= 1

        notes_to_show_preview = []
        task_info_to_show = []
        notes_title_preview = ""
        jira_comments = []
        trello_data = []
        trello_link = ""

        if selected_subtask_idx != -1 and 0 <= selected_subtask_idx < len(subtask_list_to_use):
            sel_sub_name, sel_sub_details = subtask_list_to_use[selected_subtask_idx]
            sel_sub_name = inc.helpers.get_jira_ticket_from_url(sel_sub_name)
            sub_task_with_desc = sel_sub_name
            notes_to_show_preview = sel_sub_details.get("notes", []).copy()

            # Handle PR information with new v2 schema
            pr_details_v2 = sel_sub_details.get("pr_details", {})
            
            # If PR details are missing but PR URL exists, try to restore from cache
            if (sel_sub_details.get("pr_url") and 
                (not pr_details_v2 or pr_details_v2.get('version') != 2)):
                from inc.integrations.pr_cache import get_pr_details_from_cache
                cached_pr_details = get_pr_details_from_cache(sel_sub_details["pr_url"])
                if cached_pr_details:
                    sel_sub_details["pr_details"] = cached_pr_details
                    pr_details_v2 = cached_pr_details
            
            if pr_details_v2 and pr_details_v2.get('version') == 2:
                pr_meta = pr_details_v2.get('meta', {})
                task_info_to_show.insert(0, f"PR: {pr_meta.get('url', sel_sub_details.get('pr_url', 'Unknown URL'))}")
                
                # Use new formatter to get overall status
                from inc.utils.pr_formatters import overall_status_badge
                status_text, _ = overall_status_badge(pr_details_v2)
                
                # Format reviewers with new schema
                reviewers = pr_details_v2.get('reviewers', [])
                if reviewers:
                    from inc.utils.pr_formatters import status_badge
                    reviewer_badges = []
                    for reviewer in reviewers[:4]:  # Limit to 4 reviewers for space
                        badge, _ = status_badge(reviewer.get('status', 'UNAPPROVED'))
                        reviewer_badges.append(f"{badge} {reviewer.get('displayName', 'Unknown')}")
                    
                    approvers_str = f"PR {status_text}: {', '.join(reviewer_badges)}"
                    if len(reviewers) > 4:
                        approvers_str += f" (+{len(reviewers) - 4} more)"
                    task_info_to_show.insert(1, approvers_str)
                else:
                    task_info_to_show.insert(1, f"PR {status_text}: No reviewers")
            elif sel_sub_details.get("pr_url"):  # Fallback for old format
                task_info_to_show.insert(0, f"PR: {sel_sub_details.get('pr_url')}")
                # Try to use old pr_details if available
                old_pr_details = sel_sub_details.get("pr_details", {})
                if old_pr_details and old_pr_details.get('status_text'):
                    status_text = old_pr_details.get('status_text', 'waiting')
                    approvers_formatted = old_pr_details.get('approvers_formatted', [])
                    approvers_str = "PR " + status_text + ": " + ", ".join(approvers_formatted)
                    task_info_to_show.insert(1, approvers_str)


            cached_item = cache_copy.get(sel_sub_name, {})
            

            if cached_item:
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

                


                vf_link = next((l.get("object",{}).get("url") for l in cached_item.get('remotelinks',[]) if l.get("globalId") == "VF - Log Hours"), "N/A")
                task_info_to_show.insert(0, f"VF: {vf_link}")

                jira_description = cached_item.get('data', {}).get('fields', {}).get('description', "")
                
                if (jira_description and isinstance(jira_description, str)):
                    # API v2 way, no objects
                    pattern = r"(https://trello\.com/c/[^]]+)"
                    match = re.search(pattern, jira_description)
                    if match:
                        trello_link = match.group(0)

                        task_info_to_show.insert(0, f"Trello: {trello_link}")

                jira_link = f"{inc.config_manager.config.get('JIRA_URL')}/browse/{sel_sub_name}"
                task_info_to_show.insert(0, f"{status_icon} {jira_link}")

                summary = cached_item.get('data', {}).get('fields', {}).get('summary', {})
                jira_comments = list(reversed(cached_item.get('data', {}).get('fields', {}).get('comment', {}).get('comments', {})))
                trello_data = cached_item.get('trello_data', {})

                sub_task_with_desc = f"{sel_sub_name} {summary}"

            notes_title_preview = t('ui_subtask_notes_header', subtask=sub_task_with_desc)

        elif current_ticket:
            notes_title_preview = t('ui_main_task_notes_header', task=current_ticket)
            notes_to_show_preview = data.get("notes", {}).get(current_ticket, [])

        if notes_title_preview and content_height_obj[0] > 0 and effective_main_width > 2:
            row += 1
            stdscr.addstr(row, 2, notes_title_preview[:effective_main_width-2])
            row += 1; content_height_obj[0] -= 1
            if not notes_to_show_preview and content_height_obj[0] > 0 :
                stdscr.addstr(row, 4, t('ui_no_notes')[:effective_main_width-4])
                row += 1; content_height_obj[0] -=1


        if len(task_info_to_show):
            lines_used_note = _draw_wrapped_text(stdscr, "┌─────INFO─── ─── ── ── ─ ─  ─   ─", row, 4,
                effective_main_width, effective_main_width, content_height_obj,
                prefix="", subsequent_indent_offset=0,
                attr=curses.color_pair(COLOR_PAIR_PAUSED))
            row += lines_used_note

        for note_idx, note in enumerate(task_info_to_show[:10]):
            if content_height_obj[0] <= 0 : break
            if effective_main_width <= 4: break

            prefix_note = f"| "
            start_col_note = 4
            max_text_width_note = effective_main_width - start_col_note - len(prefix_note)
            if max_text_width_note < 0 : max_text_width_note = 0
            lines_used_note = _draw_wrapped_text(stdscr, note, row, start_col_note,
                                            max_text_width_note, effective_main_width, content_height_obj,
                                            prefix=prefix_note, subsequent_indent_offset=len(prefix_note),
                                            attr=curses.color_pair(COLOR_PAIR_PAUSED))
            row += lines_used_note

        # PR comments are now handled by the new schema and displayed separately
        # Only show regular notes (PR comments were migrated out of notes)
        regular_notes = [n for n in notes_to_show_preview if not n.startswith("*PR* ")]


        if len(task_info_to_show):
            lines_used_note = _draw_wrapped_text(stdscr, "└──────────── ─── ── ── ─ ─  ─   ─", row, 4,
                effective_main_width, effective_main_width, content_height_obj,
                prefix="", subsequent_indent_offset=0,
                attr=curses.color_pair(COLOR_PAIR_PAUSED))
            row += lines_used_note


        # 1. Load the JSON string into a Python dictionary
        #trello_data = json.loads(trello_data_string)

        # 2. Create an empty list to hold the formatted comments
        trello_comments = []

        # 3. Loop through each action in the 'actions' list
        if trello_data and len(trello_data):
            for action in trello_data['actions']:
                # We only want actions that are comments
                if action['type'] == 'commentCard':
                    # Parse the date string into a datetime object
                    # The 'Z' at the end means UTC, which we replace for Python's parser
                    date_obj = datetime.fromisoformat(action['date'].replace('Z', '+00:00'))

                    # Format the date into a more readable string (e.g., 07.08.2025 12:40)
                    formatted_date = date_obj.strftime('%d.%m %H:%M')

                    # Create a dictionary for the comment and add it to our list
                    trello_comments.append({
                        'comment_text': action['data']['text'],
                        'creator_name': action['memberCreator']['fullName'],
                        'date': formatted_date
                    })

        # 4. Print the final array beautifully ✨
        #print(json.dumps(trello_comments, indent=4, ensure_ascii=False))

        if len(trello_comments):
            lines_used_note = _draw_wrapped_text(stdscr, "┌─────TRELLO─ ─── ── ── ─ ─  ─   ─", row, 4,
                effective_main_width, effective_main_width, content_height_obj,
                prefix="", subsequent_indent_offset=0,
                attr=curses.color_pair(COLOR_PAIR_GREY))
            row += lines_used_note

        for note_idx, note in enumerate(trello_comments[:5]):
            note_body = note.get("comment_text", "")
            note_from = note.get("creator_name", "")
            comment_date = note.get("date", "")
            note_body = note_body.replace("\n", " ")
            note_body = note_from + ": " + note_body
            if content_height_obj[0] <= 0 : break
            if effective_main_width <= 4: break
            prefix_note = f"| "
            start_col_note = 4
            max_text_width_note = effective_main_width - start_col_note - len(prefix_note)

            note_body = comment_date + ": " + note_body[0:max_text_width_note - 17] + "..."

            if max_text_width_note < 0 : max_text_width_note = 0
            lines_used_note = _draw_wrapped_text(stdscr, note_body, row, start_col_note,
                                            max_text_width_note, effective_main_width, content_height_obj,
                                            prefix=prefix_note, subsequent_indent_offset=len(prefix_note),
                                            attr=curses.color_pair(COLOR_PAIR_GREY))
            row += lines_used_note

        if len(trello_comments):
            lines_used_note = _draw_wrapped_text(stdscr, "└──────────── ─── ── ── ─ ─  ─   ─", row, 4,
                effective_main_width, effective_main_width, content_height_obj,
                prefix="", subsequent_indent_offset=0,
                attr=curses.color_pair(COLOR_PAIR_GREY))
            row += lines_used_note


        if len(jira_comments):
            lines_used_note = _draw_wrapped_text(stdscr, "┌─────JIRA─── ─── ── ── ─ ─  ─   ─", row, 4,
                effective_main_width, effective_main_width, content_height_obj,
                prefix="", subsequent_indent_offset=0,
                attr=curses.color_pair(COLOR_PAIR_STANDOUT))
            row += lines_used_note
        for note_idx, note in enumerate(jira_comments[:5]):
            note_body = note.get("body", "")
            comment_date = note.get("updated", "")
            comment_date = comment_date[:-2] + ':' + comment_date[-2:]
            dt = datetime.fromisoformat(comment_date) 

            note_body = re.sub(r'\[~.*?\]', 'USER', note_body)
            note_body = note_body.replace("\n", " ")
            
            if content_height_obj[0] <= 0 : break
            if effective_main_width <= 4: break
            prefix_note = f"| "
            start_col_note = 4
            max_text_width_note = effective_main_width - start_col_note - len(prefix_note)

            note_body = dt.strftime('%d.%m. %H:%M') + ": " + note_body[0:max_text_width_note - 17] + "..."

            if max_text_width_note < 0 : max_text_width_note = 0
            lines_used_note = _draw_wrapped_text(stdscr, note_body, row, start_col_note,
                                            max_text_width_note, effective_main_width, content_height_obj,
                                            prefix=prefix_note, subsequent_indent_offset=len(prefix_note),
                                            attr=curses.color_pair(COLOR_PAIR_STANDOUT))
            row += lines_used_note
        if len(jira_comments):
            lines_used_note = _draw_wrapped_text(stdscr, "└──────────── ─── ── ── ─ ─  ─   ─", row, 4,
                effective_main_width, effective_main_width, content_height_obj,
                prefix="", subsequent_indent_offset=0,
                attr=curses.color_pair(COLOR_PAIR_STANDOUT))
            row += lines_used_note


        # Display PR comments if available with v2 schema
        if (selected_subtask_idx != -1 and 0 <= selected_subtask_idx < len(subtask_list_to_use)):
            sel_sub_name, sel_sub_details = subtask_list_to_use[selected_subtask_idx]
            pr_details_v2 = sel_sub_details.get("pr_details", {})
            if pr_details_v2 and pr_details_v2.get('version') == 2:
                from inc.utils.pr_display import render_pr_comments_section
                lines_used = render_pr_comments_section(
                    stdscr, pr_details_v2, row, effective_main_width, content_height_obj,
                    max_comments=5, start_col=0, show_header=True, show_footer=True
                )
                row += lines_used
        
        # Display regular notes (without PR comments)
        for note_idx, note in enumerate(regular_notes[:10]):
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

        if len(regular_notes) > 10 and content_height_obj[0] > 0 and effective_main_width > 7:
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

    with external_meetings_lock:
        current_external_meetings = copy.deepcopy(external_meetings)

    for m in current_external_meetings:
        try:
            time_obj = datetime.strptime(m['start_time'], "%H:%M").time()
            dt = datetime.combine(date.today(), time_obj)
            if dt.date() == now_dt_display.date() and dt >= now_dt_display:
                todays_upcoming_events.append({'dt': dt, 'details': m, 'type': 'external_meeting', 'recurring': False})
        except (ValueError, KeyError):
            continue

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
            elif event.get('type') == 'external_meeting':
                if content_height_obj[0] <= 0: break
                m = event['details']
                text_content = f"{m['start_time']}-{m['end_time']}: ({m['title']}) {m['url']} ({format_timedelta_minutes(event['dt'] - now_dt)})"
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

    # Draw help overlay - always show when requested, overlaying the content
    if show_help_footer and effective_main_width > 0:
        # Calculate help overlay dimensions
        help_start_y = max(1, height - num_actual_help_lines - 3)  # Leave space for command line
        help_width = min(effective_main_width, width - 4)  # Leave some margins
        help_height = min(num_actual_help_lines, height - help_start_y - 2)
        
        # Draw help overlay with black background
        try:
            for i in range(help_height):
                overlay_y = help_start_y + i
                if overlay_y < height - 2 and i < len(current_help_lines_list):
                    line_text = current_help_lines_list[i]
                    # Clear the line with black background
                    padding = " " * help_width
                    stdscr.addstr(overlay_y, 2, padding, curses.color_pair(COLOR_PAIR_HELP_OVERLAY))
                    
                    # Add the help text with appropriate indentation
                    indent = 2 if i > 0 and line_text.strip() != t('help_header') else 0
                    if line_text.strip() == t('help_header'): 
                        indent = 0
                    
                    display_text = line_text[:max(0, help_width - indent - 2)]
                    if display_text.strip():  # Only draw non-empty lines
                        stdscr.addstr(overlay_y, 2 + indent, display_text, 
                                    curses.color_pair(COLOR_PAIR_HELP_OVERLAY) | curses.A_BOLD)
        except curses.error:
            pass  # Ignore drawing errors

    try:
        stdscr.addstr(height - 1, 0, " " * (width-1 if width > 0 else 0) )
        stdscr.addstr(height - 1, 0, command_line_text.ljust(width-1 if width > 0 else 0), curses.color_pair(COLOR_PAIR_DEFAULT) | curses.A_BOLD)
        curses.curs_set(1)
        stdscr.move(height - 1, min(cursor_x, width - 1 if width > 0 else 0))
    except curses.error: pass

    try:
        stdscr.attroff(curses.A_BOLD)
        for i in range(1, 17):  # Updated to include new COLOR_PAIR_HELP_OVERLAY
            stdscr.attroff(curses.color_pair(i))
    except curses.error: pass
    stdscr.refresh()
    return True

def read_jira_box_content(max_lines=10):
    try:
        with open(JIRA_BOX_FILE, 'r', encoding='utf-8') as f:
            lines = [line.rstrip('\n') for line in f.readlines()]
            return lines[:max_lines]
    except FileNotFoundError:
        return []
    except Exception:
        return []