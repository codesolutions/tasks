#!/usr/bin/env python3
"""
PR notification management system that tracks notification state in PR details.

This module manages permanent notifications for PR status changes and integrates
with the existing notification system.
"""

import re
import sys
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from inc.helpers import t
from inc.integrations.notification_service import send_desktop_notification
from inc.utils.formatters import format_subtask_for_title


def get_jira_ticket_id_from_subtask_name(subtask_name: str) -> Optional[str]:
    """
    Extract Jira ticket ID from subtask name (URL).
    
    Args:
        subtask_name: The subtask name (typically a Jira URL)
        
    Returns:
        Jira ticket ID or None if not found
    """
    # Match patterns like DCVEIK-1562, DCPAM-1573, etc.
    match = re.search(r'([A-Z]+[A-Z0-9]*-\d+)', subtask_name)
    return match.group(1) if match else None


def format_notification_datetime(iso_datetime: str) -> str:
    """
    Format ISO datetime for notifications in Finnish format.
    
    Args:
        iso_datetime: ISO format datetime string
        
    Returns:
        Formatted date/time string like "24.9.2025 19.15"
    """
    try:
        if iso_datetime.endswith('Z'):
            dt = datetime.fromisoformat(iso_datetime.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(iso_datetime)
        return dt.strftime('%d.%m.%Y %H.%M')
    except:
        return "unknown time"


def get_latest_approval_date(pr_details: Dict) -> Optional[str]:
    """
    Get the latest approval date from PR reviewers.
    
    Args:
        pr_details: PR details dictionary
        
    Returns:
        Latest approval date in ISO format or None
    """
    latest_date = None
    for reviewer in pr_details.get('reviewers', []):
        if reviewer.get('status') == 'APPROVED' and reviewer.get('approved_date'):
            if not latest_date or reviewer['approved_date'] > latest_date:
                latest_date = reviewer['approved_date']
    return latest_date


def should_send_notification(pr_details: Dict, notification_type: str) -> bool:
    """
    Check if a notification should be sent based on tracked state.
    
    Args:
        pr_details: PR details dictionary
        notification_type: Type of notification (APPROVED, MERGED, ATTENTION_NEEDED)
        
    Returns:
        True if notification should be sent
    """
    notifications = pr_details.get('meta', {}).get('notifications', {})
    return notification_type not in notifications


def mark_notification_sent(pr_details: Dict, notification_type: str, additional_data: Dict = None):
    """
    Mark a notification as sent and store metadata.
    
    Args:
        pr_details: PR details dictionary to update
        notification_type: Type of notification sent
        additional_data: Additional data to store with the notification
    """
    if 'meta' not in pr_details:
        pr_details['meta'] = {}
    if 'notifications' not in pr_details['meta']:
        pr_details['meta']['notifications'] = {}
    
    notification_data = {
        'sent': datetime.now().isoformat() + 'Z'
    }
    
    if additional_data:
        notification_data.update(additional_data)
    
    pr_details['meta']['notifications'][notification_type] = notification_data


def create_approval_notification(ticket_name: str, subtask_name: str, pr_details: Dict) -> str:
    """
    Create an approval notification message with proper formatting.
    
    Args:
        ticket_name: Project name
        subtask_name: Subtask name/URL
        pr_details: PR details dictionary
        
    Returns:
        Formatted notification message
    """
    jira_id = get_jira_ticket_id_from_subtask_name(subtask_name)
    latest_approval = get_latest_approval_date(pr_details)
    
    if jira_id and latest_approval:
        formatted_date = format_notification_datetime(latest_approval)
        return f"{ticket_name}/{jira_id}: PR approved on {formatted_date}. Please merge!"
    else:
        return f"{ticket_name}: PR approved. Please merge!"


def create_merged_notification(ticket_name: str, subtask_name: str, pr_details: Dict) -> str:
    """
    Create a merged notification message with proper formatting.
    
    Args:
        ticket_name: Project name
        subtask_name: Subtask name/URL
        pr_details: PR details dictionary
        
    Returns:
        Formatted notification message
    """
    jira_id = get_jira_ticket_id_from_subtask_name(subtask_name)
    merge_time = pr_details.get('meta', {}).get('updated')
    
    if jira_id and merge_time:
        formatted_date = format_notification_datetime(merge_time)
        return f"{ticket_name}/{jira_id}: PR merged on {formatted_date}"
    else:
        return f"{ticket_name}: PR merged"


def create_attention_notification(ticket_name: str, subtask_name: str) -> str:
    """
    Create an attention needed notification message.
    
    Args:
        ticket_name: Project name
        subtask_name: Subtask name/URL
        
    Returns:
        Formatted notification message
    """
    jira_id = get_jira_ticket_id_from_subtask_name(subtask_name)
    if jira_id:
        return f"{ticket_name}/{jira_id}: PR attention needed!"
    else:
        return f"{ticket_name}: PR attention needed!"


def update_permanent_notifications(
    permanent_notifications: List[str], 
    ticket_name: str, 
    subtasks: Dict, 
    action: str = "update"
) -> bool:
    """
    Update permanent notifications based on current PR status.
    
    Args:
        permanent_notifications: List of permanent notifications to modify
        ticket_name: Project name
        subtasks: Dictionary of subtasks for the project
        action: "update" or "remove_all"
        
    Returns:
        True if notifications were modified
    """
    if not isinstance(subtasks, dict):
        return False
    
    modified = False
    
    # Remove all existing PR notifications for this ticket
    old_notifications = permanent_notifications[:]
    permanent_notifications[:] = [
        notif for notif in permanent_notifications 
        if not (notif.startswith(f"{ticket_name}/") or notif.startswith(f"{ticket_name}:"))
        or not ("PR" in notif and ("approved" in notif or "attention" in notif or "merged" in notif))
    ]
    
    if len(permanent_notifications) != len(old_notifications):
        modified = True
    
    if action == "remove_all":
        return modified
    
    # Add current PR notifications based on subtask status and PR details
    for subtask_name, subtask_details in subtasks.items():
        if not isinstance(subtask_details, dict):
            continue
            
        pr_url = subtask_details.get("pr_url")
        pr_details = subtask_details.get("pr_details", {})
        
        if not pr_url or pr_details.get('version') != 2:
            continue
        
        pr_state = pr_details.get('meta', {}).get('state', 'OPEN')
        notifications = pr_details.get('meta', {}).get('notifications', {})
        
        # Don't show notifications for merged PRs (they're done)
        if pr_state == 'MERGED':
            continue
        
        pr_status = subtask_details.get("pr_status")
        
        if pr_status == 'approved':
            # Only show approval notification if not already notified
            if 'APPROVED' not in notifications:
                approval_msg = create_approval_notification(ticket_name, subtask_name, pr_details)
                if approval_msg not in permanent_notifications:
                    permanent_notifications.append(approval_msg)
                    modified = True
        elif pr_status == 'attention_needed':
            attention_msg = create_attention_notification(ticket_name, subtask_name)
            if attention_msg not in permanent_notifications:
                permanent_notifications.append(attention_msg)
                modified = True
    
    return modified


def handle_pr_notification_changes(
    data_ref: Dict, 
    ticket_name: str, 
    subtask_name: str, 
    pr_details: Dict,
    old_status: str,
    new_status: str,
    permanent_notifications: List[str] = None
) -> bool:
    """
    Handle PR notification changes when status changes.
    
    Args:
        data_ref: Main application data
        ticket_name: Project name
        subtask_name: Subtask name
        pr_details: PR details dictionary
        old_status: Previous PR status
        new_status: New PR status
        permanent_notifications: List of permanent notifications to update
        
    Returns:
        True if data was modified
    """
    data_changed = False
    pr_state = pr_details.get('meta', {}).get('state', 'OPEN')
    
    # Handle merged PRs
    if pr_state == 'MERGED' and should_send_notification(pr_details, 'MERGED'):
        send_desktop_notification(
            t('notification_pr_merged_title', main_task=ticket_name, sub_task=format_subtask_for_title(subtask_name)),
            t('notification_pr_merged_body', pr_url=pr_details['meta']['url'])
        )
        mark_notification_sent(pr_details, 'MERGED')
        data_changed = True
        
        # Remove approval notifications since PR is merged
        if permanent_notifications:
            update_permanent_notifications(
                permanent_notifications, ticket_name, 
                data_ref.get("sub_tasks", {}).get(ticket_name, {}), 
                action="update"
            )
    
    # Handle approved PRs
    elif new_status == 'approved' and should_send_notification(pr_details, 'APPROVED'):
        latest_approval = get_latest_approval_date(pr_details)
        send_desktop_notification(
            t('notification_pr_approved_title', main_task=ticket_name, sub_task=format_subtask_for_title(subtask_name)),
            t('notification_pr_approved_body', pr_url=pr_details['meta']['url'])
        )
        mark_notification_sent(pr_details, 'APPROVED', {'approved_date': latest_approval})
        data_changed = True
        
        # Update permanent notifications
        if permanent_notifications:
            update_permanent_notifications(
                permanent_notifications, ticket_name, 
                data_ref.get("sub_tasks", {}).get(ticket_name, {}), 
                action="update"
            )
    
    # Handle attention needed
    elif new_status == 'attention_needed':
        notifications = pr_details.get('meta', {}).get('notifications', {})
        if should_send_notification(pr_details, 'ATTENTION_NEEDED'):
            logging.info(f"DEBUG: Sending ATTENTION_NEEDED desktop notification for {ticket_name}/{subtask_name}")
            send_desktop_notification(
                t('notification_pr_unhandled_title', main_task=ticket_name, sub_task=format_subtask_for_title(subtask_name)),
                t('notification_pr_unhandled_body', pr_url=pr_details['meta']['url'])
            )
            mark_notification_sent(pr_details, 'ATTENTION_NEEDED')
            data_changed = True
        else:
            logging.info(f"DEBUG: Skipping ATTENTION_NEEDED desktop notification for {ticket_name}/{subtask_name} - already sent. Notifications: {notifications}")
        
        # Update permanent notifications
        if permanent_notifications:
            update_permanent_notifications(
                permanent_notifications, ticket_name, 
                data_ref.get("sub_tasks", {}).get(ticket_name, {}), 
                action="update"
            )
    
    return data_changed