import threading
import time
from datetime import datetime, date
from typing import Dict, Any, Optional

# This module encapsulates time tracking concerns: entries, work sessions, and hourly check-ins.

DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_FORGETFUL_MINUTES = 120


def _now_ts() -> float:
    return time.time()


def _today_iso() -> str:
    return date.today().isoformat()


def _utc_iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def normalize_subtask_identifier(subtask: str) -> str:
    """Convert any subtask format to standardized '[Project] TICKET-123' format."""
    if not subtask:
        return subtask
    
    # If it's already in [Project] TICKET format, return as is
    if subtask.startswith('[') and '] ' in subtask:
        return subtask
    
    # Extract from URL format: https://domain.com/browse/TICKET-123
    if 'browse/' in subtask:
        ticket_id = subtask.split('browse/')[-1]
        # For now, we'll use a generic project name if we can't determine it
        # In future, this could be enhanced to extract project from URL or config
        return f"[Unknown] {ticket_id}"
    
    # If it's just a ticket ID like DCMIN-906, try to find matching project
    if '-' in subtask and subtask.replace('-', '').replace('_', '').isalnum():
        # This looks like a ticket ID, return as is with Unknown project for now
        return f"[Unknown] {subtask}"
    
    return subtask


def find_matching_subtask(data: Dict[str, Any], target_subtask: str) -> Optional[str]:
    """Find an existing subtask that matches the target, handling various formats."""
    if not target_subtask:
        return None
    
    # Extract ticket ID from various formats
    target_ticket_id = None
    if 'browse/' in target_subtask:
        # URL format: https://domain.com/browse/TICKET-123
        target_ticket_id = target_subtask.split('browse/')[-1]
    elif target_subtask.startswith('[') and '] ' in target_subtask:
        # Already normalized format: [Project] TICKET-123
        target_ticket_id = target_subtask.split('] ', 1)[1]
    elif '-' in target_subtask and len(target_subtask.split('-')) == 2:
        # Direct ticket ID: TICKET-123
        parts = target_subtask.split('-')
        if parts[0].isalpha() and parts[1].isdigit():
            target_ticket_id = target_subtask.upper()
    
    if not target_ticket_id:
        # If we can't extract a ticket ID, just normalize and return
        return normalize_subtask_identifier(target_subtask)
    
    # Search through existing subtasks for matches
    best_match = None
    for ticket_name, subtasks in data.get("sub_tasks", {}).items():
        for subtask_name in subtasks.keys():
            # Extract ticket ID from existing subtask (could be URL format)
            existing_ticket_id = None
            if 'browse/' in subtask_name:
                existing_ticket_id = subtask_name.split('browse/')[-1]
            elif subtask_name.startswith('[') and '] ' in subtask_name:
                existing_ticket_id = subtask_name.split('] ', 1)[1]
            elif '-' in subtask_name:
                # Could be a direct ticket ID
                existing_ticket_id = subtask_name
            
            # Check for exact match
            if existing_ticket_id and existing_ticket_id.upper() == target_ticket_id.upper():
                # Found exact match - return in standardized format with correct project name
                return f"[{ticket_name}] {existing_ticket_id.upper()}"
    
    # No exact match found - try to find a good project to assign it to
    # Look for projects that have similar ticket prefixes
    target_prefix = target_ticket_id.split('-')[0] if '-' in target_ticket_id else target_ticket_id
    
    best_project = None
    max_score = 0
    
    for ticket_name, subtasks in data.get("sub_tasks", {}).items():
        matching_count = 0
        total_count = 0
        
        for subtask_name in subtasks.keys():
            # Extract ticket ID to check prefix
            existing_ticket_id = None
            if 'browse/' in subtask_name:
                existing_ticket_id = subtask_name.split('browse/')[-1]
            elif '-' in subtask_name:
                existing_ticket_id = subtask_name.split('/')[-1] if '/' in subtask_name else subtask_name
            
            if existing_ticket_id and '-' in existing_ticket_id:
                total_count += 1
                existing_prefix = existing_ticket_id.split('-')[0]
                if existing_prefix.upper() == target_prefix.upper():
                    matching_count += 1
        
        if total_count > 0:
            score = matching_count / total_count
            if score > max_score:
                max_score = score
                best_project = ticket_name
    
    if best_project and max_score > 0.5:  # At least 50% match
        return f"[{best_project}] {target_ticket_id.upper()}"
    
    # If no good match found, return with generic project name
    return f"[Unknown] {target_ticket_id.upper()}"


