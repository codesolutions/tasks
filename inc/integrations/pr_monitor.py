#!/usr/bin/env python3
"""
PR monitoring and polling functionality for GitHub pull requests using 'gh' CLI.

This module handles:
- Polling pull request status
- Fetching PR data using 'gh' CLI
- Mapping GitHub JSON to internal schema
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
import queue
import subprocess
import json
import os
from datetime import datetime, date
from typing import Dict, List, Optional, Any

import inc.config_manager
from inc.utils.constants import *
from inc.integrations.notification_service import send_desktop_notification
from inc.core.data_manager import data_manager
from inc.utils.formatters import format_subtask_for_title
from inc.helpers import t
from inc.utils.pr_formatters import overall_status_badge

# PR request queue system (similar to Jira queue)
pr_request_queue = queue.Queue()
pr_in_flight = set()  # Track PR URLs currently being processed

def _get_gh_env() -> Dict[str, str]:
    """Prepares the environment variables for subprocess calls."""
    env = os.environ.copy()
    token = inc.config_manager.config.get("API_TOKEN")
    if token and token != "PASTE_YOUR_GITHUB_PAT_HERE":
        env["GH_TOKEN"] = token
    return env

def _fetch_github_pr_details(pr_url: str) -> Optional[Dict[str, Any]]:
    """Fetch PR metadata, reviews, and comments using 'gh' CLI."""
    
    # Corrected JSON fields.
    # We request the parent objects (e.g., 'latestReviews') and
    # the gh CLI returns the full object, which our build function parses.
    json_fields = (
        "number,title,body,state,url,createdAt,updatedAt,mergeable,"
        "author,"
        "reviewRequests,"
        "latestReviews,"
        "comments"
    )
    
    # The 'gh' CLI is smart enough to parse the repo from the full URL.
    # No --repo flag is needed.
    command = [
        "gh", "pr", "view", pr_url,
        "--json", json_fields
    ]
    
    try:
        result = subprocess.run(
            command, 
            capture_output=True, 
            text=True, 
            check=True, 
            env=_get_gh_env()
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        logging.error(f"Error fetching GitHub PR {pr_url}: {e.stderr}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing GitHub PR JSON for {pr_url}: {e}")
        return None


def build_pr_details_v2(pr_url: str, gh_json: Dict) -> Dict[str, Any]:
    """Build the new v2 pr_details structure from GitHub's JSON response."""
    
    # Map GitHub state to internal state
    gh_state = gh_json.get("state", "OPEN").upper()
    internal_state = gh_state
    if gh_state == "CLOSED":
        internal_state = "DECLINED" # Assume closed means declined if not merged
    elif gh_state == "MERGED":
        internal_state = "MERGED"

    pr_details = {
        "meta": {
            "id": gh_json.get("number"),
            "title": gh_json.get("title", "Unknown PR"),
            "description": gh_json.get("body", ""),
            "author": {
                "id": gh_json.get("author", {}).get("login", "unknown"),
                "displayName": gh_json.get("author", {}).get("login", "Unknown"),
                "emailAddress": ""
            },
            "created": gh_json.get("createdAt", datetime.now().isoformat() + 'Z'),
            "updated": gh_json.get("updatedAt", datetime.now().isoformat() + 'Z'),
            "url": gh_json.get("url", pr_url),
            "state": internal_state,
            "merge_status": "CAN_MERGE" if gh_json.get("mergeable") == "MERGEABLE" else "CANNOT_MERGE",
            "notifications": {}  # Track notification states
        },
        "reviewers": [],
        "comments": [],
        "diffs": [],
        "last_synced": datetime.now().isoformat() + 'Z',
        "version": 2
    }

    reviewers = {} # Use dict to store latest status per reviewer

    # Process pending review requests
    for req in gh_json.get("reviewRequests", []):
        if req and req.get("login"):
            login = req["login"]
            reviewers[login] = {
                "id": login,
                "displayName": login,
                "status": "UNAPPROVED",
                "approved_date": None
            }

    # Process latest reviews to get current status
    for review in gh_json.get("latestReviews", []):
        if review and review.get("author", {}).get("login"):
            login = review["author"]["login"]
            status = "UNAPPROVED"
            approved_date = None
            
            gh_review_state = review.get("state", "").upper()
            if gh_review_state == "APPROVED":
                status = "APPROVED"
                approved_date = review.get("submittedAt")
            elif gh_review_state == "CHANGES_REQUESTED":
                status = "NEEDS_WORK"
            
            # Update status only if this review is newer or sets a final state
            if (login not in reviewers or 
                reviewers[login]["status"] == "UNAPPROVED" or 
                status != "UNAPPROVED"):
                
                reviewers[login] = {
                    "id": login,
                    "displayName": login,
                    "status": status,
                    "approved_date": approved_date
                }

    pr_details["reviewers"] = list(reviewers.values())

    # Process comments
    for comment in gh_json.get("comments", []):
        if not comment:
            continue
        
        pr_details["comments"].append({
            "id": str(comment.get("id", "unknown")),
            "parent_id": None, # GitHub JSON is flat, parent tracking not supported here
            "author": {
                "id": comment.get("author", {}).get("login", "unknown"),
                "displayName": comment.get("author", {}).get("login", "Unknown")
            },
            "text": comment.get("body", ""),
            "created": comment.get("createdAt", datetime.now().isoformat() + 'Z'),
            "updated": comment.get("createdAt", datetime.now().isoformat() + 'Z'),
            "imported": False
        })
    
    pr_details["comments"].sort(key=lambda c: c.get("created", ""))
    
    return pr_details

