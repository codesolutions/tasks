#!/usr/bin/env python3
"""
Formatting utilities for pull request display in the terminal.

This module provides functions to format PR comments, code blocks,
and status information for display in the curses-based UI.
"""

import re
import curses
from datetime import datetime, timezone
from typing import List, Tuple, Optional
from inc.utils.constants import *


def status_badge(reviewer_status: str) -> Tuple[str, int]:
    """
    Convert reviewer status to emoji badge and color pair.
    
    Args:
        reviewer_status: APPROVED, NEEDS_WORK, or UNAPPROVED
        
    Returns:
        Tuple of (emoji_text, color_pair_constant)
    """
    status_map = {
        'APPROVED': ('✅', COLOR_PAIR_PR_APPROVED),
        'NEEDS_WORK': ('❌', COLOR_PAIR_PR_UNHANDLED), 
        'UNAPPROVED': ('❓', COLOR_PAIR_DEFAULT)
    }
    return status_map.get(reviewer_status.upper(), ('❓', COLOR_PAIR_DEFAULT))


def overall_status_badge(pr_details: dict) -> Tuple[str, int]:
    """
    Calculate overall PR status from reviewers and state.
    
    Args:
        pr_details: The pr_details dict from the schema
        
    Returns:
        Tuple of (status_text, color_pair_constant)
    """
    meta = pr_details.get('meta', {})
    reviewers = pr_details.get('reviewers', [])
    
    state = meta.get('state', 'OPEN').upper()
    
    if state == 'MERGED':
        return ('merged', COLOR_PAIR_TASK_ALL_SUBTASKS_DONE)
    elif state == 'DECLINED':
        return ('declined', COLOR_PAIR_DEFAULT)
    
    # Calculate approval status
    total_reviewers = len(reviewers)
    approved_count = sum(1 for r in reviewers if r.get('status') == 'APPROVED')
    needs_work_count = sum(1 for r in reviewers if r.get('status') == 'NEEDS_WORK')
    
    if needs_work_count > 0:
        return ('needs work', COLOR_PAIR_PR_UNHANDLED)
    elif approved_count == total_reviewers and total_reviewers > 0:
        return ('approved', COLOR_PAIR_PR_APPROVED)
    elif approved_count > 0:
        return (f'approved ({approved_count}/{total_reviewers})', COLOR_PAIR_PR_APPROVED)
    else:
        return ('waiting', COLOR_PAIR_DEFAULT)


def format_time_ago(iso_timestamp: str) -> str:
    """
    Format an ISO timestamp as relative time (e.g., "2h ago", "3 days ago").
    
    Args:
        iso_timestamp: ISO format timestamp string
        
    Returns:
        Human readable relative time string
    """
    try:
        # Parse the timestamp
        if iso_timestamp.endswith('Z'):
            dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(iso_timestamp)
            
        # Calculate time difference
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            
        diff = now - dt
        
        # Format based on time difference
        total_seconds = int(diff.total_seconds())
        if total_seconds < 60:
            return "just now"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes}m ago"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            return f"{hours}h ago"
        else:
            days = total_seconds // 86400
            return f"{days}d ago"
            
    except (ValueError, TypeError):
        return "unknown"


