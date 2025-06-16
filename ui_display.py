import curses
from datetime import datetime, date, timedelta
from config_manager import t, SCRIPT_DIR
from jira_api import jira_cache, jira_cache_lock
import os

# --- Color Pairs (defined in main app, used here) ---
(COLOR_PAIR_DEFAULT, COLOR_PAIR_REVERSE, COLOR_PAIR_GREY, COLOR_PAIR_PAUSED,
 COLOR_PAIR_SELECTED, COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, COLOR_PAIR_URGENT_BOX,
 COLOR_PAIR_PR_UNHANDLED, COLOR_PAIR_PR_APPROVED, COLOR_PAIR_FOCUSED,
 COLOR_PAIR_PERMANENT_NOTIFICATION) = range(1, 12)

JIRA_BOX_FILE = os.path.join(SCRIPT_DIR, "jira_box2.txt")

def format_subtask_for_title(subtask_name):
    """Extracts the last part of a URL-like subtask name for a cleaner title."""
    if subtask_name.startswith("http"):
        try:
            return [part for part in subtask_name.split('/') if part][-1]
        except IndexError:
            return subtask_name
    return subtask_name

def read_jira_box_content(max_lines=10):
    """Reads content from the jira_box2.txt file."""
    try:
        with open(JIRA_BOX_FILE, 'r', encoding='utf-8') as f:
            lines = [line.rstrip('\n') for line in f.readlines()]
            return lines[:max_lines]
    except FileNotFoundError:
        return []
    except Exception:
        return []

def format_timedelta_minutes(delta):
    if not isinstance(delta, timedelta): return ""
    total_seconds = int(delta.total_seconds())
    is_past, total_seconds = total_seconds < 0, abs(total_seconds)
    h, m, s = total_seconds // 3600, (total_seconds % 3600) // 60, total_seconds % 60
    parts = []
    if h > 0: parts.append(t('time_h', h=h))
    if m > 0: parts.append(t('time_m', m=m))
    if not parts and s > 0: parts.append(t('time_s', s=s))
    if not parts: return t('time_moment_ago') if is_past else ""
    time_str = " ".join(parts)
    return t('time_ago', time_str=time_str) if is_past else t('time_in', time_str=time_str)

def _draw_wrapped_text(stdscr, text_to_draw, start_row, start_col,
                       max_width_for_text_line,
                       effective_content_width,
                       content_height_obj,
                       prefix="", subsequent_indent_offset=0, attr=0):
    lines_used_for_item = 0
    if not text_to_draw or content_height_obj[0] <= 0: return 0

    remaining_text = str(text_to_draw)
    current_line_y = start_row
    max_h, max_w = stdscr.getmaxyx()

    def draw_line(y, x, content, attr):
        if y < max_h and x < max_w:
            stdscr.addstr(y, x, content[:max_w-x], attr)

    if current_line_y < max_h - 1:
        full_first_line = prefix + remaining_text
        text_on_first_line = full_first_line[:max_width_for_text_line]
        draw_line(current_line_y, start_col, text_on_first_line, attr)

        lines_used_for_item += 1
        content_height_obj[0] -= 1
        remaining_text = remaining_text[max(0, len(text_on_first_line) - len(prefix)):]
        current_line_y += 1

    wrapped_line_start_col = start_col + subsequent_indent_offset
    max_width_for_wrapped = effective_content_width - wrapped_line_start_col
    while remaining_text and content_height_obj[0] > 0 and current_line_y < max_h -1:
        if max_width_for_wrapped <= 0: break
        segment = remaining_text[:max_width_for_wrapped]
        draw_line(current_line_y, wrapped_line_start_col, segment, attr)
        lines_used_for_item += 1
        content_height_obj[0] -= 1
        remaining_text = remaining_text[len(segment):]
        current_line_y += 1

    return lines_used_for_item

