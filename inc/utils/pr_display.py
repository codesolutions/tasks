#!/usr/bin/env python3
"""
PR comment display utilities that can be used in any view.
"""

import curses
from inc.utils.constants import COLOR_PAIR_PR_APPROVED
from inc.utils.pr_formatters import format_time_ago, wrap_comment
from inc.views.base_view import _draw_wrapped_text


def render_pr_comments_section(stdscr, pr_details_v2, row, effective_main_width, content_height_obj, 
                              max_comments=5, start_col=0, show_header=True, show_footer=True):
    """
    Render PR comments section that can be used in any view.
    
    Args:
        stdscr: The curses screen object
        pr_details_v2: PR details dictionary with v2 schema
        row: Starting row for rendering
        effective_main_width: Available width for content
        content_height_obj: List with available height (modified in place)
        max_comments: Maximum number of comments to show
        start_col: Starting column (for indentation)
        show_header: Whether to show the header border
        show_footer: Whether to show the footer border
        
    Returns:
        Number of lines used for rendering
    """
    if not pr_details_v2 or pr_details_v2.get('version') != 2:
        return 0
        
    pr_comments = pr_details_v2.get('comments', [])
    if not pr_comments or content_height_obj[0] <= 0:
        return 0
    
    lines_used_total = 0
    
    # PR Comments header
    if show_header:
        header_text = "┌─────PR COMMENTS─── ─── ── ── ─ ─  ─   ─"
        lines_used = _draw_wrapped_text(stdscr, header_text, row, start_col + 4,
            effective_main_width - start_col - 4, effective_main_width, content_height_obj,
            prefix="", subsequent_indent_offset=0,
            attr=curses.color_pair(COLOR_PAIR_PR_APPROVED))
        row += lines_used
        lines_used_total += lines_used
    
    # Display recent comments
    recent_comments = pr_comments[-max_comments:] if len(pr_comments) > max_comments else pr_comments
    for comment in recent_comments:
        if content_height_obj[0] <= 0: break
        if effective_main_width <= start_col + 4: break
        
        author = comment.get('author', {}).get('displayName', 'Unknown')
        created = comment.get('created', '')
        text = comment.get('text', '')
        
        # Format timestamp
        try:
            time_ago = format_time_ago(created)
            comment_header = f"{author} ({time_ago}):"
        except:
            comment_header = f"{author}:"
        
        # Display comment header first
        prefix_note = "| "
        header_start_col = start_col + 4
        max_text_width = effective_main_width - header_start_col - len(prefix_note)
        if max_text_width < 0: max_text_width = 0
        
        lines_used = _draw_wrapped_text(stdscr, comment_header, row, header_start_col,
                                      max_text_width, effective_main_width, content_height_obj,
                                      prefix=prefix_note, subsequent_indent_offset=len(prefix_note),
                                      attr=curses.color_pair(COLOR_PAIR_PR_APPROVED))
        row += lines_used
        lines_used_total += lines_used
        
        # Display comment text with proper word wrapping
        if text and content_height_obj[0] > 0:
            try:
                # Wrap the comment text properly
                wrapped_lines = wrap_comment(text, effective_main_width, indent=start_col + 6, subsequent_indent=start_col + 6)
                
                for line_text, line_color in wrapped_lines[:min(len(wrapped_lines), content_height_obj[0])]:
                    if content_height_obj[0] <= 0: break
                    try:
                        # Ensure the line fits within screen bounds and fill background
                        display_text = line_text[:effective_main_width] if len(line_text) > effective_main_width else line_text
                        # Pad the line to fill the entire width with background color
                        padded_text = display_text.ljust(effective_main_width)
                        stdscr.addstr(row, 0, padded_text, curses.color_pair(COLOR_PAIR_PR_APPROVED))
                        row += 1
                        content_height_obj[0] -= 1
                        lines_used_total += 1
                    except curses.error:
                        break
            except:
                # Fallback to simple wrapped text if formatting fails
                lines_used = _draw_wrapped_text(stdscr, text, row, header_start_col + 2,
                                              max_text_width - 2, effective_main_width, content_height_obj,
                                              prefix="  ", subsequent_indent_offset=2,
                                              attr=curses.color_pair(COLOR_PAIR_PR_APPROVED))
                row += lines_used
                lines_used_total += lines_used
        
        # Add a small separator between comments for readability
        if content_height_obj[0] > 0:
            try:
                # Create a separator line with full background
                separator_line = " " * (start_col + 4) + "|" + " " * (effective_main_width - start_col - 5)
                stdscr.addstr(row, 0, separator_line[:effective_main_width], curses.color_pair(COLOR_PAIR_PR_APPROVED))
                row += 1
                content_height_obj[0] -= 1
                lines_used_total += 1
            except curses.error:
                pass
    
    # Footer
    if show_footer and content_height_obj[0] > 0:
        footer_text = "└──────────── ─── ── ── ─ ─  ─   ─"
        lines_used = _draw_wrapped_text(stdscr, footer_text, row, start_col + 4,
            effective_main_width - start_col - 4, effective_main_width, content_height_obj,
            prefix="", subsequent_indent_offset=0,
            attr=curses.color_pair(COLOR_PAIR_PR_APPROVED))
        lines_used_total += lines_used
    
    return lines_used_total