def detect_code_blocks(text: str) -> List[Tuple[int, int, str]]:
    """
    Detect code blocks in text (both ``` and indented style).
    
    Args:
        text: The text to analyze
        
    Returns:
        List of tuples (start_line, end_line, block_type) where block_type
        is 'fenced' for ``` blocks or 'indented' for 4+ space indented blocks
    """
    lines = text.split('\n')
    code_blocks = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Check for fenced code blocks (```)
        if line.strip().startswith('```'):
            start_line = i
            i += 1
            # Find the closing ```
            while i < len(lines) and not lines[i].strip().startswith('```'):
                i += 1
            if i < len(lines):  # Found closing ```
                code_blocks.append((start_line, i, 'fenced'))
            i += 1
            
        # Check for indented code blocks (4+ spaces)
        elif len(line) > 0 and (line.startswith('    ') or line.startswith('\t')):
            start_line = i
            # Continue while lines are indented or empty
            while i < len(lines):
                current = lines[i]
                if len(current.strip()) == 0:  # Empty line
                    i += 1
                    continue
                elif current.startswith('    ') or current.startswith('\t'):  # Indented
                    i += 1
                else:  # Not indented, end of block
                    break
            if i > start_line + 1:  # At least 2 lines for a code block
                code_blocks.append((start_line, i - 1, 'indented'))
        else:
            i += 1
            
    return code_blocks


def render_code_block(text: str, width: int, indent: int = 0) -> List[Tuple[str, int]]:
    """
    Render a code block with proper formatting and color.
    
    Args:
        text: The code block text
        width: Available width for rendering
        indent: Left indent to apply
        
    Returns:
        List of tuples (line_text, color_pair) ready for curses display
    """
    lines = text.split('\n')
    rendered_lines = []
    available_width = max(1, width - indent)
    
    for line in lines:
        # Remove common indentation but preserve relative indentation
        if line.startswith('```'):
            continue  # Skip fence markers
            
        # Truncate if too long, but try to preserve important parts
        if len(line) > available_width:
            line = line[:available_width - 3] + '...'
            
        # Add indent and color
        padded_line = ' ' * indent + line
        rendered_lines.append((padded_line, COLOR_PAIR_CODE))
        
    return rendered_lines


def wrap_comment(text: str, width: int, indent: int = 0, subsequent_indent: int = None) -> List[Tuple[str, int]]:
    """
    Wrap a comment with proper handling of code blocks and text.
    
    Args:
        text: The comment text to wrap
        width: Available width for text
        indent: Left indent for first line
        subsequent_indent: Left indent for continuation lines (defaults to indent)
        
    Returns:
        List of tuples (line_text, color_pair) ready for curses display
    """
    if subsequent_indent is None:
        subsequent_indent = indent
        
    available_width = max(1, width - indent)
    subsequent_width = max(1, width - subsequent_indent)
    
    # Detect code blocks first
    code_blocks = detect_code_blocks(text)
    lines = text.split('\n')
    rendered_lines = []
    
    i = 0
    while i < len(lines):
        # Check if this line is part of a code block
        in_code_block = False
        for start, end, block_type in code_blocks:
            if start <= i <= end:
                in_code_block = True
                # Render the entire code block
                code_text = '\n'.join(lines[start:end + 1])
                code_lines = render_code_block(code_text, width, subsequent_indent)
                rendered_lines.extend(code_lines)
                i = end + 1
                break
                
        if not in_code_block:
            # Regular text line - wrap it
            line = lines[i]
            if len(line) == 0:
                rendered_lines.append((' ' * indent, COLOR_PAIR_DEFAULT))
            else:
                # Simple word wrapping with URL-aware handling
                words = line.split()
                current_line = ""
                line_indent = indent if len(rendered_lines) == 0 or rendered_lines[-1][0].strip() == "" else subsequent_indent
                current_width = width - line_indent
                
                for word in words:
                    # Special handling for URLs
                    is_url = word.startswith(('http://', 'https://', 'ftp://', 'www.'))
                    
                    if len(current_line) == 0:
                        if len(word) <= current_width:
                            current_line = word
                        else:
                            # Word too long, handle URLs specially
                            if is_url and current_width > 20:
                                # For URLs, try to break at logical points like '/' or '?'
                                break_point = current_width - 3
                                for i in range(break_point, max(0, break_point - 15), -1):
                                    if i < len(word) and word[i] in '/?&=':
                                        break_point = i + 1
                                        break
                                current_line = word[:break_point] + '...'
                            else:
                                current_line = word[:current_width - 3] + '...'
                    elif len(current_line + ' ' + word) <= current_width:
                        current_line += ' ' + word
                    else:
                        # Wrap to next line
                        rendered_lines.append((' ' * line_indent + current_line, COLOR_PAIR_DEFAULT))
                        
                        # Handle the new word that didn't fit
                        if len(word) <= current_width:
                            current_line = word
                        else:
                            # Word too long for the line, apply same URL logic
                            if is_url and current_width > 20:
                                break_point = current_width - 3
                                for i in range(break_point, max(0, break_point - 15), -1):
                                    if i < len(word) and word[i] in '/?&=':
                                        break_point = i + 1
                                        break
                                current_line = word[:break_point] + '...'
                            else:
                                current_line = word[:current_width - 3] + '...'
                        
                        line_indent = subsequent_indent
                        current_width = width - line_indent
                        
                if current_line:
                    rendered_lines.append((' ' * line_indent + current_line, COLOR_PAIR_DEFAULT))
            i += 1
            
    return rendered_lines


def format_reviewer_list(reviewers: List[dict], width: int) -> List[Tuple[str, int]]:
    """
    Format a list of reviewers with status badges.
    
    Args:
        reviewers: List of reviewer dicts from pr_details
        width: Available width for formatting
        
    Returns:
        List of formatted lines with color pairs
    """
    if not reviewers:
        return [('  No reviewers assigned', COLOR_PAIR_DEFAULT)]
        
    lines = []
    for reviewer in reviewers:
        status = reviewer.get('status', 'UNAPPROVED')
        name = reviewer.get('displayName', reviewer.get('id', 'Unknown'))
        badge, color = status_badge(status)
        
        line = f'  {badge} {name}'
        if len(line) > width:
            line = line[:width - 3] + '...'
        lines.append((line, color))
        
    return lines


def format_pr_header(pr_details: dict, width: int) -> List[Tuple[str, int]]:
    """
    Format the PR header with title and overall status.
    
    Args:
        pr_details: The complete PR details dict
        width: Available width for header
        
    Returns:
        List of formatted header lines
    """
    meta = pr_details.get('meta', {})
    title = meta.get('title', 'Unknown PR')
    
    # Get overall status
    status_text, status_color = overall_status_badge(pr_details)
    
    # Format main header line
    header_text = f"[PR] {title} - {status_text}"
    if len(header_text) > width:
        # Truncate title but keep status
        max_title_len = width - len(f"[PR]  - {status_text}")
        if max_title_len > 10:
            title = title[:max_title_len - 3] + '...'
            header_text = f"[PR] {title} - {status_text}"
        else:
            header_text = header_text[:width - 3] + '...'
    
    lines = [(header_text, status_color)]
    
    # Add author and timestamp if space allows
    if width > 30:
        author = meta.get('author', {}).get('displayName', 'Unknown')
        created = format_time_ago(meta.get('created', ''))
        meta_line = f"Author: {author}  Created: {created}"
        if len(meta_line) > width:
            meta_line = meta_line[:width - 3] + '...'
        lines.append((meta_line, COLOR_PAIR_DEFAULT))
    
    return lines