def display_dedicated_notes_view(stdscr, data, command_buffer, entity_for_notes, selected_note_idx):
    height, width = stdscr.getmaxyx()
    now_time_str = datetime.now().strftime("%H:%M:%S")
    stdscr.clear()

    row = 0
    stdscr.addstr(row, 0, t('ui_clock', now_time_str=now_time_str))
    row += 1
    stdscr.addstr(row, 0, "-" * (width-1))
    row += 1

    title = t('dedicated_notes_title')
    notes_list_to_display = []

    if entity_for_notes:
        entity_type, entity_name = entity_for_notes.get("type"), entity_for_notes.get("name")
        main_task_name_context = entity_for_notes.get("main_task_name", data.get("current_ticket"))

        if entity_type == "task" and entity_name:
            title = t('dedicated_notes_header_task', name=entity_name)
            notes_list_to_display = data.get("notes", {}).get(entity_name, [])
        elif entity_type == "subtask" and main_task_name_context and entity_name:
            title = t('dedicated_notes_header_subtask', main_task=main_task_name_context, name=entity_name)
            subtask_details = data.get("sub_tasks",{}).get(main_task_name_context,{}).get(entity_name, {})
            notes_list_to_display = subtask_details.get("notes", [])
    else:
        title = t('dedicated_notes_no_selection')

    stdscr.addstr(row, 0, title[:width-1])
    row +=1
    stdscr.addstr(row, 0, "-" * min(len(title), width-1))
    row +=1

    help_lines = [t('help_header'), t('dedicated_notes_help_select'), t('dedicated_notes_help_delete'), t('dedicated_notes_help_add'), t('dedicated_notes_help_back')]
    reserved_rows = len(help_lines) + 2
    content_height_obj = [height - row - reserved_rows]

    for idx, note_text in enumerate(notes_list_to_display):
        if content_height_obj[0] <= 0: break

        prefix = f"> {idx+1}. " if idx == selected_note_idx else f"  {idx+1}. "
        attr = curses.color_pair(COLOR_PAIR_SELECTED) if idx == selected_note_idx else curses.color_pair(COLOR_PAIR_DEFAULT)
        lines_used = _draw_wrapped_text(stdscr, note_text, row, 0, width-2, width, content_height_obj, prefix=prefix, subsequent_indent_offset=len(prefix), attr=attr)
        row += lines_used

    if not notes_list_to_display and entity_for_notes:
        if content_height_obj[0] > 0: stdscr.addstr(row, 0, t('dedicated_notes_no_notes'))

    help_y = height - 2 - len(help_lines)
    for i, line in enumerate(help_lines):
        if help_y + i < height-1: stdscr.addstr(help_y + i, 0, line[:width-1])

    cmd_line = f"> {command_buffer}"
    stdscr.addstr(height - 1, 0, cmd_line.ljust(width-1))
    stdscr.move(height - 1, len(cmd_line))
    stdscr.refresh()

def display_daily_notes_view(stdscr, data, command_buffer, current_date_for_notes, selected_note_idx):
    height, width = stdscr.getmaxyx()
    now_time_str = datetime.now().strftime("%H:%M:%S")
    stdscr.clear()

    row = 0
    stdscr.addstr(row, 0, t('ui_clock', now_time_str=now_time_str))
    row += 1
    stdscr.addstr(row, 0, "-" * (width-1))
    row += 1

    date_str_iso = current_date_for_notes.isoformat()
    weekday_str = t('weekdays')[current_date_for_notes.weekday()]
    title = t('daily_notes_header', date=date_str_iso, weekday=weekday_str)
    notes_list = data.get("daily_notes", {}).get(date_str_iso, [])

    stdscr.addstr(row, 0, title[:width-1])
    row +=1
    stdscr.addstr(row, 0, "-" * min(len(title), width-1))
    row +=1

    help_lines = [t('help_header'), t('dedicated_notes_help_select'), t('dedicated_notes_help_delete'), t('dedicated_notes_help_add'), t('daily_notes_help_prev'), t('daily_notes_help_next'), t('dedicated_notes_help_back')]
    reserved_rows = len(help_lines) + 2
    content_height_obj = [height - row - reserved_rows]

    for idx, note_text in enumerate(notes_list):
        if content_height_obj[0] <= 0: break

        prefix = f"> {idx+1}. " if idx == selected_note_idx else f"  {idx+1}. "
        attr = curses.color_pair(COLOR_PAIR_SELECTED) if idx == selected_note_idx else curses.color_pair(COLOR_PAIR_DEFAULT)
        lines_used = _draw_wrapped_text(stdscr, note_text, row, 0, width-2, width, content_height_obj, prefix=prefix, subsequent_indent_offset=len(prefix), attr=attr)
        row += lines_used

    if not notes_list:
        if content_height_obj[0] > 0: stdscr.addstr(row, 0, t('daily_notes_no_notes'))

    help_y = height - 2 - len(help_lines)
    for i, line in enumerate(help_lines):
        if help_y + i < height-1: stdscr.addstr(help_y + i, 0, line[:width-1])

    cmd_line = f"> {command_buffer}"
    stdscr.addstr(height - 1, 0, cmd_line.ljust(width-1))
    stdscr.move(height - 1, len(cmd_line))
    stdscr.refresh()

