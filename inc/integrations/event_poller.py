#!/usr/bin/env python3
"""
Event notification polling functionality.

This module handles:
- Polling for upcoming events
- Sending notifications for meetings and interruptions
- Opening browser links for meetings
- Managing recurring events
"""

import sys
import time
import copy
import subprocess
import webbrowser
from datetime import datetime, date, timedelta

import inc.config_manager
from inc.integrations.notification_service import send_desktop_notification
from inc.utils.formatters import focus_window
from inc.helpers import t

# Global tracking of sent notifications
sent_notifications = set()

def get_next_occurrence(recurring_event, now):
    """Calculates the next occurrence of a recurring event."""
    try:
        target_weekday = int(recurring_event['weekday']) # 0=Mon
        event_time_str = recurring_event['time'] # "HH:MM"
        event_time = datetime.strptime(event_time_str, "%H:%M").time()

        current_weekday = now.weekday() # 0=Mon
        days_ahead = target_weekday - current_weekday
        if days_ahead < 0: # Target day already passed this week
            days_ahead += 7
        elif days_ahead == 0 and now.time() >= event_time: # Target is today, but time has passed
            days_ahead += 7

        next_date = (now + timedelta(days=days_ahead)).date()
        return datetime.combine(next_date, event_time)
    except (ValueError, KeyError, TypeError):
        return None

def open_link_in_browser(url, browser_cmd):
    """Open a URL in the browser."""
    try:
        if browser_cmd and isinstance(browser_cmd, list):
            subprocess.Popen(browser_cmd + [url])
        else:
            webbrowser.open(url)
    except Exception as e:
        print(t('error_browser_open', e=e), file=sys.stderr)

def event_notification_poller(data_lock, data_ref):
    """A thread that checks for upcoming events and sends notifications."""
    global sent_notifications

    while True:
        now = datetime.now()

        if now.hour == 0 and now.minute == 0: # Daily reset
            sent_notifications.clear()

        all_upcoming_events = []
        with data_lock:
            # Make a deep copy to work with, to release the lock quickly
            meetings = copy.deepcopy(data_ref.get("meetings", []))
            interruptions = copy.deepcopy(data_ref.get("interruptions", []))
            recurring = copy.deepcopy(data_ref.get("recurring_events", []))

        # Process external calendar events
        from inc.integrations.calendar_poller import calendar_poller
        current_external_meetings = calendar_poller.get_meetings()

        for event in current_external_meetings:
            try:
                time_obj = datetime.strptime(event['start_time'], "%H:%M").time()
                dt = datetime.combine(date.today(), time_obj)
                if dt > now:
                    all_upcoming_events.append({
                        'datetime': dt,
                        'type': 'external_meeting',
                        'details': event,
                        'recurring': False
                    })
            except (ValueError, KeyError):
                continue

        # Process one-time events
        for event in meetings + interruptions:
            try:
                dt = datetime.fromisoformat(event['datetime'])
                if dt > now:
                    evt_type = 'meeting' if 'link' in event else 'interruption'
                    details = event.get('link') or event.get('message', '')
                    all_upcoming_events.append({'datetime': dt, 'type': evt_type, 'details': details, 'recurring': False})
            except (ValueError, TypeError):
                continue

        # Process recurring events
        for event in recurring:
            next_occurrence = get_next_occurrence(event, now)
            if next_occurrence:
                all_upcoming_events.append({
                    'datetime': next_occurrence,
                    'type': event.get('type'),
                    'details': event.get('details'),
                    'recurring': True
                })

        # Check for notifications
        for event in all_upcoming_events:
            time_diff = event['datetime'] - now
            if timedelta(seconds=0) <= time_diff < timedelta(minutes=11):
                minutes_until = int(time_diff.total_seconds() / 60)

                event_time_str = event['datetime'].strftime('%H:%M')
                
                # Create a proper event ID based on event type
                if event['type'] == 'external_meeting':
                    event_details = event.get('details', {})
                    event_id = f"{event['type']}_{event_details.get('title', 'unknown')}_{event['datetime'].strftime('%Y%m%d%H%M')}"
                else:
                    event_details_str = str(event['details']) if event['details'] else 'unknown'
                    event_id = f"{event['type']}_{event_details_str}_{event['datetime'].strftime('%Y%m%d%H%M')}"

                notification_title = ""
                notification_body = ""

                if event['type'] == 'meeting' or event['type'] == 'external_meeting':
                    rec_str = f"({t('recurring')}) " if event['recurring'] else ""
                    
                    if event['type'] == 'external_meeting':
                        details = event.get('details', {})
                        rec_str = f"({details.get('title', '')}) "
                        notification_body = t('notification_meeting_body', link=f"{details.get('url', '')}")
                    else:
                        notification_body = t('notification_meeting_body', link=event['details'])

                    notification_title = t('notification_meeting_title', rec=rec_str, min=minutes_until, time=event_time_str)
                    
                else: # interruption
                    rec_str = f"({t('recurring')}) " if event['recurring'] else ""
                    notification_title = t('notification_event_title', rec=rec_str, min=minutes_until, time=event_time_str)
                    notification_body = event['details']

                # 10-minute warning
                if minutes_until == 10 and (event_id, '10min') not in sent_notifications:
                    focus_window(inc.config_manager.config.get("NOTIFICATION_WINDOW_TITLE"))
                    send_desktop_notification(notification_title, notification_body)
                    sent_notifications.add((event_id, '10min'))

                # 5-minute warning
                elif minutes_until == 5 and (event_id, '5min') not in sent_notifications:
                    focus_window(inc.config_manager.config.get("NOTIFICATION_WINDOW_TITLE"))
                    send_desktop_notification(notification_title, notification_body)
                    sent_notifications.add((event_id, '5min'))
                    if event['type'] == 'meeting' and event.get('details', '').startswith('http'):
                        open_link_in_browser(event['details'], inc.config_manager.config.get("BROWSER_COMMAND"))
                    elif event['type'] == 'external_meeting':
                        url_to_open = event.get('details', {}).get('url', '')
                        if url_to_open.startswith('http'):
                            open_link_in_browser(url_to_open, inc.config_manager.config.get("BROWSER_COMMAND"))

        time.sleep(60)