def ensure_time_tracking_defaults(data: Dict[str, Any]) -> None:
    """Add required keys for time tracking if missing and migrate older structures if needed."""
    data.setdefault("time_log", {})  # { "YYYY-MM-DD": [ {type, subtask, seconds, created_at} ] }
    data.setdefault("work_session", {"active": False, "last_activity_ts": None, "current_timer_start_ts": None})
    data.setdefault("last_checkin_ts", None)
    data.setdefault("pending_checkin", None)  # { "duration_seconds", "suggested_subtask", "started_at" }


def add_time_entry(data: Dict[str, Any], *, entry_date_iso: Optional[str] = None,
                   entry_type: str, subtask: Optional[str], seconds: int) -> None:
    """Append a time entry to the time_log structure."""
    if entry_date_iso is None:
        entry_date_iso = _today_iso()
    ensure_time_tracking_defaults(data)
    
    # Normalize and match subtask identifier if it's a task
    normalized_subtask = subtask
    if entry_type == "task" and subtask:
        normalized_subtask = find_matching_subtask(data, subtask)
    
    data.setdefault("time_log", {}).setdefault(entry_date_iso, []).append({
        "type": entry_type,  # 'task' | 'break' | 'meeting'
        "subtask": normalized_subtask,
        "seconds": int(max(0, seconds)),
        "created_at": _utc_iso_now(),
    })


def get_total_seconds_for_date_and_subtask(data: Dict[str, Any], entry_date_iso: str, subtask: str) -> int:
    log = data.get("time_log", {}).get(entry_date_iso, [])
    return sum(e.get("seconds", 0) for e in log if e.get("type") == "task" and e.get("subtask") == subtask)


def get_total_seconds_for_date(data: Dict[str, Any], entry_date_iso: str) -> int:
    log = data.get("time_log", {}).get(entry_date_iso, [])
    return sum(e.get("seconds", 0) for e in log)


def start_focus_timer(data: Dict[str, Any]) -> None:
    ensure_time_tracking_defaults(data)
    ws = data["work_session"]
    
    # Only start the timer if work session is already active
    # This prevents timing when a user hasn't explicitly started their work day
    if ws.get("active"):
        ws["current_timer_start_ts"] = _now_ts()
        ws["last_activity_ts"] = _now_ts()
    # Note: We don't set active=True here anymore - that should only happen in StartDayCommand


def stop_focus_timer_and_log(data: Dict[str, Any]) -> None:
    """Stops current timer and logs elapsed time to the focused subtask if any."""
    ensure_time_tracking_defaults(data)
    ws = data.get("work_session", {})
    start_ts = ws.get("current_timer_start_ts")
    focused_subtask = data.get("focused_subtask")
    focused_ticket = data.get("focused_ticket")

    if start_ts and focused_subtask and focused_ticket:
        elapsed = int(max(0, _now_ts() - start_ts))
        if elapsed > 0:
            add_time_entry(data, entry_type="task", subtask=f"[{focused_ticket}] {focused_subtask}", seconds=elapsed)
    ws["current_timer_start_ts"] = None


def end_work_day(data: Dict[str, Any]) -> None:
    stop_focus_timer_and_log(data)
    ws = data.get("work_session", {})
    ws["active"] = False


def note_user_activity(data: Dict[str, Any]) -> None:
    ensure_time_tracking_defaults(data)
    data["work_session"]["last_activity_ts"] = _now_ts()


def add_comment_to_latest_entry(data: Dict[str, Any], comment_text: str) -> bool:
    """Add a comment to the most recent time entry"""
    time_log = data.get("time_log", {})
    
    # Find the most recent entry across all dates
    latest_entry = None
    latest_date = None
    
    for date_iso, entries in time_log.items():
        if not entries:
            continue
        for entry in reversed(entries):  # Start from most recent in each day
            if latest_entry is None or entry["created_at"] > latest_entry["created_at"]:
                latest_entry = entry
                latest_date = date_iso
                break  # Only need the most recent from each day
    
    if latest_entry:
        latest_entry["comment"] = comment_text
        return True
    
    return False


