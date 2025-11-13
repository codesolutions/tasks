"""
Desktop notification service.

This module handles sending desktop notifications using various
system notification mechanisms.
"""

import subprocess
import sys
from typing import Set, Optional


class NotificationService:
    """
    Handles desktop notifications using system-specific mechanisms.
    """
    
    def __init__(self):
        self._sent_notifications: Set[str] = set()
    
    def send_notification(self, title: str, message: str, 
                         prevent_duplicates: bool = False) -> bool:
        """
        Send a desktop notification.
        
        Args:
            title: Notification title
            message: Notification message
            prevent_duplicates: Whether to prevent duplicate notifications
            
        Returns:
            True if notification was sent successfully
        """
        if prevent_duplicates:
            notification_key = f"{title}:{message}"
            if notification_key in self._sent_notifications:
                return False  # Already sent
            self._sent_notifications.add(notification_key)
        
        try:
            if sys.platform.startswith('linux'):
                return self._send_linux_notification(title, message)
            elif sys.platform == 'darwin':
                return self._send_macos_notification(title, message)
            elif sys.platform.startswith('win'):
                return self._send_windows_notification(title, message)
            else:
                print(f"[{title}] {message}")  # Fallback to console
                return True
        except Exception as e:
            logging.error(f"Notification error: {e}")
            return False
    
    def _send_linux_notification(self, title: str, message: str) -> bool:
        """Send notification using notify-send on Linux."""
        try:
            subprocess.run([
                'notify-send',
                title,
                message,
                '--urgency=normal',
                '--expire-time=5000'
            ], check=True, capture_output=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback: try using zenity
            try:
                subprocess.run([
                    'zenity', '--info',
                    f'--title={title}',
                    f'--text={message}',
                    '--timeout=5'
                ], check=True, capture_output=True)
                return True
            except (subprocess.CalledProcessError, FileNotFoundError):
                return False
    
    def _send_macos_notification(self, title: str, message: str) -> bool:
        """Send notification using osascript on macOS."""
        try:
            script = f'''
            display notification "{message}" with title "{title}"
            '''
            subprocess.run([
                'osascript', '-e', script
            ], check=True, capture_output=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    def _send_windows_notification(self, title: str, message: str) -> bool:
        """Send notification using PowerShell on Windows."""
        try:
            # Use PowerShell to show a balloon notification
            script = f'''
            Add-Type -AssemblyName System.Windows.Forms
            $notify = New-Object System.Windows.Forms.NotifyIcon
            $notify.Icon = [System.Drawing.SystemIcons]::Information
            $notify.BalloonTipTitle = "{title}"
            $notify.BalloonTipText = "{message}"
            $notify.BalloonTipIcon = "Info"
            $notify.Visible = $true
            $notify.ShowBalloonTip(5000)
            '''
            subprocess.run([
                'powershell', '-Command', script
            ], check=True, capture_output=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    def clear_sent_notifications(self):
        """Clear the cache of sent notifications."""
        self._sent_notifications.clear()
    
    def send_urgent_notification(self, title: str, message: str) -> bool:
        """
        Send an urgent notification that bypasses duplicate checking.
        
        Args:
            title: Notification title
            message: Notification message
            
        Returns:
            True if notification was sent successfully
        """
        return self.send_notification(title, message, prevent_duplicates=False)
    
    def get_notification_count(self) -> int:
        """
        Get the number of unique notifications sent.
        
        Returns:
            Count of sent notifications
        """
        return len(self._sent_notifications)


# Global instance
notification_service = NotificationService()


# Convenience function for backward compatibility
def send_desktop_notification(title: str, message: str) -> bool:
    """
    Send a desktop notification (convenience function).
    
    Args:
        title: Notification title
        message: Notification message
        
    Returns:
        True if notification was sent successfully
    """
    return notification_service.send_notification(title, message)
