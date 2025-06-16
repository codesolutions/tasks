import re
import time
import requests
import subprocess
import webbrowser
from datetime import datetime, timedelta
from config_manager import config, t

def send_desktop_notification(title, message):
    """Sends a desktop notification using notify-send."""
    try:
        subprocess.run(['/usr/bin/notify-send', title, message], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass # Silently fail if notify-send is not available

def convert_to_api_url(pr_url, details=False):
    """Converts a stash PR URL to its corresponding API URL."""
    base_url = config.get('STASH_URL')
    if not base_url: return None
    match = re.search(r'projects/(?P<proj>[^/]+)/repos/(?P<repo>[^/]+)/pull-requests/(?P<prid>\d+)', pr_url)
    if not match: return None
    gd = match.groupdict()
    endpoint = "" if details else "/activities"
    return f"{base_url}/rest/api/1.0/projects/{gd['proj']}/repos/{gd['repo']}/pull-requests/{gd['prid']}{endpoint}"

def check_for_unhandled_comments(activities, my_user_id):
    """Checks for comments on a PR that have not been replied to by the user."""
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

def poll_pull_requests(app_data, data_lock, save_data_func, permanent_notifications_ref):
    """Polls pull requests for status changes."""
    api_token = config.get('API_TOKEN')
    my_user_id = config.get('USER_ID')
    if not api_token or "PASTE" in api_token: return

    headers = {"Authorization": f"Bearer {api_token}"}

    while True:
        data_changed = False
        with data_lock:
            data_copy = {p: v for p, v in app_data.get("sub_tasks", {}).items()}

        for project, tasks in data_copy.items():
            if not isinstance(tasks, dict): continue
            for task_id, task_details in tasks.items():
                if not isinstance(task_details, dict) or not task_details.get("pr_url"): continue

                api_url_pr = convert_to_api_url(task_details["pr_url"], details=True)
                api_url_activities = convert_to_api_url(task_details["pr_url"], details=False)
                if not api_url_pr or not api_url_activities: continue

                try:
                    pr_resp = requests.get(api_url_pr, headers=headers, timeout=15)
                    activities_resp = requests.get(api_url_activities, headers=headers, timeout=15)
                    if not pr_resp.ok or not activities_resp.ok: continue

                    pr_data = pr_resp.json()
                    activities_data = activities_resp.json()

                    reviewers = pr_data.get('reviewers', [])
                    approver_count = sum(1 for r in reviewers if r.get('status') == 'APPROVED')

                    approvers_formatted = []
                    for r in reviewers:
                        emoji = {"APPROVED": "✅", "NEEDS_WORK": "❌"}.get(r.get('status'), "❓")
                        approvers_formatted.append(f"{emoji} {r.get('user', {}).get('displayName', 'Unknown')}")

                    state = pr_data.get('state')
                    unhandled = check_for_unhandled_comments(activities_data, my_user_id)

                    if state == 'MERGED': status_text = "merged ✅"
                    elif state == 'DECLINED': status_text = "declined ❌"
                    elif unhandled: status_text = f"attention needed ({len(unhandled)}) 💬"
                    elif approver_count > 0: status_text = f"approved ({approver_count}/{len(reviewers)})"
                    else: status_text = "waiting"

                    with data_lock:
                        if project in app_data["sub_tasks"] and task_id in app_data["sub_tasks"][project]:
                            current_details = app_data["sub_tasks"][project][task_id]
                            new_pr_details = {'status_text': status_text, 'approvers_formatted': approvers_formatted}
                            if current_details.get('pr_details') != new_pr_details:
                                current_details['pr_details'] = new_pr_details
                                data_changed = True
                except requests.RequestException:
                    if t('polling_err_stash') not in permanent_notifications_ref:
                        permanent_notifications_ref.append(t('polling_err_stash'))

        if data_changed:
            save_data_func()
        time.sleep(300)

def poll_reviews_needed(reviews_list_ref, reviews_lock_ref, sent_notifications_ref):
    """Polls for pull requests that need the user's review."""
    api_token = config.get("API_TOKEN")
    user_id = config.get("USER_ID")
    review_url = config.get("STASH_REVIEW_URL")

    if not all([api_token, user_id, review_url]) or "your-stash-instance.com" in review_url:
        return

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
                        if pr['id'] not in sent_notifications_ref:
                            repo = f"{pr['links']['self'][0]['href']}"
                            send_desktop_notification(t('notification_review_title'), f"{pr['title']}\n{repo}")
                            sent_notifications_ref.add(pr['id'])
                        break

            with reviews_lock_ref:
                reviews_list_ref.clear()
                reviews_list_ref.extend(pending_reviews)

        except requests.exceptions.RequestException:
            pass

        with reviews_lock_ref:
             current_review_ids = {pr['id'] for pr in reviews_list_ref}
             sent_notifications_ref.intersection_update(current_review_ids)

        time.sleep(300)

def event_notification_poller(app_data, data_lock, sent_notifications):
    """A thread that checks for upcoming events and sends notifications."""
    def focus_window(window_title):
        try:
            subprocess.run(['/usr/bin/xdotool', 'search', '--name', window_title, 'windowactivate'], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    def open_link_in_browser(url):
        browser_cmd = config.get("BROWSER_COMMAND")
        try:
            if browser_cmd and isinstance(browser_cmd, list):
                subprocess.Popen(browser_cmd + [url])
            else:
                webbrowser.open(url)
        except Exception:
            pass

    def get_next_occurrence(recurring_event, now):
        try:
            target_weekday = int(recurring_event['weekday'])
            event_time = datetime.strptime(recurring_event['time'], "%H:%M").time()
            days_ahead = (target_weekday - now.weekday() + 7) % 7
            if days_ahead == 0 and now.time() >= event_time: days_ahead = 7
            next_date = (now + timedelta(days=days_ahead)).date()
            return datetime.combine(next_date, event_time)
        except (ValueError, KeyError, TypeError):
            return None

    while True:
        now = datetime.now()
        if now.hour == 0 and now.minute == 0: sent_notifications.clear()

        all_upcoming_events = []
        with data_lock:
            meetings = app_data.get("meetings", [])
            interruptions = app_data.get("interruptions", [])
            recurring = app_data.get("recurring_events", [])

        for event in meetings + interruptions:
            try:
                dt = datetime.fromisoformat(event['datetime'])
                if dt > now:
                    evt_type = 'meeting' if 'link' in event else 'interruption'
                    details = event.get('link') or event.get('message', '')
                    all_upcoming_events.append({'dt': dt, 'details': details, 'type': evt_type, 'recurring': False})
            except (ValueError, TypeError): continue

        for event in recurring:
            next_occurrence = get_next_occurrence(event, now)
            if next_occurrence:
                all_upcoming_events.append({'dt': next_occurrence, 'details': event.get('details'), 'type': event.get('type'), 'recurring': True})

        for event in all_upcoming_events:
            time_diff = event['dt'] - now
            if timedelta(seconds=0) <= time_diff < timedelta(minutes=11):
                minutes_until = int(time_diff.total_seconds() / 60)
                event_id = f"{event['type']}_{event['details']}_{event['dt'].strftime('%Y%m%d%H%M')}"
                rec_str = f"({t('recurring')}) " if event['recurring'] else ""

                if minutes_until in [10, 5, 1] and (event_id, f'{minutes_until}min') not in sent_notifications:
                    if event['type'] == 'meeting':
                        title = t('notification_meeting_title', rec=rec_str, min=minutes_until, time=event['dt'].strftime('%H:%M'))
                        body = t('notification_meeting_body', link=event['details'])
                    else:
                        title = t('notification_event_title', rec=rec_str, min=minutes_until, time=event['dt'].strftime('%H:%M'))
                        body = event['details']

                    focus_window(config.get("NOTIFICATION_WINDOW_TITLE"))
                    send_desktop_notification(title, body)
                    sent_notifications.add((event_id, f'{minutes_until}min'))
                    if minutes_until <= 5 and event['type'] == 'meeting' and event.get('details', '').startswith('http'):
                        open_link_in_browser(event['details'])

        time.sleep(30)
