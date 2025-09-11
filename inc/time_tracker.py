import threading
import time
from datetime import datetime, date
from typing import Dict, Any, Optional

# This module encapsulates time tracking concerns: entries, work sessions, and hourly check-ins.

DEFAULT_INTERVAL_MINUTES = 60
DEFAULT_FORGETFUL_MINUTES = 15


def _now_ts() -> float:
    return time.time()


def _today_iso() -> str:
    return date.today().isoformat()


def _utc_iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


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
    data.setdefault("time_log", {}).setdefault(entry_date_iso, []).append({
        "type": entry_type,  # 'task' | 'break' | 'meeting'
        "subtask": subtask,
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
    ws["active"] = True
    ws["current_timer_start_ts"] = _now_ts()
    ws["last_activity_ts"] = _now_ts()


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
                        # skip if no recent activity
                        if last_activity and (now_ts - last_activity) > forget_min * 60:
                            # user likely away; don't trigger
                            self.data_ref["last_checkin_ts"] = now_ts
                        else:
                            last_check = self.data_ref.get("last_checkin_ts")
                            if last_check is None:
                                self.data_ref["last_checkin_ts"] = now_ts
                            elif now_ts - last_check >= interval_min * 60:
                                # schedule a check-in of the last full interval
                                duration = interval_min * 60
                                suggested = None
                                if self.data_ref.get("focused_subtask") and self.data_ref.get("focused_ticket"):
                                    suggested = f"[{self.data_ref.get('focused_ticket')}] {self.data_ref.get('focused_subtask')}"
                                self.data_ref["pending_checkin"] = {
                                    "duration_seconds": duration,
                                    "started_at": _utc_iso_now(),
                                    "suggested_subtask": suggested
                                }
                                # mark last_checkin_ts to now to avoid retrigger
                                self.data_ref["last_checkin_ts"] = now_ts
                # sleep small increments to be responsive but not busy-wait
                self._stop.wait(5.0)
            except Exception:
                # swallow exceptions to keep the scheduler alive
                self._stop.wait(10.0)
