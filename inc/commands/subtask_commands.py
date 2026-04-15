"""
Subtask management commands.

This module contains commands for managing subtasks within projects:
adding subtasks, hiding subtasks, focusing on subtasks, etc.
"""

import logging
import re
import sys
import time
from datetime import datetime
from typing import List

import inc.config_manager
from inc.commands.base_command import BaseCommand, CommandContext, CommandResult
from inc.helpers import t


class AddSubtaskCommand(BaseCommand):
    """Command to add a subtask to the current project."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        if len(args) < 2:
            return CommandResult(
                success=False,
                message=t('cmd_usage_add_subtask')
            )
        
        sub_task_input = " ".join(args[1:])
        
        # Check if input is a Jira ticket ID (e.g., DCURJ-1234)
        jira_ticket_pattern = re.match(r'^([A-Z]+[A-Z0-9]*-\d+)$', sub_task_input.strip())
        
        if jira_ticket_pattern:
            return self._handle_jira_ticket_addition(data, sub_task_input.strip())
        else:
            return self._handle_regular_subtask_addition(data, sub_task_input)
    
    def get_usage(self) -> str:
        return "a <subtask_name_or_jira_id> - Add a subtask"
    
    def _handle_jira_ticket_addition(self, data, jira_ticket_id: str):
        """Handle addition of a Jira ticket ID."""
        # Convert Jira ID to full URL
        jira_base_url = inc.config_manager.config.get('JIRA_URL', 'https://pinja.atlassian.net')
        sub_task_url = f"{jira_base_url}/browse/{jira_ticket_id}"
        
        # Extract project prefix from ticket ID
        ticket_prefix = jira_ticket_id.split('-')[0]
        
        # Find the best matching project based on ticket patterns
        target_project = self._find_best_matching_project(data, ticket_prefix)
        current_ticket = data.get("current_ticket")
        
        if target_project:
            switch_message = ""
            if current_ticket != target_project:
                # Need to switch projects
                self._pause_current_task(data)
                data["current_ticket"] = target_project
                data["task_start_time"] = time.time()
                switch_message = f" (switched to {target_project})"
        elif current_ticket:
            target_project = current_ticket
            switch_message = ""
        else:
            return CommandResult(
                success=False,
                message=t('cmd_err_no_matching_project', prefix=ticket_prefix)
            )
        
        # Add subtask to target project
        target_subtasks = data.setdefault("sub_tasks", {}).setdefault(target_project, {})
        if sub_task_url not in target_subtasks:
            target_subtasks[sub_task_url] = {
                "status": "todo",
                "notes": [],
                "pr_url": None,
                "pr_status": None,
                "jira_refreshed": None
            }
            
            # Handle time tracking when focus changes
            from inc.time_tracker import stop_focus_timer_and_log, start_focus_timer
            
            # Stop current timer if there was a focused subtask
            old_focused_ticket = data.get("focused_ticket")
            old_focused_subtask = data.get("focused_subtask")
            if old_focused_ticket and old_focused_subtask:
                stop_focus_timer_and_log(data)
            
            # Set focus on the newly added subtask
            data["focused_ticket"] = target_project
            data["focused_subtask"] = sub_task_url
            
            # Start new timer for the focused subtask if work session is active
            work_session = data.get("work_session", {})
            if work_session.get("active"):
                start_focus_timer(data)
            
            # Don't reset checkin timer when switching focus within active work - 
            # let the scheduler handle check-ins based on actual elapsed time
            
            if switch_message:
                message = t('cmd_info_subtask_added_with_switch',
                           ticket=jira_ticket_id,
                           project=target_project,
                           old_project=current_ticket or "None")
            else:
                message = t('cmd_info_subtask_added',
                           ticket=jira_ticket_id,
                           project=target_project)
            
            return CommandResult(
                success=True,
                data_modified=True,
                message=message,
                request_redraw=True
            )
        else:
            return CommandResult(
                success=False,
                message=t('cmd_err_ticket_already_exists',
                         ticket=jira_ticket_id,
                         project=target_project)
            )
    
    def _handle_regular_subtask_addition(self, data, subtask_name: str):
        """Handle addition of a regular subtask."""
        current_ticket = data.get("current_ticket")
        if not current_ticket:
            return CommandResult(
                success=False,
                message=t('cmd_err_no_active_task_for_subtask')
            )
        
        current_subtasks = data.setdefault("sub_tasks", {}).setdefault(current_ticket, {})
        if subtask_name not in current_subtasks:
            current_subtasks[subtask_name] = {
                "status": "todo",
                "notes": [],
                "pr_url": None,
                "pr_status": None,
                "jira_refreshed": None
            }
            return CommandResult(
                success=True,
                data_modified=True,
                message=t('cmd_info_subtask_added',
                         ticket=subtask_name,
                         project=current_ticket),
                request_redraw=True
            )
        else:
            return CommandResult(
                success=False,
                message=t('cmd_err_subtask_exists', name=subtask_name)
            )
    
    def _find_best_matching_project(self, data, ticket_prefix: str):
        """Find the best matching project based on existing ticket patterns."""
        best_match_project = None
        best_match_score = 0
        
        for project_name, subtasks in data.get("sub_tasks", {}).items():
            if project_name in data.get("completed_tickets", []):
                continue  # Skip completed projects
            
            # Count matching tickets in this project
            matching_count = 0
            total_jira_tickets = 0
            
            for subtask_url in subtasks.keys():
                # Extract ticket ID from URL
                url_ticket_match = re.search(r'/browse/([A-Z]+[A-Z0-9]*-\d+)$', subtask_url)
                if url_ticket_match:
                    total_jira_tickets += 1
                    existing_ticket_id = url_ticket_match.group(1)
                    existing_prefix = existing_ticket_id.split('-')[0]
                    if existing_prefix == ticket_prefix:
                        matching_count += 1
            
            # Calculate match score
            if total_jira_tickets > 0:
                match_ratio = matching_count / total_jira_tickets
                score = match_ratio * 1000 + matching_count
                
                logging.debug(f"Project {project_name}: {matching_count}/{total_jira_tickets} "
                             f"match {ticket_prefix} (score: {score:.1f})")
                
                if score > best_match_score:
                    best_match_score = score
                    best_match_project = project_name
        
        logging.debug(f"Best match for {ticket_prefix}: {best_match_project} "
                     f"(score: {best_match_score:.1f})")
        return best_match_project
    
    def _pause_current_task(self, data):
        """Pause the current task."""
        current_to_pause = data.get("current_ticket")
        if current_to_pause:
            import copy
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


class HideSubtaskCommand(BaseCommand):
    """Command to hide a selected subtask."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        current_ticket = data.get("current_ticket")
        if not current_ticket:
            return CommandResult(
                success=False,
                message=t('cmd_prompt_select_subtask_to_hide')
            )
        
        if context.selected_subtask_idx == -1 or not context.current_ticket_subtask_list:
            return CommandResult(
                success=False,
                message=t('cmd_prompt_select_subtask_to_hide')
            )
        
        if not (0 <= context.selected_subtask_idx < len(context.current_ticket_subtask_list)):
            return CommandResult(
                success=False,
                message=t('cmd_prompt_select_subtask_to_hide')
            )
        
        subtask_name, subtask_details = context.current_ticket_subtask_list[context.selected_subtask_idx]
        
        # Hide the subtask
        if (current_ticket in data.get("sub_tasks", {}) and
            subtask_name in data["sub_tasks"][current_ticket]):
            
            data["sub_tasks"][current_ticket][subtask_name]["status"] = "hidden"
            
            # Clear focus if this was the focused subtask
            if data.get("focused_subtask") == subtask_name:
                data["focused_subtask"] = None
            
            return CommandResult(
                success=True,
                data_modified=True,
                message=t('cmd_info_subtask_hidden', name=subtask_name),
                request_redraw=True
            )
        else:
            return CommandResult(
                success=False,
                message=t('cmd_err_subtask_not_found')
            )
    
    def get_usage(self) -> str:
        return "d - Hide selected subtask"


