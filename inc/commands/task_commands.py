"""
Task management commands.

This module contains commands for managing main tasks/projects:
creating new tasks, switching between tasks, completing tasks, etc.
"""

import copy
import time
from typing import List

from inc.commands.base_command import BaseCommand, CommandContext, CommandResult
from inc.helpers import t
from inc.utils.constants import VIEW_MAIN


class NewTaskCommand(BaseCommand):
    """Command to create a new task/project."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        if len(args) < 2:
            return CommandResult(
                success=False,
                message=t('cmd_usage_new_task')
            )
        
        new_task_name = " ".join(args[1:])
        
        # Validate task name
        if new_task_name.startswith(("http:", "https:")):
            return CommandResult(
                success=False,
                message=t('cmd_err_project_is_url')
            )
        
        # Check if already current task
        current_ticket = data.get("current_ticket")
        if current_ticket and current_ticket.lower() == new_task_name.lower():
            return CommandResult(
                success=False,
                message=t('cmd_err_task_already_active', name=new_task_name)
            )
        
        # Check if it's a completed task (restore it)
        completed_tickets = data.get("completed_tickets", [])
        if new_task_name in completed_tickets:
            data["completed_tickets"].remove(new_task_name)
            self._pause_current_task(data)
            data["current_ticket"] = new_task_name
            data["task_start_time"] = time.time()
            return CommandResult(
                success=True,
                data_modified=True,
                message=t('cmd_info_task_restored', name=new_task_name),
                request_redraw=True
            )
        
        # Check if task already exists
        all_known_tickets = self._get_all_known_tickets(data)
        for ticket_name in all_known_tickets:
            if ticket_name.lower() == new_task_name.lower():
                is_paused = any(
                    pt.get('ticket', '').lower() == new_task_name.lower() 
                    for pt in data.get('paused_tasks', [])
                )
                if is_paused:
                    message = t('cmd_err_task_exists_paused', name=new_task_name)
                else:
                    message = t('cmd_err_task_exists', name=new_task_name)
                return CommandResult(success=False, message=message)
        
        # Create new task
        self._pause_current_task(data)
        data["current_ticket"] = new_task_name
        data["task_start_time"] = time.time()
        data.setdefault("sub_tasks", {}).setdefault(new_task_name, {})
        data.setdefault("notes", {}).setdefault(new_task_name, [])
        
        return CommandResult(
            success=True,
            data_modified=True,
            message=t('cmd_info_task_started', name=new_task_name),
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "n <task_name> - Create a new task or project"
    
    def _pause_current_task(self, data):
        """Helper to pause the current task."""
        current_to_pause = data.get("current_ticket")
        if current_to_pause:
            sub_tasks_for_pause = data.get("sub_tasks", {}).get(current_to_pause, {})
            notes_for_pause = data.get("notes", {}).get(current_to_pause, [])
            start_time_for_pause = data.get("task_start_time")
            paused_item = {
                'ticket': current_to_pause,
                'sub_tasks': copy.deepcopy(sub_tasks_for_pause),
                'notes': copy.deepcopy(notes_for_pause),
                'task_start_time': start_time_for_pause
            }
            data.setdefault('paused_tasks', []).insert(0, paused_item)
            data["current_ticket"] = None
            data.pop("task_start_time", None)
    
    def _get_all_known_tickets(self, data):
        """Get all known ticket names."""
        all_tickets_set = set()
        all_tickets_set.update(data.get("sub_tasks", {}).keys())
        all_tickets_set.update(data.get("notes", {}).keys())
        for paused_item in data.get("paused_tasks", []):
            if paused_item.get("ticket"):
                all_tickets_set.add(paused_item["ticket"])
        return sorted(list(filter(None, all_tickets_set)))


class CompleteTaskCommand(BaseCommand):
    """Command to mark a task as completed."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        current_ticket = data.get("current_ticket")
        if not current_ticket:
            return CommandResult(
                success=False,
                message=t('cmd_err_no_active_task_to_complete')
            )
        
        # Mark task as completed
        if current_ticket not in data.get("completed_tickets", []):
            data.setdefault("completed_tickets", []).append(current_ticket)
        
        # Clear focus if this was the focused task
        if data.get("focused_ticket") == current_ticket:
            data["focused_ticket"] = None
            data["focused_subtask"] = None
        
        # Clear current task
        data["current_ticket"] = None
        data.pop("task_start_time", None)
        
        return CommandResult(
            success=True,
            data_modified=True,
            message=t('cmd_info_task_completed_and_hidden', name=current_ticket),
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "x - Mark current task as completed"


class SwitchTaskCommand(BaseCommand):
    """Command to switch to a different task."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        if not args or not args[0]:
            return CommandResult(success=False, message="No task specified")
        
        identifier = " ".join(args)
        all_displayable_tickets = self._get_displayable_tickets(data)
        target_ticket = None
        
        # Try to parse as index
        try:
            target_idx = int(identifier) - 1
            if 0 <= target_idx < len(all_displayable_tickets):
                target_ticket = all_displayable_tickets[target_idx]
            else:
                return CommandResult(
                    success=False,
                    message=t('cmd_err_invalid_index', index=int(identifier))
                )
        except ValueError:
            # Try to find by name match
            matches = [
                t_name for t_name in all_displayable_tickets
                if identifier.lower() in t_name.lower()
            ]
            if len(matches) == 0:
                return CommandResult(
                    success=False,
                    message=t('cmd_err_unknown_command_or_ticket', id=identifier)
                )
            elif len(matches) == 1:
                target_ticket = matches[0]
            else:
                options_str = ", ".join([f"'{name}'" for name in matches[:3]])
                if len(matches) > 3:
                    options_str += "..."
                return CommandResult(
                    success=False,
                    message=t('cmd_err_multiple_tickets_found', options=options_str)
                )
        
        if not target_ticket:
            return CommandResult(success=False, message="No target ticket found")
        
        # Check if already current
        if data.get("current_ticket") == target_ticket:
            return CommandResult(
                success=False,
                message=t('cmd_err_task_already_active', name=target_ticket)
            )
        
        # Switch to the task
        self._pause_current_task(data)
        self._resume_or_activate_task(data, target_ticket)
        
        return CommandResult(
            success=True,
            data_modified=True,
            message=t('cmd_info_switched_to_task', name=target_ticket),
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "<task_name_or_number> - Switch to specified task"
    
    def _get_displayable_tickets(self, data):
        """Get all displayable ticket names."""
        completed_tickets = data.get("completed_tickets", [])
        all_tickets_set = set()
        all_tickets_set.update(data.get("sub_tasks", {}).keys())
        all_tickets_set.update(data.get("notes", {}).keys())
        for paused_item in data.get("paused_tasks", []):
            if paused_item.get("ticket"):
                all_tickets_set.add(paused_item["ticket"])
        return sorted([t for t in filter(None, all_tickets_set) if t not in completed_tickets])
    
    def _pause_current_task(self, data):
        """Pause the current task."""
        current_to_pause = data.get("current_ticket")
        if current_to_pause:
            sub_tasks_for_pause = data.get("sub_tasks", {}).get(current_to_pause, {})
            notes_for_pause = data.get("notes", {}).get(current_to_pause, [])
            start_time_for_pause = data.get("task_start_time")
            paused_item = {
                'ticket': current_to_pause,
                'sub_tasks': copy.deepcopy(sub_tasks_for_pause),
                'notes': copy.deepcopy(notes_for_pause),
                'task_start_time': start_time_for_pause
            }
            data.setdefault('paused_tasks', []).insert(0, paused_item)
            data["current_ticket"] = None
            data.pop("task_start_time", None)
    
    def _resume_or_activate_task(self, data, target_ticket):
        """Resume a paused task or activate an existing task."""
        # Try to find in paused tasks first
        for i, paused_task_item in enumerate(data.get("paused_tasks", [])):
            if paused_task_item.get("ticket") == target_ticket:
                resumed_item = data["paused_tasks"].pop(i)
                data['current_ticket'] = target_ticket
                data['task_start_time'] = resumed_item.get('task_start_time', time.time())
                
                # Restore subtasks with migration
                resumed_sub_tasks = resumed_item.get('sub_tasks', {})
                if isinstance(resumed_sub_tasks, dict):
                    data.setdefault("sub_tasks", {})[target_ticket] = self._migrate_subtasks(resumed_sub_tasks)
                
                # Restore notes
                data.setdefault("notes", {})[target_ticket] = resumed_item.get('notes', [])
                return
        
        # Not found in paused tasks, activate existing task
        data['current_ticket'] = target_ticket
        data['task_start_time'] = time.time()
        data.setdefault("sub_tasks", {}).setdefault(target_ticket, {})
        data.setdefault("notes", {}).setdefault(target_ticket, [])
    
    def _migrate_subtasks(self, subtasks_raw):
        """Migrate subtasks from old format to new format."""
        migrated = {}
        for sub_name, sub_details in subtasks_raw.items():
            if not isinstance(sub_details, dict):
                migrated[sub_name] = {
                    "status": "done" if bool(sub_details) else "todo",
                    "notes": [],
                    "pr_url": None,
                    "pr_status": None,
                    "jira_refreshed": None
                }
            else:
                # Migrate status from old boolean fields
                current_status = sub_details.get("status", "todo")
                if sub_details.get("hidden", False):
                    current_status = "hidden"
                elif sub_details.get("done", False):
                    current_status = "done"
                elif sub_details.get("focused", False):
                    current_status = "focused"
                
                migrated_details = {
                    "status": current_status,
                    "notes": sub_details.get("notes", []),
                    "pr_url": sub_details.get("pr_url"),
                    "pr_status": sub_details.get("pr_status"),
                    "jira_refreshed": sub_details.get("jira_refreshed")
                }
                migrated[sub_name] = migrated_details
        return migrated


class ToggleHiddenTasksCommand(BaseCommand):
    """Command to toggle display of hidden tasks."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        data["show_hidden_tasks"] = not data.get("show_hidden_tasks", False)
        return CommandResult(
            success=True,
            data_modified=True,
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "t - Toggle display of hidden tasks"


class DismissNotificationCommand(BaseCommand):
    """Command to dismiss web change notifications."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        if len(args) < 2:
            return CommandResult(
                success=False,
                message=t('cmd_usage_ok')
            )
        
        try:
            index_to_remove = int(args[1]) - 1
            web_notifications = data.get("web_change_notifications", [])
            if 0 <= index_to_remove < len(web_notifications):
                web_notifications.pop(index_to_remove)
                return CommandResult(
                    success=True,
                    data_modified=True,
                    message=t('cmd_info_notification_dismissed')
                )
            else:
                return CommandResult(
                    success=False,
                    message=t('cmd_err_invalid_index')
                )
        except ValueError:
            return CommandResult(
                success=False,
                message=t('cmd_err_invalid_index')
            )
    
    def get_usage(self) -> str:
        return "ok <number> - Dismiss notification by number"