class HourlyCheckinScheduler:
    """Background scheduler that triggers periodic check-ins based on configuration."""

    def __init__(self, data_ref: Dict[str, Any], config: Dict[str, Any], data_lock: threading.Lock):
        self.data_ref = data_ref
        self.config = config
        self.data_lock = data_lock
        self.thread = threading.Thread(target=self._run, daemon=True)
        self._stop = threading.Event()

    def start(self):
        if not self.thread.is_alive():
            self.thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                with self.data_lock:
                    ensure_time_tracking_defaults(self.data_ref)
                    tt_cfg = (self.config or {}).get("TIME_TRACKING", {})
                    enabled = bool(tt_cfg.get("ENABLED", True))
                    check_enabled = bool(tt_cfg.get("HOURLY_CHECKIN_ENABLED", True))
                    interval_min = int(tt_cfg.get("CHECKIN_INTERVAL_MINUTES", DEFAULT_INTERVAL_MINUTES))
                    forget_min = int(tt_cfg.get("FORGETFUL_MINUTES", DEFAULT_FORGETFUL_MINUTES))

                    if not (enabled and check_enabled):
                        pass
                    else:
                        now_ts = _now_ts()
                        ws = self.data_ref.get("work_session", {})
                        last_activity = ws.get("last_activity_ts")
                        # Auto-end work day if no recent activity AND work session is still active
                        if last_activity and (now_ts - last_activity) > forget_min * 60 and ws.get("active"):
                            # User has been idle too long - automatically end work day
                            # Log any remaining time if there's an active timer
                            if ws.get("current_timer_start_ts") and self.data_ref.get("focused_subtask"):
                                # Calculate elapsed time up to when user went idle
                                elapsed_seconds = int(last_activity - ws["current_timer_start_ts"])
                                if elapsed_seconds > 0:
                                    focused_ticket = self.data_ref.get("focused_ticket")
                                    focused_subtask = self.data_ref.get("focused_subtask")
                                    if focused_ticket and focused_subtask:
                                        # Use the standardized format for time logging
                                        normalized_subtask = f"[{focused_ticket}] {focused_subtask}"
                                        add_time_entry(self.data_ref, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
                            
                            # End the work session
                            ws["active"] = False
                            ws["end_time"] = _utc_iso_now()
                            ws.pop("current_timer_start_ts", None)
                            ws.pop("paused", None)  # Clear paused state if any
                            
                            # Clear any pending check-in since we're ending the day
                            self.data_ref["pending_checkin"] = None
                            self.data_ref["last_checkin_ts"] = now_ts
                            
                            # Set flag to show daily summary on next UI refresh
                            self.data_ref["show_auto_end_summary"] = True
                        else:
                            # Only create check-ins if work session is still active
                            if not ws.get("active"):
                                # Work session ended, don't create check-ins
                                pass
                            else:
                                last_check = self.data_ref.get("last_checkin_ts")
                                if last_check is None:
                                    self.data_ref["last_checkin_ts"] = now_ts
                                elif now_ts - last_check >= interval_min * 60:
                                    # Only create a new check-in if there isn't already one pending
                                    if not self.data_ref.get("pending_checkin"):
                                        # Get the actual timer start time for accurate duration calculation
                                        timer_start = ws.get("current_timer_start_ts")
                                        if timer_start:
                                            # Calculate actual time worked since timer started
                                            actual_work_duration = int(now_ts - timer_start)
                                        else:
                                            # Fallback to time since last check if no timer
                                            actual_work_duration = int(now_ts - last_check)
                                        
                                        suggested = None
                                        if self.data_ref.get("focused_subtask") and self.data_ref.get("focused_ticket"):
                                            suggested = f"[{self.data_ref.get('focused_ticket')}] {self.data_ref.get('focused_subtask')}"
                                        
                                        self.data_ref["pending_checkin"] = {
                                            "timer_start_ts": timer_start or last_check,  # Store original start time for UI display
                                            "suggested_subtask": suggested
                                        }
                                        # mark last_checkin_ts to now to avoid retrigger
                                        self.data_ref["last_checkin_ts"] = now_ts
                # sleep small increments to be responsive but not busy-wait
                self._stop.wait(5.0)
            except Exception:
                # swallow exceptions to keep the scheduler alive
                self._stop.wait(10.0)
