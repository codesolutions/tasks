"""
Web page monitoring service for change detection.

This module handles monitoring web pages for changes and sending
notifications when changes are detected.
"""

import hashlib
import os
import time
import threading
from typing import List, Dict, Any, Optional, Callable

import requests
from bs4 import BeautifulSoup

import inc.config_manager
from inc.helpers import t
from inc.utils.constants import CACHE_DIR, DEFAULT_WEB_MONITORING_INTERVAL


class WebMonitor:
    """
    Monitors web pages for changes and sends notifications.
    """
    
    def __init__(self):
        self._notifications: List[str] = []
        self._notifications_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._notification_callback: Optional[Callable[[str, str], None]] = None
        self._data_save_callback: Optional[Callable[[], None]] = None
        
        # Ensure cache directory exists
        os.makedirs(CACHE_DIR, exist_ok=True)
    
    def start(self, notification_callback: Callable[[str, str], None] = None,
              data_save_callback: Callable[[], None] = None):
        """
        Start the web monitoring thread.
        
        Args:
            notification_callback: Function to call for desktop notifications
            data_save_callback: Function to call to save data after changes
        """
        if self._thread and self._thread.is_alive():
            return  # Already running
        
        config = inc.config_manager.config.get("WEB_MONITORING", {})
        if not config.get("ENABLED", False):
            # Clear any existing notifications if monitoring is disabled
            with self._notifications_lock:
                if self._notifications:
                    self._notifications.clear()
                    if data_save_callback:
                        data_save_callback()
            return
        
        self._notification_callback = notification_callback
        self._data_save_callback = data_save_callback
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the web monitoring thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
    
    def get_notifications(self) -> List[str]:
        """
        Get current list of web change notifications.
        
        Returns:
            List of notification messages
        """
        with self._notifications_lock:
            return self._notifications.copy()
    
    def clear_notifications(self):
        """Clear all web change notifications."""
        with self._notifications_lock:
            self._notifications.clear()
    
    def _monitor_loop(self):
        """Main monitoring loop running in background thread."""
        config = inc.config_manager.config.get("WEB_MONITORING", {})
        check_interval = config.get("CHECK_INTERVAL_MINUTES", DEFAULT_WEB_MONITORING_INTERVAL) * 60
        pages = config.get("PAGES", [])
        
        while not self._stop_event.wait(check_interval):
            try:
                for page in pages:
                    self._check_page(page)
            except Exception as e:
                logging.error(f"Web monitoring error: {e}")
    
    def _check_page(self, page_config: Dict[str, Any]):
        """
        Check a single page for changes.
        
        Args:
            page_config: Page configuration dictionary
        """
        url = page_config.get("url")
        if not url:
            return
        
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            content = response.text
            
            # Apply CSS selector if specified
            selector = page_config.get("selector")
            if selector:
                soup = BeautifulSoup(content, 'lxml')
                element = soup.select_one(selector)
                content = str(element) if element else ""
            
            # Generate cache filename based on URL hash
            url_hash = hashlib.md5(url.encode()).hexdigest()
            cache_file = os.path.join(CACHE_DIR, f"{url_hash}.html")
            
            # Read previous content
            last_content = ""
            if os.path.exists(cache_file):
                with open(cache_file, 'r', encoding='utf-8') as f:
                    last_content = f.read()
            
            # Check for changes
            if content != last_content:
                # Save new content
                with open(cache_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                # Create notification
                page_name = page_config.get('name', url)
                notification_message = t('web_change_notification', name=page_name, url=url)
                
                # Add notification if not already present
                with self._notifications_lock:
                    if notification_message not in self._notifications:
                        self._notifications.append(notification_message)
                        
                        # Send desktop notification
                        if self._notification_callback:
                            title = t('web_change_notification_title')
                            self._notification_callback(title, notification_message)
                        
                        # Save data
                        if self._data_save_callback:
                            self._data_save_callback()
                            
        except requests.exceptions.RequestException as e:
            logging.error(t('polling_err', url=url, e=e))
            # Continue silently on network errors
        except Exception as e:
            logging.error(f"Web monitoring error for {url}: {e}")
    
    def update_data_notifications(self, data: Dict[str, Any]):
        """
        Update the data dictionary with current notifications.
        
        Args:
            data: Application data dictionary to update
        """
        with self._notifications_lock:
            data["web_change_notifications"] = self._notifications.copy()
    
    def load_notifications_from_data(self, data: Dict[str, Any]):
        """
        Load notifications from the data dictionary.
        
        Args:
            data: Application data dictionary
        """
        with self._notifications_lock:
            self._notifications.clear()
            self._notifications.extend(data.get("web_change_notifications", []))


# Global instance
web_monitor = WebMonitor()
