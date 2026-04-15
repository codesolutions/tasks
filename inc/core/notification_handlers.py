#!/usr/bin/env python3
"""
Notification Handlers for Event System

This module contains event handlers that manage permanent notifications
based on user actions like selecting tickets, switching projects, etc.

The handlers automatically mark Jira/PR comments as read and
remove notifications when the user views the relevant content.
"""

import logging
from typing import Dict, Any, List
from inc.core.event_system import event_system, EventNames
from inc.helpers import get_jira_ticket_from_url

logger = logging.getLogger(__name__)

class NotificationManager:
    """
    Manages permanent notifications based on user actions.
    
    Listens to events and automatically marks comments as read,
    removes notifications, and updates caches accordingly.
    """
    
    def __init__(self):
        self.permanent_notifications = None
        self.jira_cache = None
        self.jira_cache_lock = None
        self.app_data = None
        self.data_lock = None
        self._initialized = False
        
    def initialize(self, permanent_notifications: List[str], jira_cache: Dict, jira_cache_lock, app_data: Dict, data_lock):
        """
        Initialize the notification manager with references to shared data.
        
        Args:
            permanent_notifications: Reference to global permanent_notifications list
            jira_cache: Reference to global jira_cache dict
            jira_cache_lock: Threading lock for jira_cache
            app_data: Reference to global app_data dict
            data_lock: Threading lock for app_data
        """
        self.permanent_notifications = permanent_notifications
        self.jira_cache = jira_cache
        self.jira_cache_lock = jira_cache_lock
        self.app_data = app_data
        self.data_lock = data_lock
        self._initialized = True
        
        # Register event handlers
        self._register_handlers()
        
        logger.info("NotificationManager initialized and handlers registered")
    
    def _register_handlers(self):
        """Register all event handlers."""
        event_system.register(EventNames.TICKET_SELECTED, self._handle_ticket_selected)
        event_system.register(EventNames.PROJECT_SWITCHED, self._handle_project_switched)
        event_system.register(EventNames.NOTES_VIEW_ENTERED, self._handle_notes_view_entered)
        event_system.register(EventNames.VIEW_CHANGED, self._handle_view_changed)
    
    def _handle_ticket_selected(self, event_data: Dict[str, Any]):
        """
        Handle when a ticket/subtask is selected with arrow keys.

        This should mark Jira/PR comments as read for the selected ticket.
        """
        if not self._initialized:
            return

        ticket_name = event_data.get('ticket_name')
        project_name = event_data.get('project_name')

        if not ticket_name or not project_name:
            return

        logger.debug(f"Handling ticket selection: {project_name}/{ticket_name}")

        # Extract Jira ticket ID
        jira_ticket_id = get_jira_ticket_from_url(ticket_name)
        if not jira_ticket_id:
            return

        # Mark Jira comments as read
        self._mark_comments_as_read(jira_ticket_id)

        # Mark PR comments as read
        self._mark_pr_comments_as_read(project_name, ticket_name)
    
    def _handle_project_switched(self, event_data: Dict[str, Any]):
        """
        Handle when switching between projects.
        
        This could be used for project-level notification management.
        """
        old_project = event_data.get('old_project')
        new_project = event_data.get('new_project')
        
        logger.debug(f"Project switched from {old_project} to {new_project}")
        # Could add project-level notification logic here if needed
    
    def _handle_notes_view_entered(self, event_data: Dict[str, Any]):
        """
        Handle when entering notes view for a ticket.
        
        This definitely means the user is viewing the ticket content,
        so we should mark all comments as read.
        """
        if not self._initialized:
            return
            
        context = event_data.get('context', {})
        if context.get('type') == 'subtask':
            ticket_name = context.get('name')
            project_name = context.get('main_task_name')
            
            if ticket_name and project_name:
                logger.debug(f"Entered notes view for: {project_name}/{ticket_name}")
                
                # Extract Jira ticket ID and mark comments as read
                jira_ticket_id = get_jira_ticket_from_url(ticket_name)
                if jira_ticket_id:
                    self._mark_comments_as_read(jira_ticket_id)
                    self._mark_pr_comments_as_read(project_name, ticket_name)
    
    def _handle_view_changed(self, event_data: Dict[str, Any]):
        """Handle general view changes."""
        old_view = event_data.get('old_view')
        new_view = event_data.get('new_view')
        context = event_data.get('context')
        
        # If entering notes view, trigger the notes view handler
        if new_view == 'VIEW_DEDICATED_NOTES' and context:
            self._handle_notes_view_entered({'context': context})
    
    def _mark_comments_as_read(self, jira_ticket_id: str):
        """
        Mark Jira comments as read for a specific ticket.

        Args:
            jira_ticket_id: The Jira ticket ID (e.g., 'DCOLO-376')
        """
        if not self._initialized or not jira_ticket_id:
            return

        try:
            needs_save = False

            with self.jira_cache_lock:
                if jira_ticket_id in self.jira_cache:
                    cache_entry = self.jira_cache[jira_ticket_id]

                    # Mark as read if there were new comments
                    if cache_entry.get('new_jira_comment'):
                        cache_entry['new_jira_comment'] = False
                        needs_save = True

                        logger.debug(f"Marked Jira comments as read for {jira_ticket_id}")

                # Remove related notifications from permanent notifications
                jira_notif = f"New Jira comment in {jira_ticket_id}"

                notifications_removed = []
                if jira_notif in self.permanent_notifications:
                    self.permanent_notifications.remove(jira_notif)
                    notifications_removed.append("Jira")

                if notifications_removed:
                    logger.debug(f"Removed {', '.join(notifications_removed)} notifications for {jira_ticket_id}")
            
            # Save cache if changes were made
            if needs_save:
                from inc.jira import save_jira_cache
                save_jira_cache(self.jira_cache, self.jira_cache_lock)
                
        except Exception as e:
            logger.error(f"Error marking comments as read for {jira_ticket_id}: {e}")
    
    def _mark_pr_comments_as_read(self, project_name: str, ticket_name: str):
        """
        Mark PR comments as read for a specific ticket.
        
        Args:
            project_name: The project name (e.g., 'Veikonkone')
            ticket_name: The ticket name/URL
        """
        if not self._initialized or not project_name or not ticket_name:
            return
            
        try:
            with self.data_lock:
                # Check if ticket has PR details with attention_needed status
                subtasks = self.app_data.get("sub_tasks", {}).get(project_name, {})
                if ticket_name not in subtasks:
                    return
                    
                subtask_details = subtasks[ticket_name]
                pr_url = subtask_details.get("pr_url")
                pr_status = subtask_details.get("pr_status")
                
                if pr_url and pr_status == "attention_needed":
                    # Mark PR comments as viewed by updating notification state
                    pr_details = subtask_details.get("pr_details", {})
                    if pr_details.get('version') == 2:
                        # Update notification state to indicate comments were viewed
                        if 'meta' not in pr_details:
                            pr_details['meta'] = {}
                        if 'notifications' not in pr_details['meta']:
                            pr_details['meta']['notifications'] = {}
                        
                        # Mark attention as handled by viewing
                        from datetime import datetime
                        pr_details['meta']['notifications']['ATTENTION_VIEWED'] = {
                            'viewed': datetime.now().isoformat() + 'Z'
                        }
                        
                        logger.debug(f"Marked PR comments as viewed for {project_name}/{ticket_name}")
                        
                        # Save data
                        from inc.core.data_manager import data_manager
                        data_manager.save_data(self.app_data)
                        
        except Exception as e:
            logger.error(f"Error marking PR comments as read for {project_name}/{ticket_name}: {e}")

# Global instance
notification_manager = NotificationManager()

def initialize_notification_handlers(permanent_notifications: List[str], jira_cache: Dict, jira_cache_lock, app_data: Dict, data_lock):
    """
    Initialize the notification handlers with shared data references.
    
    This should be called from the main application after all shared data is set up.
    """
    notification_manager.initialize(permanent_notifications, jira_cache, jira_cache_lock, app_data, data_lock)