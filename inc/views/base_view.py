import curses
from datetime import timedelta
from inc.utils.constants import COLOR_PAIR_REVERSE, COLOR_PAIR_PERMANENT_NOTIFICATION

def _draw_wrapped_text(stdscr, text_to_draw, start_row, start_col,
                       max_width_for_text_line,
                       effective_content_width,
                       content_height_obj,
                       prefix="", subsequent_indent_offset=0, attr=0):
    lines_used_for_item = 0
    remaining_text = text_to_draw
    current_line_y = start_row

    max_h, max_w = stdscr.getmaxyx()

    if content_height_obj[0] > 0 and current_line_y < max_h - 1:
        line_content_with_prefix = prefix + remaining_text
        available_for_text_on_first_line = effective_content_width - start_col - len(prefix)
        text_segment_on_first_line = remaining_text[:available_for_text_on_first_line]
        full_first_line_to_draw = prefix + text_segment_on_first_line

        try:
            stdscr.addstr(current_line_y, start_col, full_first_line_to_draw, attr)
            lines_used_for_item += 1
            content_height_obj[0] -= 1
            remaining_text = remaining_text[len(text_segment_on_first_line):]
            current_line_y += 1
        except curses.error:
            remaining_text = ""
    else:
        remaining_text = ""

    wrapped_line_draw_start_col = start_col + subsequent_indent_offset
    max_width_for_this_wrapped_line = effective_content_width - wrapped_line_draw_start_col

    while remaining_text and content_height_obj[0] > 0 and current_line_y < max_h - 1:
        segment = remaining_text[:max_width_for_this_wrapped_line]
        try:
            stdscr.addstr(current_line_y, wrapped_line_draw_start_col, segment, attr)
            lines_used_for_item += 1
            content_height_obj[0] -= 1
            remaining_text = remaining_text[len(segment):]
            current_line_y += 1
        except curses.error:
            break
    return lines_used_for_item


def format_timedelta_minutes(delta):
    if not isinstance(delta, timedelta):
        return ""
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"

def show_notification(stdscr, message):
    """Show a temporary notification at the bottom of the screen."""
    try:
        height, width = stdscr.getmaxyx()
        if height < 2 or width == 0:
            return
        notification_line = height - 2
        message_to_show = message[:width - 2 if width > 2 else width]

        stdscr.attron(curses.color_pair(COLOR_PAIR_REVERSE))
        stdscr.addstr(notification_line, 0, " " * (width-1 if width > 0 else 0))
        stdscr.addstr(notification_line, 0, message_to_show.ljust(width-1 if width > 0 else 0))
        stdscr.attroff(curses.color_pair(COLOR_PAIR_REVERSE))
        stdscr.refresh()
        curses.napms(500)
        stdscr.addstr(notification_line, 0, " " * (width-1 if width > 0 else 0))
        show_permanent_notification(stdscr, [])
        stdscr.refresh()
    except curses.error:
        pass


def show_permanent_notification(stdscr, permanent_notifications):
    try:
        height, width = stdscr.getmaxyx()
        if height < 2 or width == 0: return
        notification_line = height - 2

        row = 1
        if permanent_notifications:
            for msg in permanent_notifications:

                stdscr.addstr(notification_line, 0, " " * (width-1 if width > 0 else 0))
                stdscr.addstr(notification_line, 0, f"{row}. {msg[:width-3]}", curses.color_pair(COLOR_PAIR_PERMANENT_NOTIFICATION) | curses.A_BOLD)
                row += 1

        stdscr.refresh()
    except curses.error: pass
    except Exception: pass