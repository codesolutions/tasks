"""
Formatting utilities for text, time, and other display elements.

This module contains helper functions for formatting various types of data
for display in the terminal application.
"""

from datetime import timedelta
from typing import Optional


def format_timedelta_minutes(td: timedelta) -> str:
    """
    Format a timedelta as hours and minutes.
    
    Args:
        td: The timedelta to format
        
    Returns:
        Formatted string like "2h 30m" or "45m"
    """
    total_minutes = int(td.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"
    else:
        return f"{minutes}m"


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to fit within specified length.
    
    Args:
        text: Text to truncate
        max_length: Maximum allowed length
        suffix: Suffix to add when truncating
        
    Returns:
        Truncated text with suffix if needed
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def format_subtask_display_name(subtask: str, project_name: Optional[str] = None) -> str:
    """
    Format a subtask identifier for display.
    
    Args:
        subtask: Raw subtask identifier (could be URL or ticket ID)
        project_name: Project name if known
        
    Returns:
        Clean display name
    """
    if not subtask:
        return "N/A"
    
    # If already in [Project] TICKET format, return as is
    if subtask.startswith('[') and '] ' in subtask:
        return subtask
    
    # Extract from URL format
    if 'browse/' in subtask:
        ticket_id = subtask.split('browse/')[-1]
        if project_name:
            return f"[{project_name}] {ticket_id}"
        # Try to extract project from URL
        project_match = subtask.split('/browse/')[0].split('/')[-1] if '/' in subtask else None
        if project_match:
            return f"[{project_match}] {ticket_id}"
        return ticket_id
    
    # Direct ticket ID or other format
    if project_name:
        return f"[{project_name}] {subtask}"
    return subtask


def format_notification_text(text: str, max_width: int = 80) -> str:
    """
    Format notification text to fit terminal width.
    
    Args:
        text: Notification text
        max_width: Maximum line width
        
    Returns:
        Formatted text
    """
    if len(text) <= max_width:
        return text
    
    # Simple word wrapping
    words = text.split()
    lines = []
    current_line = []
    current_length = 0
    
    for word in words:
        if current_length + len(word) + 1 <= max_width:
            current_line.append(word)
            current_length += len(word) + 1
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
            current_length = len(word)
    
    if current_line:
        lines.append(' '.join(current_line))
    
    return '\n'.join(lines)


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Human-readable size string
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}TB"
