import curses
import time
import threading
import os
import copy
from config_manager import load_config, load_translations, t, config
from ui_display import display_ui, show_notification
from command_handler import handle_input
from jira_api import get_and_save_jira_session, jira_data_poller
from polling import poll_pull_requests, event_notification_poller

# --- Global State ---
app_data = {}
data_lock = threading.Lock()
permanent_notifications = []
jira_cache = {}
jira_cache_lock = threading.Lock()
sent_notifications = set()

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jira_data.json")

def load_data():
    global app_data
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f: app_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): app_data = {}
    
    app_data.setdefault("current_ticket", None)
    app_data.setdefault("focused_ticket", None)
    app_data.setdefault("focused_subtask", None)
    app_data.setdefault("completed_tickets", [])
    app_data.setdefault("task_start_time", None)
    app_data.setdefault("sub_tasks", {})
    app_data.setdefault("meetings", [])
    app_data.setdefault("interruptions", [])
    app_data.setdefault("notes", {})
    app_data.setdefault("paused_tasks", [])
    app_data.setdefault("recurring_events", [])
    app_data.setdefault("daily_notes", {})
    
    for project_tasks in app_data.get("sub_tasks", {}).values():
        if isinstance(project_tasks, dict):
            for task_details in project_tasks.values():
                if isinstance(task_details, dict):
                    task_details.setdefault("pr_details", {})

def save_data():
    with data_lock:
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(app_data, f, indent=4, default=str, ensure_ascii=False)
        except (IOError, TypeError) as e:
            permanent_notifications.append(f"FATAL: Could not save data: {e}")

def main(stdscr):
    global app_data
    curses.curs_set(1)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    
    # Initialize colors
    try:
        curses.start_color()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(3, curses.COLOR_BLUE, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(6, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(7, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(8, curses.COLOR_WHITE, curses.COLOR_RED)
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(10, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(11, curses.COLOR_YELLOW, curses.COLOR_RED)
    except:
        pass

    load_data()
    
    # Start background threads
    threading.Thread(target=poll_pull_requests, args=(app_data, data_lock, save_data, permanent_notifications), daemon=True).start()
    threading.Thread(target=jira_data_poller, args=(app_data, data_lock, jira_cache, jira_cache_lock, permanent_notifications), daemon=True).start()
    threading.Thread(target=event_notification_poller, args=(app_data, data_lock, sent_notifications), daemon=True).start()
    
    command_buffer = ""
    request_full_redraw = True
    selected_subtask_idx = -1
    last_refresh = time.time()
    current_view = "main"
    show_help = True
    entity_for_notes = None
    selected_note_idx = -1
    daily_notes_date = date.today()

    while True:
        if time.time() - last_refresh > 1.0:
            request_full_redraw = True
            last_refresh = time.time()
            
        if request_full_redraw:
            with data_lock:
                display_ui(stdscr, app_data, command_buffer, full_redraw=True, selected_subtask_idx=selected_subtask_idx, 
                           current_view_mode=current_view, entity_for_dedicated_notes=entity_for_notes,
                           show_help_footer=show_help, current_date_for_daily_notes_arg=daily_notes_date, 
                           selected_note_idx=selected_note_idx, permanent_notifications=permanent_notifications)
            request_full_redraw = False

        try:
            key = stdscr.getch()
        except curses.error:
            key = -1

        if key != -1:
            if key in [curses.KEY_ENTER, 10, 13]:
                with data_lock:
                    # Collect all necessary arguments for handle_input
                    current_project = app_data.get("current_ticket")
                    visible_tasks = []
                    if current_project:
                        visible_tasks = [(k,v) for k,v in app_data.get("sub_tasks",{}).get(current_project,{}).items() if isinstance(v,dict) and not v.get("hidden")]
                    all_displayable_tickets = sorted(list(set(p for p in list(app_data.get("sub_tasks", {}).keys()) + [p['ticket'] for p in app_data.get("paused_tasks", [])] if p not in app_data.get("completed_tickets",[]))))

                    data_copy = copy.deepcopy(app_data)
                    app_data, result_flag = handle_input(data_copy, command_buffer.split(), stdscr,
                                          current_view_mode=current_view,
                                          selected_subtask_idx=selected_subtask_idx,
                                          selected_note_idx=selected_note_idx,
                                          current_ticket_subtask_list=visible_tasks,
                                          all_displayable_tickets_for_cmd=all_displayable_tickets,
                                          permanent_notifications_ref=permanent_notifications,
                                          jira_cache_ref=jira_cache,
                                          jira_cache_lock_ref=jira_cache_lock)

                if result_flag == "RUN_LOGIN": return "RESTART_FOR_LOGIN"
                elif result_flag is None: break
                elif result_flag == "TOGGLE_HELP": show_help = not show_help
                elif result_flag: save_data()
                
                command_buffer = ""
            elif key in [curses.KEY_BACKSPACE, 127, 8]:
                command_buffer = command_buffer[:-1]
            elif isinstance(key, int) and 32 <= key < 127:
                command_buffer += chr(key)
            elif key == curses.KEY_UP:
                if selected_subtask_idx > 0: selected_subtask_idx -= 1
                else: selected_subtask_idx = 0
            elif key == curses.KEY_DOWN:
                current_project = app_data.get("current_ticket", "")
                visible_tasks_len = len([v for v in app_data.get("sub_tasks",{}).get(current_project,{}).values() if isinstance(v,dict) and not v.get("hidden")])
                if selected_subtask_idx < visible_tasks_len - 1: selected_subtask_idx += 1
            elif key == ord('q') and not command_buffer:
                break
            
            request_full_redraw = True
        
        time.sleep(0.03)
        
    return "EXIT"

if __name__ == "__main__":
    while True:
        if not load_config():
            print("INFO: New 'config.json' created. Please edit it with your details and restart the application.")
            sys.exit()

        load_translations()
        
        if not os.path.exists(os.path.join(SCRIPT_DIR, config.get("JIRA_SESSION_FILE"))):
            if t('jira_login_prompt') not in permanent_notifications:
                permanent_notifications.append(t('jira_login_prompt'))

        result = "EXIT"
        try:
            result = curses.wrapper(main)
        except curses.error as e:
            print(f"\nCurses Error: {e}", file=sys.stderr)
        except Exception as e:
            import traceback
            print(f"\nUnexpected Error: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        finally:
            if 'curses' in sys.modules and not curses.isendwin():
                curses.endwin()

        if result == "RESTART_FOR_LOGIN":
            get_and_save_jira_session()
            print("\nLogin process finished. Restarting application in 3 seconds...")
            time.sleep(3)
            continue
        else:
            break
            
    print(t('app_closed'))

