import curses
import time
from datetime import datetime, timedelta, date
from inc.helpers import t
from inc.views.base_view import _draw_wrapped_text

(COLOR_PAIR_DEFAULT, COLOR_PAIR_REVERSE, COLOR_PAIR_GREY, COLOR_PAIR_PAUSED,
 COLOR_PAIR_SELECTED, COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN, COLOR_PAIR_URGENT_BOX,
 COLOR_PAIR_PR_UNHANDLED, COLOR_PAIR_PR_APPROVED, COLOR_PAIR_FOCUSED,
 COLOR_PAIR_PERMANENT_NOTIFICATION, COLOR_PAIR_STANDOUT, COLOR_PAIR_NEW_COMMENT) = range(1, 15)

VIEW_MAIN = "main"
VIEW_DEDICATED_NOTES = "dedicated_notes"
VIEW_DAILY_NOTES = "daily_notes"

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

    #main_win.refresh()
    return True