def check_for_unhandled_comments(pr_details_v2: Dict, my_user_name: str) -> bool:
    """Check if there are unhandled comments or reviews."""
    if not my_user_name:
        return False

    # Check for "NEEDS_WORK" status from any reviewer
    for reviewer in pr_details_v2.get("reviewers", []):
        if reviewer.get("status") == "NEEDS_WORK":
            return True # Someone requested changes

    # Check if the latest comment is not from the user
    all_comments = pr_details_v2.get("comments", [])
    if all_comments:
        latest_comment = all_comments[-1]
        if latest_comment.get("author", {}).get("id") != my_user_name:
            return True # Latest comment is from someone else

    return False


def queue_pr_for_polling(data_ref):
    """
    Queue all visible PR URLs for polling (non-blocking, similar to Jira system).
    This function should be called periodically to refresh PR data.
    """
    if not data_ref:
        return
    
    # Get all visible subtasks from current ticket
    current_ticket = data_ref.get("current_ticket")
    visible_pr_urls = set()
    
    if current_ticket:
        subtasks = data_ref.get("sub_tasks", {}).get(current_ticket, {})
        show_hidden = data_ref.get("show_hidden_tasks", False)
        
        for subtask_name, subtask_details in subtasks.items():
            if isinstance(subtask_details, dict) and (show_hidden or subtask_details.get("status") != "hidden"):
                pr_url = subtask_details.get("pr_url")
                if pr_url and "github.com" in pr_url: # Only queue GitHub URLs
                    visible_pr_urls.add((current_ticket, subtask_name, pr_url))
    
    # Also check paused tasks for PR URLs
    for paused_task in data_ref.get("paused_tasks", []):
        paused_ticket = paused_task.get("ticket")
        if paused_ticket:
            paused_subtasks = paused_task.get("sub_tasks", {})
            for subtask_name, subtask_details in paused_subtasks.items():
                if isinstance(subtask_details, dict) and subtask_details.get("status") != "hidden":
                    pr_url = subtask_details.get("pr_url")
                    if pr_url and "github.com" in pr_url: # Only queue GitHub URLs
                        visible_pr_urls.add((paused_ticket, subtask_name, pr_url))
    
    # Queue PR URLs that need refreshing
    current_time = time.time()
    PR_CACHE_TIMEOUT = 60  # Cache timeout in seconds (same as Jira)
    
    for ticket_name, subtask_name, pr_url in visible_pr_urls:
        # Check if PR was recently synced
        subtask_details = data_ref.get("sub_tasks", {}).get(ticket_name, {}).get(subtask_name, {})
        existing_pr_details = subtask_details.get('pr_details', {})
        
        should_fetch = True
        if existing_pr_details.get('version') == 2:
            last_synced = existing_pr_details.get('last_synced')
            if last_synced:
                try:
                    last_sync_time = datetime.fromisoformat(last_synced.replace('Z', '+00:00'))
                    now = datetime.now(last_sync_time.tzinfo)
                    if (now - last_sync_time).total_seconds() < PR_CACHE_TIMEOUT:
                        should_fetch = False
                except:
                    pass  # If parsing fails, continue with polling
        
        if should_fetch and pr_url not in pr_in_flight:
            pr_in_flight.add(pr_url)
            pr_request_queue.put((ticket_name, subtask_name, pr_url))
            logging.debug(f"Queued PR {pr_url} for polling")