class FocusSubtaskCommand(BaseCommand):
    """Command to focus on a selected subtask."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        current_ticket = data.get("current_ticket")
        if not current_ticket:
            return CommandResult(
                success=False,
                message=t('cmd_prompt_select_subtask_for_focus')
            )
        
        if context.selected_subtask_idx == -1 or not context.current_ticket_subtask_list:
            return CommandResult(
                success=False,
                message=t('cmd_prompt_select_subtask_for_focus')
            )
        
        if not (0 <= context.selected_subtask_idx < len(context.current_ticket_subtask_list)):
            return CommandResult(
                success=False,
                message=t('cmd_prompt_select_subtask_for_focus')
            )
        
        subtask_name, subtask_details = context.current_ticket_subtask_list[context.selected_subtask_idx]
        current_status = subtask_details.get("status", "todo")
        
        # Handle time tracking transition properly
        from inc.time_tracker import stop_focus_timer_and_log, start_focus_timer
        
        # Stop current timer and log time for previously focused subtask
        old_focused_ticket = data.get("focused_ticket")
        old_focused_subtask = data.get("focused_subtask")
        if old_focused_ticket and old_focused_subtask:
            stop_focus_timer_and_log(data)
        
        # Unfocus all subtasks across all projects (not just current ticket)
        for ticket_subtasks in data["sub_tasks"].values():
            for st in ticket_subtasks.values():
                if isinstance(st, dict) and st.get("status") == "focused":
                    st["status"] = "todo"
        
        if current_status == "focused":
            # Unfocus the subtask
            data["sub_tasks"][current_ticket][subtask_name]["status"] = "todo"
            data["focused_ticket"] = None
            data["focused_subtask"] = None
            message = t('cmd_info_focus_cleared')
        else:
            # Focus the subtask
            data["sub_tasks"][current_ticket][subtask_name]["status"] = "focused"
            data["focused_ticket"] = current_ticket
            data["focused_subtask"] = subtask_name
            
            # Start new timer for the focused subtask
            work_session = data.get("work_session", {})
            if work_session.get("active"):
                start_focus_timer(data)
                message = t('cmd_info_subtask_focus_set', name=subtask_name)
            else:
                # Work session not active - focus but don't start timer
                message = t('cmd_info_subtask_focus_set_no_timer', name=subtask_name)
        
        # Don't reset checkin timer on focus toggle - maintain continuous work tracking
        # Only the scheduler should manage check-in timing based on actual work periods
        
        return CommandResult(
            success=True,
            data_modified=True,
            message=message,
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "f - Toggle focus on selected subtask"
    
    # Removed _log_previous_focused_time - now handled by unified time tracking system


class FocusCommand(BaseCommand):
    """Command to focus on a task or subtask by name/identifier."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        if len(args) < 2:
            # Clear focus if no arguments
            from inc.time_tracker import stop_focus_timer_and_log
            
            # Stop current timer and log time
            old_focused_ticket = data.get("focused_ticket")
            old_focused_subtask = data.get("focused_subtask")
            if old_focused_ticket and old_focused_subtask:
                stop_focus_timer_and_log(data)
            
            data["focused_ticket"] = None
            data["focused_subtask"] = None
            for ticket_subtasks in data["sub_tasks"].values():
                for st in ticket_subtasks.values():
                    if isinstance(st, dict) and st.get("status") == "focused":
                        st["status"] = "todo"
            
            # Don't automatically reset checkin timer when clearing focus
            # Let the user explicitly end their work day or let the scheduler handle idle timeout
            
            return CommandResult(
                success=True,
                data_modified=True,
                message=t('cmd_info_focus_cleared'),
                request_redraw=True
            )
        
        identifier = " ".join(args[1:])
        completed_tickets = data.get("completed_tickets", [])
        
        # First, search for a subtask
        found_subtasks = []
        for ticket_name, subtasks in data.get("sub_tasks", {}).items():
            if ticket_name in completed_tickets:
                continue
            for st_name, st_details in subtasks.items():
                if identifier.lower() in st_name.lower():
                    found_subtasks.append((ticket_name, st_name))
        
        target_ticket = None
        target_subtask = None
        
        if len(found_subtasks) == 1:
            target_ticket, target_subtask = found_subtasks[0]
        elif len(found_subtasks) > 1:
            options = ", ".join([st for _, st in found_subtasks])
            return CommandResult(
                success=False,
                message=t('cmd_err_multiple_subtasks_found', options=options)
            )
        
        # If no subtask found, search for a main ticket
        if not target_ticket:
            all_displayable_tickets = self._get_displayable_tickets(data)
            try:
                idx = int(identifier) - 1
                if 0 <= idx < len(all_displayable_tickets):
                    target_ticket = all_displayable_tickets[idx]
            except ValueError:
                matches = [
                    t_name for t_name in all_displayable_tickets
                    if identifier.lower() in t_name.lower()
                ]
                if len(matches) == 1:
                    target_ticket = matches[0]
                elif len(matches) > 1:
                    options = ", ".join(matches)
                    return CommandResult(
                        success=False,
                        message=t('cmd_err_multiple_tickets_found', options=options)
                    )
        
        if target_ticket:
            # Handle time tracking transition
            from inc.time_tracker import stop_focus_timer_and_log, start_focus_timer
            
            # Stop current timer and log time for previously focused subtask
            old_focused_ticket = data.get("focused_ticket")
            old_focused_subtask = data.get("focused_subtask")
            if old_focused_ticket and old_focused_subtask:
                stop_focus_timer_and_log(data)
            
            # Clear all previous focuses
            data["focused_ticket"] = None
            data["focused_subtask"] = None
            for ticket_subtasks in data["sub_tasks"].values():
                for st in ticket_subtasks.values():
                    if isinstance(st, dict) and st.get("status") == "focused":
                        st["status"] = "todo"
            
            # Set new focus
            data["focused_ticket"] = target_ticket
            
            if target_subtask:
                data["sub_tasks"][target_ticket][target_subtask]["status"] = "focused"
                data["focused_subtask"] = target_subtask
                
                # Start new timer for the focused subtask if work session is active
                work_session = data.get("work_session", {})
                if work_session.get("active"):
                    start_focus_timer(data)
            
            # Don't reset checkin timer when changing focus during active work
            # This allows proper time tracking across focus changes
            
            return CommandResult(
                success=True,
                data_modified=True,
                message=t('cmd_info_focus_set', name=target_ticket),
                request_redraw=True
            )
        else:
            return CommandResult(
                success=False,
                message=t('cmd_err_ticket_not_found', name=identifier)
            )
    
    def get_usage(self) -> str:
        return "focus [<task_or_subtask>] - Focus on task/subtask or clear focus"
    
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
    
    # Removed duplicate _log_previous_focused_time - now handled by unified time tracking system


