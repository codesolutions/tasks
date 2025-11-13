"""
External calendar polling service.

This module handles polling external calendar sources (CSV format)
and managing meeting data.
"""

import csv
import io
import time
import threading
from typing import List, Dict, Any, Optional

import requests

import inc.config_manager
from inc.helpers import t
from inc.utils.constants import CALENDAR_POLL_INTERVAL


class CalendarPoller:
    """
    Polls external calendar sources for meeting information.
    """
    
    def __init__(self):
        self._meetings: List[Dict[str, Any]] = []
        self._meetings_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._calendar_url: Optional[str] = None
    
    def start(self):
        """Start the calendar polling thread."""
        if self._thread and self._thread.is_alive():
            return  # Already running
        
        self._calendar_url = inc.config_manager.config.get('CALENDAR_CSV')
        if not self._calendar_url:
            return  # No calendar URL configured
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the calendar polling thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
    
    def get_meetings(self) -> List[Dict[str, Any]]:
        """
        Get current list of meetings.
        
        Returns:
            List of meeting dictionaries
        """
        with self._meetings_lock:
            return self._meetings.copy()
    
    def _poll_loop(self):
        """Main polling loop running in background thread."""
        # Do an immediate fetch on startup
        try:
            self._fetch_meetings()
        except Exception as e:
            logging.info(f"Calendar initial polling error: {e}")
        
        # Then continue with regular polling interval
        while not self._stop_event.wait(CALENDAR_POLL_INTERVAL):
            try:
                self._fetch_meetings()
            except Exception as e:
                logging.info(f"Calendar polling error: {e}")
    
    def _fetch_meetings(self):
        """Fetch meetings from the external calendar URL."""
        if not self._calendar_url:
            return
        
        try:
            response = requests.get(self._calendar_url, timeout=20, allow_redirects=True)
            response.raise_for_status()
            
            csv_data = response.text
            csv_file = io.StringIO(csv_data)
            reader = csv.reader(csv_file)
            
            # Skip header row
            try:
                next(reader)
            except StopIteration:
                return  # Empty CSV
            
            new_meetings = []
            for row in reader:
                if len(row) >= 6:
                    meeting = {
                        'start_time': row[1],
                        'end_time': row[2], 
                        'title': row[3],
                        'url': row[5]
                    }
                    new_meetings.append(meeting)
            
            # Update the meetings list atomically
            with self._meetings_lock:
                self._meetings.clear()
                self._meetings.extend(new_meetings)
                
        except requests.exceptions.RequestException as e:
            logging.info(t('polling_err', url=self._calendar_url, e=e))
            # Continue silently on network errors
        except Exception as e:
            logging.info(f"Calendar parsing error: {e}")


# Global instance
calendar_poller = CalendarPoller()
