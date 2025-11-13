#!/usr/bin/env python3
"""
Global Event System for Jira Tracker

This module provides a centralized hook/event system that allows different parts
of the application to register listeners and trigger events based on user actions.

Usage:
    from inc.core.event_system import event_system
    
    # Register a handler
    @event_system.on('ticket_changed')
    def handle_ticket_change(event_data):
        print(f"Ticket changed to: {event_data['ticket_name']}")
    
    # Or register manually
    event_system.register('ticket_changed', my_handler)
    
    # Trigger an event
    event_system.trigger('ticket_changed', {
        'ticket_name': 'DCOLO-376',
        'project_name': 'Veikonkone',
        'old_ticket': 'DCOLO-123'
    })
"""

import threading
import logging
from typing import Dict, List, Callable, Any, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class Event:
    """Represents an event that occurred in the system."""
    name: str
    data: Dict[str, Any]
    timestamp: datetime
    source: Optional[str] = None

class EventSystem:
    """
    Centralized event system for handling user actions and system events.
    
    Thread-safe event dispatcher that allows components to register handlers
    for specific events and trigger those events with associated data.
    """
    
    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()
        self._event_history: List[Event] = []
        self._max_history = 100  # Keep last 100 events for debugging
        
    def register(self, event_name: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        """
        Register a handler for a specific event.
        
        Args:
            event_name: Name of the event to listen for
            handler: Function to call when event is triggered
        """
        with self._lock:
            if event_name not in self._handlers:
                self._handlers[event_name] = []
            
            if handler not in self._handlers[event_name]:
                self._handlers[event_name].append(handler)
                logger.debug(f"Registered handler for event '{event_name}'")
    
    def unregister(self, event_name: str, handler: Callable[[Dict[str, Any]], None]) -> bool:
        """
        Unregister a handler for a specific event.
        
        Args:
            event_name: Name of the event
            handler: Handler function to remove
            
        Returns:
            True if handler was found and removed, False otherwise
        """
        with self._lock:
            if event_name in self._handlers:
                try:
                    self._handlers[event_name].remove(handler)
                    logger.debug(f"Unregistered handler for event '{event_name}'")
                    return True
                except ValueError:
                    pass
            return False
    
    def trigger(self, event_name: str, event_data: Dict[str, Any], source: Optional[str] = None) -> None:
        """
        Trigger an event, calling all registered handlers.
        
        Args:
            event_name: Name of the event to trigger
            event_data: Data to pass to handlers
            source: Optional source identifier (e.g., 'main_view', 'command_handler')
        """
        event = Event(
            name=event_name,
            data=event_data.copy(),  # Copy to prevent modifications
            timestamp=datetime.now(),
            source=source
        )
        
        with self._lock:
            # Store event in history
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history.pop(0)
            
            # Get handlers for this event
            handlers = self._handlers.get(event_name, []).copy()
        
        # Call handlers outside the lock to prevent deadlocks
        logger.debug(f"Triggering event '{event_name}' with {len(handlers)} handlers")
        
        for handler in handlers:
            try:
                handler(event.data)
            except Exception as e:
                logger.error(f"Error in event handler for '{event_name}': {e}")
    
    def on(self, event_name: str) -> Callable:
        """
        Decorator for registering event handlers.
        
        Usage:
            @event_system.on('ticket_changed')
            def handle_ticket_change(event_data):
                print(f"Ticket: {event_data['ticket_name']}")
        """
        def decorator(handler: Callable[[Dict[str, Any]], None]) -> Callable:
            self.register(event_name, handler)
            return handler
        return decorator
    
    def get_handlers(self, event_name: str) -> List[Callable]:
        """Get list of handlers for an event (for debugging)."""
        with self._lock:
            return self._handlers.get(event_name, []).copy()
    
    def get_event_history(self, limit: Optional[int] = None) -> List[Event]:
        """Get recent event history (for debugging)."""
        with self._lock:
            history = self._event_history.copy()
            if limit:
                history = history[-limit:]
            return history
    
    def clear_handlers(self, event_name: Optional[str] = None) -> None:
        """Clear handlers for specific event or all events."""
        with self._lock:
            if event_name:
                self._handlers.pop(event_name, None)
                logger.debug(f"Cleared all handlers for event '{event_name}'")
            else:
                self._handlers.clear()
                logger.debug("Cleared all event handlers")

# Global event system instance
event_system = EventSystem()

# Standard event names (constants to prevent typos)
class EventNames:
    # Navigation events
    TICKET_SELECTED = 'ticket_selected'
    TICKET_CHANGED = 'ticket_changed'  
    PROJECT_SWITCHED = 'project_switched'
    SUBTASK_SELECTED = 'subtask_selected'
    
    # Data events
    SUBTASK_ADDED = 'subtask_added'
    SUBTASK_COMPLETED = 'subtask_completed'
    SUBTASK_HIDDEN = 'subtask_hidden'
    PROJECT_CREATED = 'project_created'
    PROJECT_COMPLETED = 'project_completed'
    
    # View events
    VIEW_CHANGED = 'view_changed'
    NOTES_VIEW_ENTERED = 'notes_view_entered'
    NOTES_VIEW_EXITED = 'notes_view_exited'
    
    # Comment/notification events
    JIRA_COMMENT_VIEWED = 'jira_comment_viewed'
    TRELLO_COMMENT_VIEWED = 'trello_comment_viewed'
    PR_COMMENT_VIEWED = 'pr_comment_viewed'
    NOTIFICATION_DISMISSED = 'notification_dismissed'

# Convenience functions for common events
def trigger_ticket_selected(ticket_name: str, project_name: str, source: str = None):
    """Trigger when a ticket/subtask is selected with arrow keys."""
    event_system.trigger(EventNames.TICKET_SELECTED, {
        'ticket_name': ticket_name,
        'project_name': project_name
    }, source)

def trigger_project_switched(old_project: str, new_project: str, source: str = None):
    """Trigger when switching between projects."""
    event_system.trigger(EventNames.PROJECT_SWITCHED, {
        'old_project': old_project,
        'new_project': new_project
    }, source)

def trigger_view_changed(old_view: str, new_view: str, context: Dict[str, Any] = None, source: str = None):
    """Trigger when view changes (main -> notes, etc.)."""
    event_data = {
        'old_view': old_view,
        'new_view': new_view
    }
    if context:
        event_data['context'] = context
    event_system.trigger(EventNames.VIEW_CHANGED, event_data, source)