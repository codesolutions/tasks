"""
External integrations package.

This package contains modules for integrating with external services
such as calendar providers, web monitoring, and desktop notifications.
"""

from .calendar_poller import calendar_poller
from .web_monitor import web_monitor
from .notification_service import notification_service, send_desktop_notification

__all__ = [
    'calendar_poller',
    'web_monitor', 
    'notification_service',
    'send_desktop_notification'
]