def _is_valid_past_event_today(event_item, now_for_display, today_start_dt):
    try:
        dt_str = event_item.get('datetime')
        if not isinstance(dt_str, str): return False
        dt = datetime.fromisoformat(dt_str)
        return today_start_dt <= dt < now_for_display
    except (ValueError, TypeError): return False

def display_ui(stdscr, data, command_buffer="", full_redraw=False, selected_subtask_idx=-1,
               current_view_mode="main", entity_for_dedicated_notes=None,
               show_help_footer=True, current_date_for_daily_notes_arg=None, selected_note_idx=-1,
               permanent_notifications=[], pull_requests_for_review=[]):

    if current_view_mode == "dedicated_notes":
        return display_dedicated_notes_view(stdscr, data, command_buffer, entity_for_dedicated_notes, selected_note_idx)
    if current_view_mode == "daily_notes":
        return display_daily_notes_view(stdscr, data, command_buffer, current_date_for_daily_notes_arg, selected_note_idx)

    height, width = stdscr.getmaxyx()
    now_time_str = datetime.now().strftime("%H:%M:%S")
    now_dt = datetime.now()

    if full_redraw: stdscr.clear()

    row = 0
    if permanent_notifications:
        for msg in permanent_notifications:
            if row >= height - 1: break
            stdscr.addstr(row, 0, msg[:width-1], curses.color_pair(COLOR_PAIR_PERMANENT_NOTIFICATION) | curses.A_BOLD)
            row += 1
        if row < height - 1:
            stdscr.addstr(row, 0, "-" * (width-1))
            row += 1

    content_start_row = row

    all_projects = list(data.get("sub_tasks", {}).keys()) + [p['ticket'] for p in data.get("paused_tasks", [])]
    all_displayable_projects = sorted(list(set(p for p in all_projects if p not in data.get("completed_tickets", []))))

    display_right_panel = bool(all_displayable_projects)
    panel_width = max(len(p) for p in all_displayable_projects) + 5 if all_displayable_projects else 10
    effective_main_width = width - panel_width - 1 if display_right_panel and width > 40 else width

    if display_right_panel:
        for i, proj_name in enumerate(all_displayable_projects):
            if i >= height: break
            stdscr.addstr(i, effective_main_width, "|")
            attr = curses.color_pair(COLOR_PAIR_DEFAULT)
            if data.get("current_ticket") == proj_name: attr = curses.color_pair(COLOR_PAIR_SELECTED) | curses.A_BOLD
            elif any(p['ticket'] == proj_name for p in data.get("paused_tasks",[])): attr = curses.color_pair(COLOR_PAIR_PAUSED)
            stdscr.addstr(i, effective_main_width + 2, f"{i+1}. {proj_name}"[:panel_width-2], attr)

    if content_start_row < height:
        stdscr.addstr(content_start_row, 0, t('ui_clock', now_time_str=now_time_str)[:effective_main_width-1])
    
    current_project = data.get("current_ticket")
    row = content_start_row + 1
    help_lines = [t('help_header'), t('help_login'), t('help_switch_task'), t('help_new_task'), t('help_add_subtask'), t('help_hide_subtask'), t('help_add_pr'), t('help_done_subtask'), t('help_done_task'), t('help_add_meeting'), t('help_add_event'), t('help_add_note'), t('help_set_focus'), t('help_set_subtask_focus'), t('help_toggle_help'), t('help_daily_notes'), t('help_notes_view'), t('help_quit')]
    current_help_lines = help_lines if show_help_footer else [t('help_hidden_prompt')]
    footer_height = len(current_help_lines) + 2
    content_height_obj = [height - row - footer_height]
    
    if not current_project:
        if content_height_obj[0] > 0: stdscr.addstr(row, 0, t('ui_no_active_task'))
    else:
        if content_height_obj[0] > 0:
            stdscr.addstr(row, 0, f"{t('ui_current_task_prefix')}{current_project}"[:effective_main_width-1])
            row += 1; content_height_obj[0] -= 1

        visible_tasks = [(k,v) for k,v in data.get("sub_tasks", {}).get(current_project, {}).items() if isinstance(v, dict) and not v.get("hidden")]
        
        if visible_tasks and content_height_obj[0] > 0:
            stdscr.addstr(row, 2, t('ui_subtasks_header')[:effective_main_width-3])
            row += 1; content_height_obj[0] -= 1
            for i, (task_id, task_details) in enumerate(visible_tasks):
                if content_height_obj[0] <= 0: break
                with jira_cache_lock: summary = jira_cache.get(task_id, {}).get('data', {}).get('fields', {}).get('summary', '')
                display_name = f"{task_id}: {summary}" if summary else task_id
                status_char = "✅" if task_details.get("done") else "[ ]"
                prefix = "> " if i == selected_subtask_idx else "  "
                attr = curses.color_pair(COLOR_PAIR_SELECTED) if i == selected_subtask_idx else curses.color_pair(COLOR_PAIR_DEFAULT)
                lines = _draw_wrapped_text(stdscr, display_name, row, 2, effective_main_width-4, effective_main_width, content_height_obj, prefix=f"{prefix}{status_char} ", subsequent_indent_offset=4, attr=attr)
                row += lines
        elif content_height_obj[0] > 0:
             stdscr.addstr(row, 2, t('ui_no_subtasks')); row += 1; content_height_obj[0] -= 1

        if selected_subtask_idx != -1 and selected_subtask_idx < len(visible_tasks):
            sel_id, sel_details = visible_tasks[selected_subtask_idx]
            if content_height_obj[0] > 0:
                row += 1; content_height_obj[0] -= 1
            if content_height_obj[0] > 0:
                stdscr.addstr(row, 2, t('ui_ticket_info_header'), curses.A_BOLD)
                row += 1; content_height_obj[0] -=1

            with jira_cache_lock: cached_item = jira_cache.get(sel_id, {})
            if cached_item:
                if content_height_obj[0] > 0:
                    status = cached_item.get('data',{}).get('fields',{}).get('status',{}).get('name','N/A')
                    lines = _draw_wrapped_text(stdscr, f"Status: {status}", row, 4, effective_main_width-6, effective_main_width, content_height_obj)
                    row += lines
                if content_height_obj[0] > 0:
                    jira_link = f"{config.get('JIRA_URL')}/browse/{sel_id}"
                    lines = _draw_wrapped_text(stdscr, f"Link: {jira_link}", row, 4, effective_main_width-6, effective_main_width, content_height_obj)
                    row += lines
                if content_height_obj[0] > 0:
                    vf_link = next((l.get("object",{}).get("url") for l in cached_item.get('remotelinks',[]) if l.get("globalId") == "VF - Log Hours"), "N/A")
                    lines = _draw_wrapped_text(stdscr, f"VF Log Hours: {vf_link}", row, 4, effective_main_width-6, effective_main_width, content_height_obj)
                    row += lines

            pr_details = sel_details.get("pr_details", {})
            if pr_details:
                if content_height_obj[0] > 0:
                    lines = _draw_wrapped_text(stdscr, f"PR Status: {pr_details.get('status_text', 'waiting')}", row, 4, effective_main_width-6, effective_main_width, content_height_obj)
                    row += lines
                if content_height_obj[0] > 0:
                    approvers = ", ".join(pr_details.get('approvers_formatted', ["no data yet"]))
                    lines = _draw_wrapped_text(stdscr, f"PR Approvers: {approvers}", row, 4, effective_main_width-6, effective_main_width, content_height_obj)
                    row += lines

            if content_height_obj[0] > 0:
                row += 1; content_height_obj[0] -=1
                stdscr.addstr(row, 2, t('ui_subtask_notes_header', subtask=sel_id), curses.A_BOLD)
                row += 1; content_height_obj[0] -=1
            
            notes_list = sel_details.get("notes", [])
            for note in notes_list:
                if content_height_obj[0] <= 0: break
                lines = _draw_wrapped_text(stdscr, note, row, 4, effective_main_width-6, effective_main_width, content_height_obj, prefix="- ")
                row += lines

    help_y = height - footer_height
    if show_help_footer:
        for i, line in enumerate(current_help_lines):
            if help_y + i < height-1:
                stdscr.addstr(help_y + i, 0, line[:effective_main_width-1])

    cmd_line_text = f"> {command_buffer}"
    stdscr.addstr(height-1, 0, cmd_line_text.ljust(width-1))
    stdscr.move(height-1, len(cmd_line_text))
    stdscr.refresh()

def show_notification(stdscr, message):
    try:
        height, width = stdscr.getmaxyx()
        if height < 2: return
        stdscr.attron(curses.color_pair(COLOR_PAIR_REVERSE))
        stdscr.addstr(height - 2, 0, message.ljust(width-1))
        stdscr.attroff(curses.color_pair(COLOR_PAIR_REVERSE))
        stdscr.refresh()
        curses.napms(2000)
        stdscr.addstr(height - 2, 0, " " * (width - 1))
        stdscr.refresh()
    except curses.error: pass