def pr_queue_worker(stop_event, data_lock, data_ref):
    """
    Worker thread that processes PR data requests from a queue (similar to Jira queue worker).
    Runs in background and updates shared app data when PR data is fetched.
    """
    my_user_name = inc.config_manager.config.get("GITHUB_USERNAME")
    
    while not stop_event.is_set():
        try:
            # Get PR request from queue with timeout
            ticket_name, subtask_name, pr_url = pr_request_queue.get(timeout=1)
            
            logging.debug(f"Processing PR {pr_url} from queue")
            
            try:
                # Fetch PR metadata and activities
                gh_json = _fetch_github_pr_details(pr_url)
                if not gh_json:
                    continue
                
                # Build new v2 pr_details structure
                pr_details_v2 = build_pr_details_v2(pr_url, gh_json)
                
                # Update shared app data with data lock
                with data_lock:
                    # Check if the ticket/subtask still exists
                    if (ticket_name not in data_ref.get("sub_tasks", {}) or 
                        subtask_name not in data_ref.get("sub_tasks", {}).get(ticket_name, {})):
                        logging.debug("Ticket/subtask no longer exists, skipping PR update")
                        continue
                    
                    subtask_details = data_ref["sub_tasks"][ticket_name][subtask_name]
                    existing_pr_details = subtask_details.get('pr_details', {})
                    old_pr_status = subtask_details.get("pr_status")
                    
                    # Preserve existing notification state and imported comments
                    if existing_pr_details.get('version') == 2:
                        # Preserve existing notification state
                        existing_notifications = existing_pr_details.get('meta', {}).get('notifications', {})
                        if existing_notifications:
                            pr_details_v2['meta'].setdefault('notifications', {}).update(existing_notifications)
                            logging.debug(f"Preserved notification state for {subtask_name}")
                        
                        # Preserve imported comments
                        imported_comments = [c for c in existing_pr_details.get('comments', []) if c.get('imported')]
                        existing_ids = {c['id'] for c in pr_details_v2['comments']}
                        for imported_comment in imported_comments:
                            if imported_comment['id'] not in existing_ids:
                                pr_details_v2['comments'].append(imported_comment)
                        pr_details_v2['comments'].sort(key=lambda c: c.get('created', ''))
                    
                    # Store the updated pr_details
                    subtask_details['pr_details'] = pr_details_v2
                    
                    # Store in cache for persistence
                    from inc.integrations.pr_cache import store_pr_details_in_cache
                    store_pr_details_in_cache(pr_url, pr_details_v2)
                    
                    # Calculate new status and handle notifications
                    from inc.utils.pr_formatters import overall_status_badge
                    overall_status, _ = overall_status_badge(pr_details_v2)
                    
                    state = pr_details_v2['meta']['state']
                    new_status = None
                    
                    if state == 'MERGED':
                        new_status = 'merged'
                    elif 'approved' in overall_status:
                        new_status = 'approved'
                    else:
                        # Check for unhandled comments or changes requested
                        if check_for_unhandled_comments(pr_details_v2, my_user_name):
                            new_status = 'attention_needed'
                    
                    # Update status if changed
                    if new_status != old_pr_status:
                        subtask_details['pr_status'] = new_status
                        
                        # Clean up old PR notes for status changes
                        if new_status in ['merged', 'approved']:
                            notes = subtask_details.get('notes', [])
                            if new_status == 'merged':
                                subtask_details['notes'] = [n for n in notes if not n.startswith("*PR* ") and not n.startswith(t('polling_note_approved'))]
                            elif new_status == 'approved':
                                notes_to_keep = [n for n in notes if not n.startswith("*PR* ")]
                                if t('polling_note_approved') not in notes_to_keep:
                                    notes_to_keep.append(t('polling_note_approved'))
                                subtask_details['notes'] = notes_to_keep
                    
                    # Handle notifications
                    from inc.integrations.pr_notifications import handle_pr_notification_changes
                    try:
                        from jira_tracker import permanent_notifications
                        handle_pr_notification_changes(
                            data_ref, ticket_name, subtask_name, pr_details_v2, old_pr_status, new_status, permanent_notifications
                        )
                    except ImportError:
                        handle_pr_notification_changes(
                            data_ref, ticket_name, subtask_name, pr_details_v2, old_pr_status, new_status, None
                        )
                    
                    # Save data
                    data_manager.save_data(data_ref)
                    logging.debug(f"Updated PR data for {ticket_name}/{subtask_name}")
                
            except Exception as e:
                logging.error(f"Error processing PR {pr_url} in worker: {e}")
            
            # Mark as completed
            pr_request_queue.task_done()
            
        except queue.Empty:
            # Expected when queue is empty, just continue
            continue
        except Exception as e:
            logging.error(f"Error in PR queue worker: {e}")
        finally:
            # Always remove from in-flight set
            if 'pr_url' in locals() and pr_url in pr_in_flight:
                pr_in_flight.remove(pr_url)


