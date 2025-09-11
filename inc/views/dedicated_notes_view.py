import curses
from datetime import datetime
import re
from inc.helpers import t
from inc.config_manager import config

(COLOR_PAIR_DEFAULT, COLOR_PAIR_REVERSE, COLOR_PAIR_GREY, COLOR_PAIR_PAUSED,
 COLOR_PAIR_SELECTED, COLOR_PAIR_TASK_ALL_SUBTASKS_DONE, COLOR_PAIR_TASK_ALL_SUBTASKS_HIDDEN, COLOR_PAIR_URGENT_BOX,
 COLOR_PAIR_PR_UNHANDLED, COLOR_PAIR_PR_APPROVED, COLOR_PAIR_FOCUSED,
 COLOR_PAIR_PERMANENT_NOTIFICATION, COLOR_PAIR_STANDOUT, COLOR_PAIR_NEW_COMMENT) = range(1, 15)


def display_dedicated_notes_view(stdscr, data, command_buffer, entity_for_notes, show_help_footer, selected_note_idx, jira_cache=None, jira_cache_lock=None, scroll_offset=0):
    height, width = stdscr.getmaxyx()
    now_time_str = datetime.now().strftime("%H:%M:%S")
    stdscr.clear()

    row = 0
    stdscr.addstr(row, 0, t('ui_clock', now_time_str=now_time_str), curses.color_pair(COLOR_PAIR_DEFAULT))
    row += 1
    stdscr.addstr(row, 0, "-" * width)
    row += 1

    title = t('dedicated_notes_title')
    notes_list_to_display = []
    jira_comments = []
    trello_comments = []
    task_info_to_show = []

    # Get cache copy if available
    cache_copy = {}
    if jira_cache and jira_cache_lock:
        with jira_cache_lock:
            cache_copy = jira_cache.copy()

    if entity_for_notes:
        entity_type = entity_for_notes.get("type")
        entity_name = entity_for_notes.get("name")
        main_task_name_context = entity_for_notes.get("main_task_name", data.get("current_ticket"))

        if entity_type == "task" and entity_name:
            title = t('dedicated_notes_header_task', name=entity_name)
            notes_list_to_display = data.get("notes", {}).get(entity_name, [])
        elif entity_type == "subtask" and main_task_name_context and entity_name:
            title = t('dedicated_notes_header_subtask', main_task=main_task_name_context, name=entity_name)
            subtask_details = data.get("sub_tasks",{}).get(main_task_name_context,{}).get(entity_name)
            if subtask_details and isinstance(subtask_details, dict):
                notes_list_to_display = subtask_details.get("notes", [])

            # Get Jira and Trello data for subtask
            from inc.helpers import get_jira_ticket_from_url
            jira_ticket_id = get_jira_ticket_from_url(entity_name)
            cached_item = cache_copy.get(jira_ticket_id, {})

            if cached_item:
                # Get task info
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

                jira_link = f"{config.get('JIRA_URL')}/browse/{jira_ticket_id}"
                task_info_to_show.append(f"{status_icon} {jira_link}")

                summary = cached_item.get('data', {}).get('fields', {}).get('summary', '')
                if summary:
                    task_info_to_show.append(f"Summary: {summary}")

                # Check for Trello link in description
                jira_description = cached_item.get('data', {}).get('fields', {}).get('description', "")
                if jira_description and isinstance(jira_description, str):
                    pattern = r"(https://trello\.com/c/[^]]+)"
                    match = re.search(pattern, jira_description)
                    if match:
                        trello_link = match.group(0)
                        task_info_to_show.append(f"Trello: {trello_link}")

                # VF link
                vf_link = next((l.get("object",{}).get("url") for l in cached_item.get('remotelinks',[]) if l.get("globalId") == "VF - Log Hours"), None)
                if vf_link and vf_link != "N/A":
                    task_info_to_show.append(f"VF: {vf_link}")

                # PR info from subtask details
                if subtask_details and subtask_details.get("pr_url"):
                    task_info_to_show.append(f"PR: {subtask_details.get('pr_url')}")

                    pr_details = subtask_details.get("pr_details", {})
                    if pr_details:
                        status_text = pr_details.get('status_text', 'waiting')
                        approvers_str = "PR " + status_text + ": " + ", ".join(pr_details.get('approvers_formatted', []))
                        task_info_to_show.append(approvers_str)

                # Get Jira comments
                jira_comments = list(reversed(cached_item.get('data', {}).get('fields', {}).get('comment', {}).get('comments', [])))

                # Get Trello comments
                trello_data = cached_item.get('trello_data', {})
                if trello_data and len(trello_data):
                    for action in trello_data['actions']:
                        if action['type'] == 'commentCard':
                            date_obj = datetime.fromisoformat(action['date'].replace('Z', '+00:00'))
                            formatted_date = date_obj.strftime('%d.%m %H:%M')
                            trello_comments.append({
                                'comment_text': action['data']['text'],
                                'creator_name': action['memberCreator']['fullName'],
                                'date': formatted_date
                            })
        else:
            title = t('dedicated_notes_no_selection')
    else:
        title = t('dedicated_notes_no_selection')

    stdscr.addstr(row, 0, title[:width])
    row +=1
    if len(title[:width-1]) > 0 : stdscr.addstr(row, 0, "-" * len(title[:width-1]))
    row +=1

    help_lines_notes_view = [
        t('help_header'),
        t('dedicated_notes_help_select'),
        t('dedicated_notes_help_delete'),
        t('dedicated_notes_help_add'),
        t('dedicated_notes_help_scroll_up'),
        t('dedicated_notes_help_scroll_down'),
        t('dedicated_notes_help_back')
    ]
    num_help_lines_notes_view = len(help_lines_notes_view)
    reserved_rows_notes_footer = num_help_lines_notes_view + 2

    content_height_val = height - (row + reserved_rows_notes_footer)
    if content_height_val < 0: content_height_val = 0
    content_height_obj = [content_height_val]

    # Create a virtual content area to render all content, then apply scrolling
    virtual_content = []
    virtual_row = 0

    # Helper function to add content to virtual screen with intelligent text wrapping
    def add_virtual_content(text, attr=curses.color_pair(COLOR_PAIR_DEFAULT), prefix="", indent_continuation=0):
        if not text:
            virtual_content.append({'text': "", 'attr': attr})
            return

        # Calculate available width for text
        available_width = width - 1  # Leave space for cursor

        def smart_wrap(text_to_wrap, line_width):
            """Intelligently wrap text, preferring to break at word boundaries"""
            if len(text_to_wrap) <= line_width:
                return [text_to_wrap]

            wrapped_lines = []
            remaining = text_to_wrap

            while remaining:
                if len(remaining) <= line_width:
                    wrapped_lines.append(remaining)
                    break

                # Try to find a good break point (space, punctuation)
                break_point = line_width
                for i in range(line_width, max(0, line_width - 20), -1):
                    if i < len(remaining) and remaining[i] in ' \t\n.,;:!?':
                        break_point = i + 1 if remaining[i] in ' \t\n' else i + 1
                        break

                # If we couldn't find a good break point, just break at the width
                if break_point == line_width and line_width < len(remaining):
                    # Look ahead to see if we're in the middle of a word
                    if line_width < len(remaining) and remaining[line_width] not in ' \t\n':
                        # Try to find the end of the current word
                        word_end = line_width
                        while word_end > line_width - 10 and word_end > 0 and remaining[word_end - 1] not in ' \t\n':
                            word_end -= 1
                        if word_end > line_width - 10:  # Found a reasonable word boundary
                            break_point = word_end

                wrapped_lines.append(remaining[:break_point].rstrip())
                remaining = remaining[break_point:].lstrip()

            return wrapped_lines

        # Handle prefixed lines (like "| comment text")
        if prefix:
            # First line with prefix
            first_line_available = available_width - len(prefix)
            if first_line_available <= 0:
                virtual_content.append({'text': prefix, 'attr': attr})
                return

            wrapped_lines = smart_wrap(text, first_line_available)

            # First line with prefix
            if wrapped_lines:
                virtual_content.append({'text': prefix + wrapped_lines[0], 'attr': attr})

                # Continuation lines with indentation
                if len(wrapped_lines) > 1:
                    continuation_prefix = " " * (len(prefix) + indent_continuation)
                    continuation_width = available_width - len(continuation_prefix)

                    for line in wrapped_lines[1:]:
                        # Further wrap continuation lines if needed
                        cont_wrapped = smart_wrap(line, continuation_width)
                        for cont_line in cont_wrapped:
                            virtual_content.append({'text': continuation_prefix + cont_line, 'attr': attr})
        else:
            # Simple text without prefix - wrap at available width
            wrapped_lines = smart_wrap(text, available_width)
            for line in wrapped_lines:
                virtual_content.append({'text': line, 'attr': attr})

    # Display task info section if available
    if task_info_to_show:
        add_virtual_content("┌─────INFO─── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_PAUSED))
        for info_item in task_info_to_show:
            add_virtual_content(info_item, curses.color_pair(COLOR_PAIR_PAUSED), prefix="| ")
        add_virtual_content("└──────────── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_PAUSED))
        add_virtual_content("")  # Empty line for spacing

    # Display Trello comments in full detail
    if trello_comments:
        add_virtual_content("┌─────TRELLO COMMENTS─── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_GREY))
        for comment in trello_comments:
            comment_text = comment.get('comment_text', '')
            creator_name = comment.get('creator_name', '')
            comment_date = comment.get('date', '')

            # Show header with author and date
            header = f"{creator_name} - {comment_date}"
            add_virtual_content(header, curses.color_pair(COLOR_PAIR_GREY) | curses.A_BOLD, prefix="| ")

            # Show full comment text (preserve newlines and wrap long lines)
            if comment_text:
                for line in comment_text.split('\n'):
                    add_virtual_content(line, curses.color_pair(COLOR_PAIR_GREY), prefix="| ")

            # Add separator between comments
            add_virtual_content("---", curses.color_pair(COLOR_PAIR_GREY), prefix="| ")
        add_virtual_content("└──────────── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_GREY))
        add_virtual_content("")  # Empty line for spacing

    # Display Jira comments in full detail
    if jira_comments:
        add_virtual_content("┌─────JIRA COMMENTS─── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_STANDOUT))
        for comment in jira_comments:
            comment_body = comment.get('body', '')
            comment_date = comment.get('updated', '')
            author = comment.get('author', {}).get('displayName', 'Unknown')

            # Fix date format
            if comment_date:
                try:
                    if len(comment_date) > 2:
                        comment_date = comment_date[:-2] + ':' + comment_date[-2:]
                    dt = datetime.fromisoformat(comment_date)
                    formatted_date = dt.strftime('%d.%m %H:%M')
                except:
                    formatted_date = comment_date
            else:
                formatted_date = 'Unknown date'

            # Show header with author and date
            header = f"{author} - {formatted_date}"
            add_virtual_content(header, curses.color_pair(COLOR_PAIR_STANDOUT) | curses.A_BOLD, prefix="| ")

            # Show full comment text (clean up user mentions, preserve newlines and wrap long lines)
            if comment_body:
                # Clean up Jira user mentions
                clean_body = re.sub(r'\[~.*?\]', 'USER', comment_body)
                for line in clean_body.split('\n'):
                    add_virtual_content(line, curses.color_pair(COLOR_PAIR_STANDOUT), prefix="| ")

            # Add separator between comments
            add_virtual_content("---", curses.color_pair(COLOR_PAIR_STANDOUT), prefix="| ")
        add_virtual_content("└──────────── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_STANDOUT))
        add_virtual_content("")  # Empty line for spacing

    # Display regular notes (with selection/deletion functionality preserved)
    if notes_list_to_display:
        add_virtual_content("┌─────NOTES─── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_DEFAULT))
        for note_idx, note_text in enumerate(notes_list_to_display):
            item_attr = curses.color_pair(COLOR_PAIR_DEFAULT)
            prefix = f"  {note_idx+1}. "
            if note_idx == selected_note_idx:
                item_attr = curses.color_pair(COLOR_PAIR_SELECTED)
                prefix = f"> {note_idx+1}. "
            add_virtual_content(note_text, item_attr, prefix=prefix)
        add_virtual_content("└──────────── ─── ── ── ─ ─  ─   ─", curses.color_pair(COLOR_PAIR_DEFAULT))

    # Show message if no content available
    if not notes_list_to_display and not jira_comments and not trello_comments and not task_info_to_show and entity_for_notes:
        add_virtual_content("No notes, comments, or details available for this item.")

    # Now render the visible portion of virtual content based on scroll offset
    visible_start = max(0, scroll_offset)
    visible_end = visible_start + content_height_val
    visible_content = virtual_content[visible_start:visible_end]

    for i, content_item in enumerate(visible_content):
        if row + i >= height - reserved_rows_notes_footer: break
        try:
            # Text wrapping is now handled in add_virtual_content, so just display as-is
            stdscr.addstr(row + i, 0, content_item['text'], content_item['attr'])
        except curses.error:
            pass

    # Show scroll indicators if there's more content
    total_content_lines = len(virtual_content)
    if total_content_lines > content_height_val:
        scroll_info = f"[{visible_start+1}-{min(visible_end, total_content_lines)}/{total_content_lines}]"
        try:
            stdscr.addstr(height - reserved_rows_notes_footer - 1, width - len(scroll_info) - 1, scroll_info, curses.color_pair(COLOR_PAIR_PAUSED))
        except curses.error:
            pass

    help_draw_start_y_notes = height - 1 - 1 - num_help_lines_notes_view
    if help_draw_start_y_notes >= row:
        for i, line in enumerate(help_lines_notes_view):
            try:
                if help_draw_start_y_notes + i < height -2:
                    stdscr.addstr(help_draw_start_y_notes + i, 0, line[:width])
            except curses.error: pass

    max_cmd_len_notes = width - 1
    max_buffer_len_notes = max_cmd_len_notes - len("> ")
    if max_buffer_len_notes < 0: max_buffer_len_notes = 0
    display_buffer_notes = command_buffer[:max_buffer_len_notes]
    command_line_text_notes = "> " + display_buffer_notes
    cursor_x_notes = len(command_line_text_notes)

    try:
        stdscr.addstr(height - 1, 0, " " * (width-1 if width > 0 else 0) )
        stdscr.addstr(height - 1, 0, command_line_text_notes.ljust(width-1 if width > 0 else 0), curses.color_pair(COLOR_PAIR_DEFAULT) | curses.A_BOLD)
        curses.curs_set(1)
        stdscr.move(height - 1, min(cursor_x_notes, width - 1 if width > 0 else 0))
    except curses.error: pass
    stdscr.refresh()
    return True