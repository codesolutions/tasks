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

import inc.config_manager
from inc.utils.constants import *
from inc.integrations.notification_service import send_desktop_notification
from inc.core.data_manager import data_manager
from inc.utils.formatters import format_subtask_for_title
from inc.helpers import t

def convert_to_api_url(pr_url):
    """Convert a pull request web URL to its API URL."""
    match = re.search(r'projects/(?P<projectKey>[^/]+)/repos/(?P<repositorySlug>[^/]+)/pull-requests/(?P<pullRequestId>\d+)', pr_url)
    if match:
        parts = match.groupdict()
        return f"{inc.config_manager.config.get('STASH_URL')}/rest/api/1.0/projects/{parts['projectKey']}/repos/{parts['repositorySlug']}/pull-requests/{parts['pullRequestId']}"
    return None

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
    """Poll pull request statuses and update data."""
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

                    if original_subtask.get("status") == "hidden" or not pr_url or pr_status == 'merged':
                        continue

                    api_url = convert_to_api_url(pr_url)
                    if not api_url: 
                        continue

                    headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json;charset=UTF-8"}
                    try:
                        reviewers_response = requests.get(api_url, headers=headers, timeout=10)
                        reviewers_response.raise_for_status()
                        reviewers = reviewers_response.json()

                        api_url = f"{convert_to_api_url(pr_url)}/activities"
                        response = requests.get(api_url, headers=headers, timeout=10)
                        response.raise_for_status()
                        activities = response.json()

                        is_merged = False
                        unique_approvers = set()
                        for activity in activities.get("values", []):
                            action = activity.get("action")
                            if action == "MERGED":
                                is_merged = True
                                break
                            if action == "APPROVED":
                                approver_id = activity.get("user", {}).get("id")
                                if approver_id:
                                    unique_approvers.add(approver_id)

                        # Format approvers
                        approvers_formatted = []
                        approver_count = 0
                        total_reviewers = len(reviewers.get('reviewers', []))
                        for r in reviewers.get('reviewers', []):
                            status_emoji = "❓" # Not responded
                            if r['status'] == 'APPROVED':
                                status_emoji = "✅"
                                approver_count += 1
                            elif r['status'] == 'NEEDS_WORK':
                                status_emoji = "❌"
                            approvers_formatted.append(f"{status_emoji} {r['user']['displayName']}")

                        # Determine overall status text
                        status_text = "waiting"
                        if activities.get('state') == 'MERGED':
                            status_text = "merged"
                        elif activities.get('state') == 'DECLINED':
                            status_text = "declined"
                        elif approver_count > 0:
                            status_text = f"approved ({approver_count}/{total_reviewers})"

                        # Store in the main data object
                        original_subtask = data_ref["sub_tasks"][ticket][subtask_name]
                        original_subtask['pr_details'] = {
                            'status_text': status_text,
                            'approvers_formatted': approvers_formatted
                        }
                        data_changed = True

                        if is_merged:
                            if pr_status != 'merged':
                                original_subtask['pr_status'] = 'merged'
                                notes = original_subtask.get('notes', [])
                                original_subtask['notes'] = [n for n in notes if not n.startswith("UNHANDLED") and not n.startswith(t('polling_note_approved'))]
                                data_changed = True
                                send_desktop_notification(t('notification_pr_merged_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), t('notification_pr_merged_body', pr_url=pr_url))
                        elif len(unique_approvers) >= 2:
                            if pr_status != 'approved':
                                original_subtask['pr_status'] = 'approved'
                                notes = original_subtask.get('notes', [])
                                notes_to_keep = [n for n in notes if not n.startswith("UNHANDLED")]
                                if t('polling_note_approved') not in notes_to_keep:
                                    notes_to_keep.append(t('polling_note_approved'))
                                original_subtask['notes'] = notes_to_keep
                                data_changed = True
                                send_desktop_notification(t('notification_pr_approved_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), t('notification_pr_approved_body', pr_url=pr_url))
                        else:
                            notes = original_subtask.get("notes", [])
                            notes_without_unhandled = [n for n in notes if not n.startswith("*PR* ")]
                            if len(notes_without_unhandled) < len(notes):
                                original_subtask["notes"] = notes_without_unhandled
                                data_changed = True

                            unhandled_comments = check_for_unhandled_comments(activities, my_user_id)
                            if unhandled_comments:
                                if pr_status != 'attention_needed':
                                    original_subtask['pr_status'] = 'attention_needed'
                                    data_changed = True
                                    send_desktop_notification(t('notification_pr_unhandled_title', main_task=ticket, sub_task=format_subtask_for_title(subtask_name)), t('notification_pr_unhandled_body', pr_url=pr_url))

                                for comment in unhandled_comments:
                                    note = t('polling_note_unhandled_comment', author=comment['author']['displayName'], text=comment['text'])
                                    if note not in original_subtask["notes"]:
                                        original_subtask["notes"].append(note)
                                        data_changed = True
                            else:
                                if pr_status == 'attention_needed':
                                    original_subtask['pr_status'] = None
                                    data_changed = True

                    except requests.exceptions.RequestException as e:
                        print(t('polling_err', url=api_url, e=e), file=sys.stderr)
                        pass

            if data_changed:
                data_manager.save_data(data_ref)

        time.sleep(300)

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