# Global variable for tracking sent review notifications
sent_review_notifications = set()

def poll_reviews_needed():
    """Polls for pull requests that need the user's review using 'gh' CLI."""
    global sent_review_notifications
    
    github_org = inc.config_manager.config.get("GITHUB_ORG")

    if not github_org:
        logging.info("PR review poller exiting - GITHUB_ORG not configured.")
        return # Missing essential config

    # **FIX:** org: search qualifier is used in the query string, not as a flag.
    search_query = f"is:open review-requested:@me state:open org:{github_org}"

    while True:
        try:
            # **FIX:** Removed invalid '--org' flag and put query string in correct position
            command = [
                "gh", "search", "prs",
                search_query,
                "--json", "number,title,url,repository"
            ]
            
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                env=_get_gh_env()
            )
            
            pending_reviews = json.loads(result.stdout)

            # Reformat for compatibility with jira_tracker.py UI
            formatted_reviews = []
            for pr in pending_reviews:
                repo_name = pr.get('repository', {}).get('name', 'unknown-repo')
                repo_owner = pr.get('repository', {}).get('owner', {}).get('login', github_org)
                
                formatted_reviews.append({
                    'id': pr.get('number'),
                    'title': pr.get('title'),
                    'links': {'self': [{'href': pr.get('url')}]},
                    'toRef': {'repository': {'project': {'key': repo_owner}, 'name': repo_name}}
                })

            # Handle notifications
            for pr in formatted_reviews:
                if pr['id'] not in sent_review_notifications:
                    repo_info = f"{pr['toRef']['repository']['project']['key']}/{pr['toRef']['repository']['name']}"
                    notif_title = t('notification_review_title')
                    notif_body = t('notification_review_body', repo=repo_info, title=pr['title'])
                    send_desktop_notification(notif_title, notif_body)
                    sent_review_notifications.add(pr['id'])

            # Update global pull requests for review
            from jira_tracker import pull_requests_for_review, reviews_lock
            with reviews_lock:
                pull_requests_for_review.clear()
                pull_requests_for_review.extend(formatted_reviews)

        except subprocess.CalledProcessError as e:
            # Don't log an error if no results are found, that's normal
            if "no search results" not in e.stderr.lower():
                logging.info(f"Error polling for GitHub reviews: {e.stderr}")
        except json.JSONDecodeError as e:
            logging.info(f"Error parsing GitHub review JSON: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in poll_reviews_needed: {e}")

        # Clear sent notification list if no PRs are pending review
        from jira_tracker import pull_requests_for_review, reviews_lock
        with reviews_lock:
             current_review_ids = {pr['id'] for pr in pull_requests_for_review}
             sent_review_notifications.intersection_update(current_review_ids)

        time.sleep(300) # Poll every 5 minutes