class AddPRCommand(BaseCommand):
    """Command to add a PR URL to a selected subtask."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        current_ticket = data.get("current_ticket")
        if not current_ticket:
            return CommandResult(
                success=False,
                message=t('cmd_prompt_select_subtask_for_pr')
            )
        
        if context.selected_subtask_idx == -1 or not context.current_ticket_subtask_list:
            return CommandResult(
                success=False,
                message=t('cmd_prompt_select_subtask_for_pr')
            )
        
        if len(args) < 2:
            return CommandResult(
                success=False,
                message=t('cmd_usage_add_pr')
            )
        
        if not (0 <= context.selected_subtask_idx < len(context.current_ticket_subtask_list)):
            return CommandResult(
                success=False,
                message=t('cmd_prompt_select_subtask_for_pr')
            )
        
        pr_url = " ".join(args[1:])

        # Simple validation for Bitbucket URL
        from inc.integrations.pr_monitor import is_bitbucket_pr_url
        if not is_bitbucket_pr_url(pr_url):
            return CommandResult(
                success=False,
                message="Error: PR URL must be a valid Bitbucket URL "
                        "(https://bitbucket.org/{workspace}/{repo}/pull-requests/{id})"
            )
        
        subtask_name, _ = context.current_ticket_subtask_list[context.selected_subtask_idx]
        
        # Add PR URL to subtask
        if (current_ticket in data.get("sub_tasks", {}) and
            subtask_name in data["sub_tasks"][current_ticket]):
            
            data["sub_tasks"][current_ticket][subtask_name]["pr_url"] = pr_url
            data["sub_tasks"][current_ticket][subtask_name]["pr_status"] = None  # Reset status
            
            # **FIX:** Trigger asynchronous PR polling for this subtask
            try:
                from inc.integrations.pr_monitor import queue_pr_for_polling
                # Queue all visible PRs. This will include the one just added.
                queue_pr_for_polling(data)
            except Exception as e:
                # Don't fail the command if PR polling fails
                logging.warning(f"Warning: Failed to queue PR for polling after adding URL: {e}")
            
            return CommandResult(
                success=True,
                data_modified=True,
                message=t('cmd_info_pr_added', name=subtask_name),
                request_redraw=True
            )
        else:
            return CommandResult(
                success=False,
                message=t('cmd_err_subtask_not_found')
            )
    
    def get_usage(self) -> str:
        return "pr <url> - Add pull request URL to selected subtask"