#!/usr/bin/env python3
"""
PR monitoring and polling functionality for Bitbucket Cloud pull requests.

This module handles:
- Polling pull request status via the Bitbucket REST API
- Fetching PR metadata, participants, and comments
- Mapping Bitbucket JSON to the internal v2 schema
- Checking for unhandled comments
- PR status notifications
"""

import time
import re
import threading
import logging
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

import requests

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

API_BASE = "https://api.bitbucket.org/2.0"

# Cached current Bitbucket user info (uuid/display_name/account_id), populated lazily.
_current_user_info: Optional[Dict[str, str]] = None
_current_user_lock = threading.Lock()


def _get_auth() -> Optional[Tuple[str, str]]:
    """Return a requests auth tuple from config, or None if not configured."""
    username = inc.config_manager.config.get("BB_USERNAME")
    password = inc.config_manager.config.get("BB_APP_PASSWORD")
    if not username or not password or password.startswith("PASTE_"):
        return None
    return (username, password)


def _get_workspace() -> Optional[str]:
    """Return the configured Bitbucket workspace slug."""
    return inc.config_manager.config.get("BB_WORKSPACE")


def get_current_user() -> Optional[Dict[str, str]]:
    """Return the currently authenticated Bitbucket user, fetching lazily once."""
    global _current_user_info
    with _current_user_lock:
        if _current_user_info is not None:
            return _current_user_info
        auth = _get_auth()
        if not auth:
            return None
        try:
            resp = requests.get(f"{API_BASE}/user", auth=auth, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            _current_user_info = {
                "uuid": data.get("uuid", ""),
                "username": data.get("username", ""),
                "display_name": data.get("display_name", ""),
                "account_id": data.get("account_id", ""),
            }
            return _current_user_info
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to fetch Bitbucket user info: {e}")
            return None


def parse_bitbucket_pr_url(pr_url: str) -> Optional[Tuple[str, str, str]]:
    """Parse a Bitbucket Cloud PR URL and return (workspace, repo_slug, pr_id)."""
    if not pr_url:
        return None
    match = re.match(
        r"https?://bitbucket\.org/([^/]+)/([^/]+)/pull-requests/(\d+)",
        pr_url.strip(),
    )
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def is_bitbucket_pr_url(pr_url: str) -> bool:
    """Return True if the URL looks like a Bitbucket Cloud PR URL."""
    return parse_bitbucket_pr_url(pr_url) is not None


def _api_get(url: str, params: Optional[Dict] = None, session: Optional[requests.Session] = None) -> Optional[Dict]:
    """Single GET against the Bitbucket API returning parsed JSON, or None on failure."""
    auth = _get_auth()
    if not auth:
        return None
    try:
        s = session if session is not None else requests
        resp = s.get(url, params=params, auth=auth, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Bitbucket API GET failed for {url}: {e}")
        return None


def _api_get_paginated(url: str, params: Optional[Dict] = None, max_pages: int = 10) -> List[Dict]:
    """Fetch all values from a paginated Bitbucket endpoint."""
    auth = _get_auth()
    if not auth:
        return []
    results: List[Dict] = []
    next_url = url
    next_params = params
    pages = 0
    try:
        while next_url and pages < max_pages:
            resp = requests.get(next_url, params=next_params, auth=auth, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("values", []))
            next_url = data.get("next")
            next_params = None  # next URL already includes params
            pages += 1
    except requests.exceptions.RequestException as e:
        logging.error(f"Bitbucket paginated GET failed for {url}: {e}")
    return results


def _fetch_bitbucket_pr_details(pr_url: str) -> Optional[Dict[str, Any]]:
    """Fetch PR metadata (with participants) from the Bitbucket API."""
    parsed = parse_bitbucket_pr_url(pr_url)
    if not parsed:
        logging.warning(f"Not a recognizable Bitbucket PR URL: {pr_url}")
        return None
    workspace, repo_slug, pr_id = parsed
    url = f"{API_BASE}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}"
    return _api_get(url)


def _fetch_bitbucket_pr_comments(pr_url: str) -> List[Dict[str, Any]]:
    """Fetch all comments for a PR (paginated)."""
    parsed = parse_bitbucket_pr_url(pr_url)
    if not parsed:
        return []
    workspace, repo_slug, pr_id = parsed
    url = f"{API_BASE}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/comments"
    # Bitbucket returns both active and deleted; filter out deleted.
    raw = _api_get_paginated(url, params={"pagelen": 100})
    return [c for c in raw if not c.get("deleted")]


def _map_reviewer_status(participant: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Map a Bitbucket participant entry to (status, approved_date)."""
    state = (participant.get("state") or "").lower()
    if participant.get("approved") or state == "approved":
        return ("APPROVED", participant.get("participated_on"))
    if state == "changes_requested":
        return ("NEEDS_WORK", None)
    return ("UNAPPROVED", None)


def build_pr_details_v2(pr_url: str, bb_pr: Dict[str, Any], bb_comments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the v2 pr_details structure from Bitbucket JSON responses."""
    bb_state = (bb_pr.get("state") or "OPEN").upper()
    # Bitbucket states: OPEN, MERGED, DECLINED, SUPERSEDED
    if bb_state == "MERGED":
        internal_state = "MERGED"
    elif bb_state in ("DECLINED", "SUPERSEDED"):
        internal_state = "DECLINED"
    else:
        internal_state = "OPEN"

    author = bb_pr.get("author", {}) or {}
    author_id = author.get("uuid") or author.get("account_id") or author.get("nickname") or "unknown"

    pr_details = {
        "meta": {
            "id": bb_pr.get("id"),
            "title": bb_pr.get("title", "Unknown PR"),
            "description": bb_pr.get("description", ""),
            "author": {
                "id": author_id,
                "displayName": author.get("display_name", "Unknown"),
                "emailAddress": "",
            },
            "created": bb_pr.get("created_on") or datetime.utcnow().isoformat() + "Z",
            "updated": bb_pr.get("updated_on") or datetime.utcnow().isoformat() + "Z",
            "url": (bb_pr.get("links", {}) or {}).get("html", {}).get("href") or pr_url,
            "state": internal_state,
            "merge_status": "CAN_MERGE" if internal_state == "OPEN" else "CANNOT_MERGE",
            "notifications": {},
        },
        "reviewers": [],
        "comments": [],
        "diffs": [],
        "last_synced": datetime.utcnow().isoformat() + "Z",
        "version": 2,
    }

    # Reviewers are the participants with role == REVIEWER.
    reviewers: Dict[str, Dict[str, Any]] = {}
    for participant in bb_pr.get("participants", []) or []:
        role = (participant.get("role") or "").upper()
        if role != "REVIEWER":
            continue
        user = participant.get("user", {}) or {}
        user_id = user.get("uuid") or user.get("account_id") or user.get("nickname")
        if not user_id:
            continue
        status, approved_date = _map_reviewer_status(participant)
        reviewers[user_id] = {
            "id": user_id,
            "displayName": user.get("display_name", user_id),
            "status": status,
            "approved_date": approved_date,
        }
    pr_details["reviewers"] = list(reviewers.values())

    # Comments.
    for comment in bb_comments:
        user = comment.get("user", {}) or {}
        user_id = user.get("uuid") or user.get("account_id") or user.get("nickname") or "unknown"
        content = (comment.get("content") or {}).get("raw", "")
        parent = comment.get("parent") or {}
        parent_id = parent.get("id") if isinstance(parent, dict) else None
        pr_details["comments"].append({
            "id": str(comment.get("id", "unknown")),
            "parent_id": str(parent_id) if parent_id else None,
            "author": {
                "id": user_id,
                "displayName": user.get("display_name", user_id),
            },
            "text": content,
            "created": comment.get("created_on") or datetime.utcnow().isoformat() + "Z",
            "updated": comment.get("updated_on") or comment.get("created_on") or datetime.utcnow().isoformat() + "Z",
            "imported": False,
        })

    pr_details["comments"].sort(key=lambda c: c.get("created", ""))
    return pr_details


def check_for_unhandled_comments(pr_details_v2: Dict, my_user_id: Optional[str]) -> bool:
    """Check if there are unhandled comments or reviews requiring attention."""
    if not my_user_id:
        return False

    # Someone requested changes
    for reviewer in pr_details_v2.get("reviewers", []):
        if reviewer.get("status") == "NEEDS_WORK":
            return True

    # Latest comment is from someone other than the current user
    all_comments = pr_details_v2.get("comments", [])
    if all_comments:
        latest = all_comments[-1]
        if latest.get("author", {}).get("id") != my_user_id:
            return True

    return False


def queue_pr_for_polling(data_ref):
    """Queue all visible Bitbucket PR URLs for polling (non-blocking)."""
    if not data_ref:
        return

    current_ticket = data_ref.get("current_ticket")
    visible_pr_urls = set()

    if current_ticket:
        subtasks = data_ref.get("sub_tasks", {}).get(current_ticket, {})
        show_hidden = data_ref.get("show_hidden_tasks", False)

        for subtask_name, subtask_details in subtasks.items():
            if isinstance(subtask_details, dict) and (show_hidden or subtask_details.get("status") != "hidden"):
                pr_url = subtask_details.get("pr_url")
                if pr_url and is_bitbucket_pr_url(pr_url):
                    visible_pr_urls.add((current_ticket, subtask_name, pr_url))

    # Paused tasks too
    for paused_task in data_ref.get("paused_tasks", []):
        paused_ticket = paused_task.get("ticket")
        if not paused_ticket:
            continue
        for subtask_name, subtask_details in paused_task.get("sub_tasks", {}).items():
            if isinstance(subtask_details, dict) and subtask_details.get("status") != "hidden":
                pr_url = subtask_details.get("pr_url")
                if pr_url and is_bitbucket_pr_url(pr_url):
                    visible_pr_urls.add((paused_ticket, subtask_name, pr_url))

    PR_CACHE_TIMEOUT = 60  # seconds

    for ticket_name, subtask_name, pr_url in visible_pr_urls:
        subtask_details = data_ref.get("sub_tasks", {}).get(ticket_name, {}).get(subtask_name, {})
        existing_pr_details = subtask_details.get("pr_details", {})

        should_fetch = True
        if existing_pr_details.get("version") == 2:
            last_synced = existing_pr_details.get("last_synced")
            if last_synced:
                try:
                    last_sync_time = datetime.fromisoformat(last_synced.replace("Z", "+00:00"))
                    now = datetime.now(last_sync_time.tzinfo)
                    if (now - last_sync_time).total_seconds() < PR_CACHE_TIMEOUT:
                        should_fetch = False
                except Exception:
                    pass

        if should_fetch and pr_url not in pr_in_flight:
            pr_in_flight.add(pr_url)
            pr_request_queue.put((ticket_name, subtask_name, pr_url))
            logging.debug(f"Queued Bitbucket PR {pr_url} for polling")


def pr_queue_worker(stop_event, data_lock, data_ref):
    """Worker thread that processes PR data requests from a queue."""
    while not stop_event.is_set():
        pr_url = None
        try:
            ticket_name, subtask_name, pr_url = pr_request_queue.get(timeout=1)

            logging.debug(f"Processing Bitbucket PR {pr_url} from queue")

            try:
                bb_pr = _fetch_bitbucket_pr_details(pr_url)
                if not bb_pr:
                    continue

                bb_comments = _fetch_bitbucket_pr_comments(pr_url)
                pr_details_v2 = build_pr_details_v2(pr_url, bb_pr, bb_comments)

                # Determine "my" user id once per cycle
                user_info = get_current_user()
                my_user_id = user_info.get("uuid") if user_info else None

                with data_lock:
                    if (ticket_name not in data_ref.get("sub_tasks", {})
                            or subtask_name not in data_ref.get("sub_tasks", {}).get(ticket_name, {})):
                        logging.debug("Ticket/subtask no longer exists, skipping PR update")
                        continue

                    subtask_details = data_ref["sub_tasks"][ticket_name][subtask_name]
                    existing_pr_details = subtask_details.get("pr_details", {})
                    old_pr_status = subtask_details.get("pr_status")

                    if existing_pr_details.get("version") == 2:
                        existing_notifications = existing_pr_details.get("meta", {}).get("notifications", {})
                        if existing_notifications:
                            pr_details_v2["meta"].setdefault("notifications", {}).update(existing_notifications)

                        imported_comments = [c for c in existing_pr_details.get("comments", []) if c.get("imported")]
                        existing_ids = {c["id"] for c in pr_details_v2["comments"]}
                        for imported_comment in imported_comments:
                            if imported_comment["id"] not in existing_ids:
                                pr_details_v2["comments"].append(imported_comment)
                        pr_details_v2["comments"].sort(key=lambda c: c.get("created", ""))

                    subtask_details["pr_details"] = pr_details_v2

                    from inc.integrations.pr_cache import store_pr_details_in_cache
                    store_pr_details_in_cache(pr_url, pr_details_v2)

                    overall_status, _ = overall_status_badge(pr_details_v2)
                    state = pr_details_v2["meta"]["state"]
                    new_status = None

                    if state == "MERGED":
                        new_status = "merged"
                    elif "approved" in overall_status:
                        new_status = "approved"
                    else:
                        if check_for_unhandled_comments(pr_details_v2, my_user_id):
                            new_status = "attention_needed"

                    if new_status != old_pr_status:
                        subtask_details["pr_status"] = new_status

                        if new_status in ["merged", "approved"]:
                            notes = subtask_details.get("notes", [])
                            if new_status == "merged":
                                subtask_details["notes"] = [
                                    n for n in notes
                                    if not n.startswith("*PR* ") and not n.startswith(t("polling_note_approved"))
                                ]
                            elif new_status == "approved":
                                notes_to_keep = [n for n in notes if not n.startswith("*PR* ")]
                                if t("polling_note_approved") not in notes_to_keep:
                                    notes_to_keep.append(t("polling_note_approved"))
                                subtask_details["notes"] = notes_to_keep

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

                    data_manager.save_data(data_ref)
                    logging.debug(f"Updated Bitbucket PR data for {ticket_name}/{subtask_name}")

            except Exception as e:
                logging.error(f"Error processing PR {pr_url} in worker: {e}")

            pr_request_queue.task_done()

        except queue.Empty:
            continue
        except Exception as e:
            logging.error(f"Error in PR queue worker: {e}")
        finally:
            if pr_url and pr_url in pr_in_flight:
                pr_in_flight.remove(pr_url)


# Global state for review notifications
sent_review_notifications = set()


def _get_prs_reviewing(user_uuid: str, workspace: str) -> List[Dict[str, Any]]:
    """Find open PRs where the current user is a reviewer.

    Bitbucket Cloud has no workspace-wide reviewer endpoint, so this mirrors
    the approach from scripts/bb_prs.py: look at the most recently updated
    repos in the workspace and query each one concurrently with a reviewer
    filter.
    """
    auth = _get_auth()
    if not auth:
        return []

    # Find the recently-updated repos.
    try:
        resp = requests.get(
            f"{API_BASE}/repositories/{workspace}",
            params={"pagelen": 40, "sort": "-updated_on"},
            auth=auth,
            timeout=30,
        )
        resp.raise_for_status()
        repos = resp.json().get("values", [])
    except requests.exceptions.RequestException as e:
        logging.info(f"Failed listing Bitbucket repos for {workspace}: {e}")
        return []

    slugs = [r.get("slug", "") for r in repos if r.get("slug")]
    session = requests.Session()
    session.auth = auth

    def check_repo(repo_slug: str) -> List[Dict[str, Any]]:
        pr_url = f"{API_BASE}/repositories/{workspace}/{repo_slug}/pullrequests"
        pr_params = {
            "pagelen": 50,
            "q": f'state="OPEN" AND reviewers.uuid="{user_uuid}"',
        }
        try:
            r = session.get(pr_url, params=pr_params, timeout=15)
            if r.status_code == 200:
                return r.json().get("values", [])
        except requests.exceptions.RequestException:
            pass
        return []

    review_prs: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_repo, slug): slug for slug in slugs}
        for future in as_completed(futures):
            prs = future.result()
            if prs:
                review_prs.extend(prs)

    return review_prs


def poll_reviews_needed():
    """Poll for Bitbucket pull requests that need the current user's review."""
    global sent_review_notifications

    while True:
        try:
            workspace = _get_workspace()
            if not workspace:
                logging.info("PR review poller exiting - BB_WORKSPACE not configured.")
                return

            user_info = get_current_user()
            if not user_info or not user_info.get("uuid"):
                logging.info("Bitbucket review poller: no authenticated user yet, retrying later")
                time.sleep(300)
                continue

            review_prs = _get_prs_reviewing(user_info["uuid"], workspace)

            # Reformat for compatibility with the main view UI
            formatted_reviews = []
            for pr in review_prs:
                dest_repo = (pr.get("destination", {}) or {}).get("repository", {}) or {}
                full_name = dest_repo.get("full_name", "")
                if "/" in full_name:
                    proj_key, repo_name = full_name.split("/", 1)
                else:
                    proj_key, repo_name = workspace, dest_repo.get("name", "unknown-repo")
                html_url = (pr.get("links", {}) or {}).get("html", {}).get("href", "")
                formatted_reviews.append({
                    "id": pr.get("id"),
                    "title": pr.get("title", ""),
                    "links": {"self": [{"href": html_url}]},
                    "toRef": {"repository": {"project": {"key": proj_key}, "name": repo_name}},
                })

            # Desktop notifications for new review requests
            for pr in formatted_reviews:
                if pr["id"] not in sent_review_notifications:
                    repo_info = f"{pr['toRef']['repository']['project']['key']}/{pr['toRef']['repository']['name']}"
                    notif_title = t("notification_review_title")
                    notif_body = t("notification_review_body", repo=repo_info, title=pr["title"])
                    send_desktop_notification(notif_title, notif_body)
                    sent_review_notifications.add(pr["id"])

            # Update shared state so the main view can render the list.
            from jira_tracker import pull_requests_for_review, reviews_lock
            with reviews_lock:
                pull_requests_for_review.clear()
                pull_requests_for_review.extend(formatted_reviews)

                current_review_ids = {pr["id"] for pr in pull_requests_for_review}
                sent_review_notifications.intersection_update(current_review_ids)

        except Exception as e:
            logging.error(f"Unexpected error in poll_reviews_needed: {e}")

        time.sleep(300)  # Poll every 5 minutes
