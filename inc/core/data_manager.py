"""
Data management operations for the task tracker application.

This module handles loading, saving, and initializing application data,
including data migration and validation.
"""

import json
import os
from typing import Dict, Any, List

from inc.utils.constants import DATA_FILE
from inc.helpers import t


class DataManager:
    """
    Manages loading and saving of application data.
    """
    
    @staticmethod
    def load_data() -> Dict[str, Any]:
        """
        Load application data from the data file.
        
        Returns:
            Dictionary containing application data with defaults
        """
        data = {}
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            pass  # Use empty data with defaults
        except json.JSONDecodeError:
            print(t('error_json_read', file=DATA_FILE))
            pass  # Use empty data with defaults
        
        # Set defaults
        data.setdefault("current_ticket", None)
        data.setdefault("focused_ticket", None)
        data.setdefault("focused_subtask", None)
        data.setdefault("completed_tickets", [])
        data.setdefault("task_start_time", None)
        data.setdefault("sub_tasks", {})
        data.setdefault("tasks_done", {})
        data.setdefault("meetings", [])
        data.setdefault("interruptions", [])
        data.setdefault("notes", {})
        data.setdefault("paused_tasks", [])
        data.setdefault("recurring_events", [])
        data.setdefault("daily_notes", {})
        data.setdefault("show_hidden_tasks", False)
        data.setdefault("web_change_notifications", [])
        
        # Initialize time tracking data
        from inc.time_tracker import ensure_time_tracking_defaults
        ensure_time_tracking_defaults(data)
        
        # Perform data migration
        DataManager._migrate_data(data)
        
        return data
    
    @staticmethod
    def save_data(data: Dict[str, Any], web_change_notifications: List[str] = None) -> bool:
        """
        Save application data to the data file.
        
        Args:
            data: Application data dictionary
            web_change_notifications: List of web change notifications to include
            
        Returns:
            True if successful, False otherwise
        """
        # Include web change notifications if provided
        if web_change_notifications is not None:
            data["web_change_notifications"] = web_change_notifications
        
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, default=str, ensure_ascii=False)
            return True
        except IOError as e:
            print(t('error_json_save', file=DATA_FILE, e=e))
            return False
        except TypeError as e:
            print(t('error_json_convert', e=e))
            return False
    
    @staticmethod
    def _migrate_data(data: Dict[str, Any]) -> None:
        """
        Migrate data from older formats to current format.
        
        Args:
            data: Data dictionary to migrate in-place
        """
        # Migrate subtask structure
        for ticket_name, sub_tasks_for_ticket in data.get("sub_tasks", {}).items():
            if isinstance(sub_tasks_for_ticket, dict):
                DataManager._migrate_subtasks(sub_tasks_for_ticket)
            elif sub_tasks_for_ticket is not None:
                # Invalid structure, reset to empty dict
                data["sub_tasks"][ticket_name] = {}
    
    @staticmethod
    def _migrate_subtasks(sub_tasks_for_ticket: Dict[str, Any]) -> None:
        """
        Migrate subtask data structure.
        
        Args:
            sub_tasks_for_ticket: Subtasks dictionary to migrate
        """
        for sub_task_name, sub_task_details in list(sub_tasks_for_ticket.items()):
            if not isinstance(sub_task_details, dict):
                # Convert old boolean format to new dict format
                current_status = "done" if sub_task_details else "todo"
                sub_tasks_for_ticket[sub_task_name] = {
                    "status": current_status,
                    "notes": [],
                    "pr_url": None,
                    "pr_status": None,
                    "jira_refreshed": None
                }
            else:
                # Migrate existing dict format
                DataManager._migrate_subtask_status(sub_task_details)
                DataManager._ensure_subtask_fields(sub_task_details)
                DataManager._cleanup_old_subtask_fields(sub_task_details)
    
    @staticmethod
    def _migrate_subtask_status(sub_task_details: Dict[str, Any]) -> None:
        """
        Migrate subtask status from old boolean fields to new status field.
        
        Args:
            sub_task_details: Subtask details dictionary
        """
        current_status = sub_task_details.get("status")
        if not current_status or current_status not in ["todo", "in_progress", "done", "hidden", "focused"]:
            if sub_task_details.get("hidden", False):
                current_status = "hidden"
            elif sub_task_details.get("done", False):
                current_status = "done"
            elif sub_task_details.get("focused", False):
                current_status = "focused"
            else:
                current_status = "todo"
            
            sub_task_details["status"] = current_status
    
    @staticmethod
    def _ensure_subtask_fields(sub_task_details: Dict[str, Any]) -> None:
        """
        Ensure all required fields exist on subtask details.
        
        Args:
            sub_task_details: Subtask details dictionary
        """
        sub_task_details.setdefault("notes", [])
        sub_task_details.setdefault("pr_url", None)
        sub_task_details.setdefault("pr_status", None)
        sub_task_details.setdefault("jira_refreshed", None)
    
    @staticmethod
    def _cleanup_old_subtask_fields(sub_task_details: Dict[str, Any]) -> None:
        """
        Clean up old/deprecated fields from subtask details.
        
        Args:
            sub_task_details: Subtask details dictionary
        """
        # Remove old boolean status fields
        sub_task_details.pop("done", None)
        sub_task_details.pop("hidden", None)
        sub_task_details.pop("focused", None)
        
        # Migrate old PR handling fields
        if "pr_unhandled_comments" in sub_task_details:
            if sub_task_details["pr_unhandled_comments"] and sub_task_details.get("pr_status") is None:
                sub_task_details["pr_status"] = "attention_needed"
            del sub_task_details["pr_unhandled_comments"]
        
        # Clean up old PR notes
        if sub_task_details.get("pr_url") and "notes" in sub_task_details:
            cleaned_notes = [
                note for note in sub_task_details["notes"] 
                if not note.strip().startswith("PR:")
            ]
            sub_task_details["notes"] = cleaned_notes
    
    @staticmethod
    def backup_data(backup_suffix: str = None) -> str:
        """
        Create a backup of the current data file.
        
        Args:
            backup_suffix: Optional suffix for backup filename
            
        Returns:
            Path to the backup file created
        """
        if backup_suffix is None:
            from datetime import datetime
            backup_suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        backup_file = f"{DATA_FILE}.backup_{backup_suffix}"
        
        try:
            import shutil
            shutil.copy2(DATA_FILE, backup_file)
            return backup_file
        except Exception as e:
            print(f"Failed to create backup: {e}")
            raise
    
    @staticmethod
    def validate_data_structure(data: Dict[str, Any]) -> List[str]:
        """
        Validate the data structure and return any issues found.
        
        Args:
            data: Data dictionary to validate
            
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Check required top-level keys exist
        required_keys = ["sub_tasks", "current_ticket", "notes"]
        for key in required_keys:
            if key not in data:
                errors.append(f"Missing required key: {key}")
        
        # Validate sub_tasks structure
        if "sub_tasks" in data and not isinstance(data["sub_tasks"], dict):
            errors.append("sub_tasks must be a dictionary")
        
        # Validate subtask details
        for ticket_name, subtasks in data.get("sub_tasks", {}).items():
            if not isinstance(subtasks, dict):
                errors.append(f"Subtasks for '{ticket_name}' must be a dictionary")
                continue
            
            for subtask_name, details in subtasks.items():
                if not isinstance(details, dict):
                    errors.append(f"Subtask details for '{ticket_name}.{subtask_name}' must be a dictionary")
                    continue
                
                if "status" not in details:
                    errors.append(f"Missing status for subtask '{ticket_name}.{subtask_name}'")
                elif details["status"] not in ["todo", "in_progress", "done", "hidden", "focused"]:
                    errors.append(f"Invalid status '{details['status']}' for subtask '{ticket_name}.{subtask_name}'")
        
        return errors


# Global instance for convenient access
data_manager = DataManager()
