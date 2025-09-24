#!/usr/bin/env python3
"""
PR monitoring and polling functionality for pull requests.

This module handles:
- Polling pull request status
- Converting PR URLs to API URLs
- Checking for unhandled comments
- PR status notifications
"""

import time
import copy
import re
import sys
import requests
import threading
import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Any

import inc.config_manager
from inc.utils.constants import *
from inc.integrations.notification_service import send_desktop_notification
from inc.core.data_manager import data_manager
from inc.utils.formatters import format_subtask_for_title
from inc.helpers import t
from inc.utils.pr_formatters import overall_status_badge

def convert_to_api_url(pr_url):
    """Convert a pull request web URL to its API URL."""
    match = re.search(r'projects/(?P<projectKey>[^/]+)/repos/(?P<repositorySlug>[^/]+)/pull-requests/(?P<pullRequestId>\d+)', pr_url)
    if match:
        parts = match.groupdict()
        return f"{inc.config_manager.config.get('STASH_URL')}/rest/api/1.0/projects/{parts['projectKey']}/repos/{parts['repositorySlug']}/pull-requests/{parts['pullRequestId']}"
    return None


def fetch_pr_metadata(api_url: str, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Fetch PR metadata including basic info and reviewers."""
    try:
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching PR metadata from {api_url}: {e}", file=sys.stderr)
        return None


def fetch_pr_activities(api_url: str, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Fetch PR activities including comments and approvals."""
    try:
        activities_url = f"{api_url}/activities"
        response = requests.get(activities_url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching PR activities from {activities_url}: {e}", file=sys.stderr)
        return None


def fetch_pr_comments(api_url: str, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Fetch PR comments separately for more detailed comment data."""
    try:
        comments_url = f"{api_url}/comments"
        response = requests.get(comments_url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching PR comments from {comments_url}: {e}", file=sys.stderr)
        return None


def build_pr_details_v2(pr_url: str, pr_meta: Dict, activities: Dict, comments: Dict = None) -> Dict[str, Any]:
    """Build the new v2 pr_details structure from API responses."""
    
    pr_details = {
        "meta": {
            "id": pr_meta.get("id"),
            "title": pr_meta.get("title", "Unknown PR"),
            "description": pr_meta.get("description", ""),
            "author": {
                "id": pr_meta.get("author", {}).get("user", {}).get("name", "unknown"),
                "displayName": pr_meta.get("author", {}).get("user", {}).get("displayName", "Unknown"),
                "emailAddress": pr_meta.get("author", {}).get("user", {}).get("emailAddress", "")
            },
            "created": pr_meta.get("createdDate", 0),  # Unix timestamp
            "updated": pr_meta.get("updatedDate", 0),  # Unix timestamp
            "url": pr_url,
            "state": pr_meta.get("state", "OPEN"),
            "merge_status": "CAN_MERGE" if pr_meta.get("properties", {}).get("mergeResult", {}).get("outcome") == "CLEAN" else "CANNOT_MERGE"
        },
        "reviewers": [],
        "comments": [],
        "diffs": [],  # Will be populated later if needed
        "last_synced": datetime.now().isoformat() + 'Z',
        "version": 2
    }
    
    # Convert unix timestamps to ISO format
    if isinstance(pr_details["meta"]["created"], (int, float)):
        pr_details["meta"]["created"] = datetime.fromtimestamp(pr_details["meta"]["created"] / 1000).isoformat() + 'Z'
    if isinstance(pr_details["meta"]["updated"], (int, float)):
        pr_details["meta"]["updated"] = datetime.fromtimestamp(pr_details["meta"]["updated"] / 1000).isoformat() + 'Z'
    
    # Process reviewers
    for reviewer in pr_meta.get("reviewers", []):
        user = reviewer.get("user", {})
        status = reviewer.get("status", "UNAPPROVED")
        
        reviewer_data = {
            "id": user.get("name", "unknown"),
            "displayName": user.get("displayName", "Unknown"),
            "status": status,
            "approved_date": None
        }
        
        # Find approval date from activities if approved
        if status == "APPROVED":
            for activity in activities.get("values", []):
                if (activity.get("action") == "APPROVED" and 
                    activity.get("user", {}).get("name") == user.get("name")):
                    if isinstance(activity.get("createdDate"), (int, float)):
                        reviewer_data["approved_date"] = datetime.fromtimestamp(activity["createdDate"] / 1000).isoformat() + 'Z'
                    break
        
        pr_details["reviewers"].append(reviewer_data)
    
    # Process comments from activities
    for activity in activities.get("values", []):
        if activity.get("action") == "COMMENTED":
            comment = activity.get("comment")
            if comment:
                comment_data = {
                    "id": str(comment.get("id", "unknown")),
                    "parent_id": str(comment.get("parent", {}).get("id")) if comment.get("parent") else None,
                    "author": {
                        "id": comment.get("author", {}).get("name", "unknown"),
                        "displayName": comment.get("author", {}).get("displayName", "Unknown")
                    },
                    "text": comment.get("text", ""),
                    "created": comment.get("createdDate", 0),
                    "updated": comment.get("updatedDate", 0),
                    "imported": False
                }
                
                # Convert timestamps
                if isinstance(comment_data["created"], (int, float)):
                    comment_data["created"] = datetime.fromtimestamp(comment_data["created"] / 1000).isoformat() + 'Z'
                if isinstance(comment_data["updated"], (int, float)):
                    comment_data["updated"] = datetime.fromtimestamp(comment_data["updated"] / 1000).isoformat() + 'Z'
                
                pr_details["comments"].append(comment_data)
    
    # Sort comments by creation date
    pr_details["comments"].sort(key=lambda c: c.get("created", ""))
    
    return pr_details

def check_for_unhandled_comments(activities, my_user_id):
    """Check if there are unhandled comments in PR activities."""
    unhandled_comments = []
    for value in activities.get("values", []):
        if value.get("action") == "COMMENTED":
            comment = value.get("comment")
            if comment and comment.get("author", {}).get("id") != my_user_id:
                has_my_reply = False
                for reply in comment.get("comments", []):
                    if reply.get("author", {}).get("id") == my_user_id:
                        has_my_reply = True
                        break
                if not has_my_reply:
                    unhandled_comments.append(comment)
    return unhandled_comments

def poll_pull_requests(data_lock, data_ref):
    """Poll pull request statuses and update data with enhanced v2 schema."""
    api_token = inc.config_manager.config.get("API_TOKEN")
    my_user_id = inc.config_manager.config.get("USER_ID")

    while True:
        with data_lock:
            data_changed = False
            data_copy = copy.deepcopy(data_ref)

            for ticket, subtasks in data_copy.get("sub_tasks", {}).items():
                if not isinstance(subtasks, dict): 
                    continue
                for subtask_name, subtask_details in subtasks.items():
                    if not isinstance(subtask_details, dict): 
                        continue

                    original_subtask = data_ref["sub_tasks"][ticket][subtask_name]
                    pr_url = original_subtask.get("pr_url")
                    pr_status = original_subtask.get("pr_status")

                    if original_subtask.get("status") == "hidden" or not pr_url:
                        continue
                    
                    # Skip polling if PR is already merged AND we have recent data (< 1 hour old)
                    if pr_status == 'merged':
                        existing_pr_details = original_subtask.get('pr_details', {})
                        if existing_pr_details.get('version') == 2:
                            last_synced = existing_pr_details.get('last_synced')
                            if last_synced:
                                from datetime import datetime
                                try:
                                    last_sync_time = datetime.fromisoformat(last_synced.replace('Z', '+00:00'))
                                    now = datetime.now(last_sync_time.tzinfo)
                                    # Skip if synced within last hour
                                    if (now - last_sync_time).total_seconds() < 3600:
                                        continue
                                except:
                                    pass  # If parsing fails, continue with polling

                    api_url = convert_to_api_url(pr_url)
                    if not api_url: 
                        continue

                    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json;charset=UTF-8"}
                    
                    try:
                        # Fetch PR metadata (reviewers, basic info)
                        pr_meta = fetch_pr_metadata(api_url, headers)
                        if not pr_meta:
                            continue
                            
                        # Fetch activities (comments, approvals, merges)
                        activities = fetch_pr_activities(api_url, headers)
                        if not activities:
                            continue
                        
                        # Build new v2 pr_details structure
                        pr_details_v2 = build_pr_details_v2(pr_url, pr_meta, activities)
                        
                        # Merge with any existing imported comments from migration
                        existing_pr_details = original_subtask.get('pr_details', {})
                        if existing_pr_details.get('version') == 2:
                            # Preserve imported comments
                            imported_comments = [c for c in existing_pr_details.get('comments', []) if c.get('imported')]
                            # Combine with new comments (avoid duplicates by ID)
                            existing_ids = {c['id'] for c in pr_details_v2['comments']}
                            for imported_comment in imported_comments:
                                if imported_comment['id'] not in existing_ids:
                                    pr_details_v2['comments'].append(imported_comment)
                            # Re-sort by creation date
                            pr_details_v2['comments'].sort(key=lambda c: c.get('created', ''))
                        
                        # Store the new pr_details
                        original_subtask['pr_details'] = pr_details_v2
                        data_changed = True
                        
                        # Store in cache for persistence across restarts
                        from inc.integrations.pr_cache import store_pr_details_in_cache
                        store_pr_details_in_cache(pr_url, pr_details_v2)
                        
                        # Calculate derived status for backward compatibility
                        overall_status, _ = overall_status_badge(pr_details_v2)
                        
                        # Check for state changes to send notifications
                        state = pr_details_v2['meta']['state']
                        if state == 'MERGED':
                            if pr_status != 'merged':
                                original_subtask['pr_status'] = 'merged'
                                # Clean up old PR notes
                                notes = original_subtask.get('notes', [])
                                original_subtask['notes'] = [n for n in notes if not n.startswith("*PR* ") and not n.startswith(t('polling_note_approved'))]
                                data_changed = True
                                send_desktop_notification(
                                    t('notification_pr_merged_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), 
                                    t('notification_pr_merged_body', pr_url=pr_details_v2['meta']['url'])
                                )
                        elif 'approved' in overall_status:
                            if pr_status != 'approved':
                                original_subtask['pr_status'] = 'approved'
                                # Clean up old unhandled comment notes
                                notes = original_subtask.get('notes', [])
                                notes_to_keep = [n for n in notes if not n.startswith("*PR* ")]
                                if t('polling_note_approved') not in notes_to_keep:
                                    notes_to_keep.append(t('polling_note_approved'))
                                original_subtask['notes'] = notes_to_keep
                                data_changed = True
                                send_desktop_notification(
                                    t('notification_pr_approved_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), 
                                    t('notification_pr_approved_body', pr_url=pr_details_v2['meta']['url'])
                                )
                        else:
                            # Check for unhandled comments (comments from others without replies from user)
                            unhandled_comments = check_for_unhandled_comments(activities, my_user_id)
                            if unhandled_comments:
                                if pr_status != 'attention_needed':
                                    original_subtask['pr_status'] = 'attention_needed'
                                    data_changed = True
                                    send_desktop_notification(
                                        t('notification_pr_unhandled_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), 
                                        t('notification_pr_unhandled_body', pr_url=pr_details_v2['meta']['url'])
                                    )
                            else:
                                if pr_status == 'attention_needed':
                                    original_subtask['pr_status'] = None
                                    data_changed = True

                    except requests.exceptions.RequestException as e:
                        print(t('polling_err', url=api_url, e=e), file=sys.stderr)
                        pass

            if data_changed:
                data_manager.save_data(data_ref)

        time.sleep(60)  # Poll every 1 minute for faster updates


def poll_pr_data_sync(data_ref):
    """Synchronous PR polling for integration with main polling cycle."""
    api_token = inc.config_manager.config.get("API_TOKEN")
    my_user_id = inc.config_manager.config.get("USER_ID")
    
    if not api_token or api_token == "PASTE_YOUR_BEARER_TOKEN_HERE":
        return  # No valid API token, skip PR polling
    
    data_changed = False
    
    # Poll PR data for all subtasks with PR URLs
    for ticket, subtasks in data_ref.get("sub_tasks", {}).items():
        if not isinstance(subtasks, dict): 
            continue
        for subtask_name, subtask_details in subtasks.items():
            if not isinstance(subtask_details, dict): 
                continue

            pr_url = subtask_details.get("pr_url")
            pr_status = subtask_details.get("pr_status")

            if subtask_details.get("status") == "hidden" or not pr_url:
                continue
            
            # Skip polling if PR is already merged AND we have recent data (< 1 hour old)
            if pr_status == 'merged':
                existing_pr_details = subtask_details.get('pr_details', {})
                if existing_pr_details.get('version') == 2:
                    last_synced = existing_pr_details.get('last_synced')
                    if last_synced:
                        from datetime import datetime
                        try:
                            last_sync_time = datetime.fromisoformat(last_synced.replace('Z', '+00:00'))
                            now = datetime.now(last_sync_time.tzinfo)
                            # Skip if synced within last hour
                            if (now - last_sync_time).total_seconds() < 3600:
                                continue
                        except:
                            pass  # If parsing fails, continue with polling

            api_url = convert_to_api_url(pr_url)
            if not api_url: 
                continue

            headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json;charset=UTF-8"}
            
            try:
                # Fetch PR metadata (reviewers, basic info)
                pr_meta = fetch_pr_metadata(api_url, headers)
                if not pr_meta:
                    continue
                    
                # Fetch activities (comments, approvals, merges)
                activities = fetch_pr_activities(api_url, headers)
                if not activities:
                    continue
                
                # Build new v2 pr_details structure
                pr_details_v2 = build_pr_details_v2(pr_url, pr_meta, activities)
                
                # Merge with any existing imported comments from migration
                existing_pr_details = subtask_details.get('pr_details', {})
                if existing_pr_details.get('version') == 2:
                    # Preserve imported comments
                    imported_comments = [c for c in existing_pr_details.get('comments', []) if c.get('imported')]
                    # Combine with new comments (avoid duplicates by ID)
                    existing_ids = {c['id'] for c in pr_details_v2['comments']}
                    for imported_comment in imported_comments:
                        if imported_comment['id'] not in existing_ids:
                            pr_details_v2['comments'].append(imported_comment)
                    # Re-sort by creation date
                    pr_details_v2['comments'].sort(key=lambda c: c.get('created', ''))
                
                # Store the new pr_details
                subtask_details['pr_details'] = pr_details_v2
                data_changed = True
                
                # Store in cache for persistence across restarts
                from inc.integrations.pr_cache import store_pr_details_in_cache
                store_pr_details_in_cache(pr_url, pr_details_v2)
                
                # Calculate derived status for backward compatibility
                overall_status, _ = overall_status_badge(pr_details_v2)
                
                # Check for state changes to send notifications
                state = pr_details_v2['meta']['state']
                if state == 'MERGED':
                    if pr_status != 'merged':
                        subtask_details['pr_status'] = 'merged'
                        # Clean up old PR notes
                        notes = subtask_details.get('notes', [])
                        subtask_details['notes'] = [n for n in notes if not n.startswith("*PR* ") and not n.startswith(t('polling_note_approved'))]
                        data_changed = True
                        send_desktop_notification(
                            t('notification_pr_merged_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), 
                            t('notification_pr_merged_body', pr_url=pr_details_v2['meta']['url'])
                        )
                elif 'approved' in overall_status:
                    if pr_status != 'approved':
                        subtask_details['pr_status'] = 'approved'
                        # Clean up old unhandled comment notes
                        notes = subtask_details.get('notes', [])
                        notes_to_keep = [n for n in notes if not n.startswith("*PR* ")]
                        if t('polling_note_approved') not in notes_to_keep:
                            notes_to_keep.append(t('polling_note_approved'))
                        subtask_details['notes'] = notes_to_keep
                        data_changed = True
                        send_desktop_notification(
                            t('notification_pr_approved_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), 
                            t('notification_pr_approved_body', pr_url=pr_details_v2['meta']['url'])
                        )
                else:
                    # Check for unhandled comments (comments from others without replies from user)
                    unhandled_comments = check_for_unhandled_comments(activities, my_user_id)
                    if unhandled_comments:
                        if pr_status != 'attention_needed':
                            subtask_details['pr_status'] = 'attention_needed'
                            data_changed = True
                            send_desktop_notification(
                                t('notification_pr_unhandled_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), 
                                t('notification_pr_unhandled_body', pr_url=pr_details_v2['meta']['url'])
                            )
                    else:
                        if pr_status == 'attention_needed':
                            subtask_details['pr_status'] = None
                            data_changed = True

            except requests.exceptions.RequestException:
                # Silently skip network errors in sync mode to avoid spam
                continue
    
    if data_changed:
        from inc.core.data_manager import data_manager
        data_manager.save_data(data_ref)

# Global variable for tracking sent review notifications
sent_review_notifications = set()

def poll_reviews_needed():
    """Polls for pull requests that need the user's review."""
    global sent_review_notifications
    
    api_token = inc.config_manager.config.get("API_TOKEN")
    user_id = inc.config_manager.config.get("USER_ID")
    review_url = inc.config_manager.config.get("STASH_REVIEW_URL")

    if not all([api_token, user_id, review_url]) or "your-stash-instance.com" in review_url:
        return # Missing essential config or using placeholder

    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json;charset=UTF-8"}

    while True:
        try:
            response = requests.get(review_url, headers=headers, timeout=20)
            response.raise_for_status()
            prs_data = response.json()

            pending_reviews = []
            for pr in prs_data.get('values', []):
                for reviewer in pr.get('reviewers', []):
                    if reviewer.get('user', {}).get('id') == user_id and reviewer.get('status') == 'UNAPPROVED':
                        pending_reviews.append(pr)
                        # Handle notifications
                        if pr['id'] not in sent_review_notifications:
                            repo = f"{pr['links']['self'][0]['href']}"
                            notif_title = t('notification_review_title')
                            notif_body = t('notification_review_body', repo=repo, title=pr['title'])
                            send_desktop_notification(pr['title'], repo)
                            sent_review_notifications.add(pr['id'])
                        break # Move to next PR once user is found as unapproved reviewer

            # Update global pull requests for review (need to import this at module level)
            from jira_tracker import pull_requests_for_review, reviews_lock
            with reviews_lock:
                pull_requests_for_review.clear()
                pull_requests_for_review.extend(pending_reviews)

        except requests.exceptions.RequestException as e:
            print(t('polling_err', url=review_url, e=e), file=sys.stderr)
            pass # Silently continue on network errors

        # Clear sent notification list if no PRs are pending review, so user gets notified again if they reappear
        from jira_tracker import pull_requests_for_review, reviews_lock
        with reviews_lock:
             current_review_ids = {pr['id'] for pr in pull_requests_for_review}
             sent_review_notifications.intersection_update(current_review_ids)

        time.sleep(300) # Poll every 5 minutes
