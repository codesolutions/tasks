#!/usr/bin/env python3
"""
PR data cache system for persistence across app restarts and project switches.

This module provides caching functionality for PR details data that persists
to disk and survives application restarts, similar to the Jira cache system.
"""

import pickle
import os
import threading
import time
from typing import Dict, Any, Optional
from inc.utils.constants import CACHE_DIR

# Global cache and lock
pr_cache: Dict[str, Any] = {}
pr_cache_lock = threading.Lock()

PR_CACHE_FILE = os.path.join(CACHE_DIR, "pr_cache.pkl")


def load_pr_cache() -> Dict[str, Any]:
    """
    Load PR cache from disk.
    
    Returns:
        Dictionary containing cached PR data
    """
    global pr_cache
    
    try:
        if os.path.exists(PR_CACHE_FILE):
            with open(PR_CACHE_FILE, 'rb') as f:
                pr_cache = pickle.load(f)
                # Loaded PR cache with {len(pr_cache)} entries
        else:
            pr_cache = {}
            # No PR cache file found, starting with empty cache
    except Exception as e:
        print(f"Error loading PR cache: {e}")
        pr_cache = {}
    
    return pr_cache


def save_pr_cache():
    """
    Save PR cache to disk.
    """
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with pr_cache_lock:
            with open(PR_CACHE_FILE, 'wb') as f:
                pickle.dump(pr_cache, f)
    except Exception as e:
        print(f"Error saving PR cache: {e}")


def get_pr_details_from_cache(pr_url: str) -> Optional[Dict[str, Any]]:
    """
    Get PR details from cache.
    
    Args:
        pr_url: The PR URL to look up
        
    Returns:
        Cached PR details or None if not found or expired
    """
    cache_key = pr_url
    
    with pr_cache_lock:
        cached_entry = pr_cache.get(cache_key)
        
        if not cached_entry:
            return None
        
        # Check if cache entry is too old (older than 6 hours)
        cache_timestamp = cached_entry.get('timestamp', 0)
        cache_age = time.time() - cache_timestamp
        max_age = 6 * 3600  # 6 hours
        
        if cache_age > max_age:
            # Remove expired entry
            del pr_cache[cache_key]
            return None
        
        return cached_entry.get('pr_details')


def store_pr_details_in_cache(pr_url: str, pr_details: Dict[str, Any]):
    """
    Store PR details in cache.
    
    Args:
        pr_url: The PR URL as cache key
        pr_details: The PR details dictionary to cache
    """
    cache_key = pr_url
    
    with pr_cache_lock:
        pr_cache[cache_key] = {
            'pr_details': pr_details,
            'timestamp': time.time()
        }
    
    # Save to disk periodically (not on every update to avoid I/O overhead)
    save_pr_cache()


def populate_pr_details_from_cache(data: Dict[str, Any]) -> int:
    """
    Populate missing PR details from cache for all subtasks.
    
    Args:
        data: The main application data dictionary
        
    Returns:
        Number of PR details restored from cache
    """
    restored_count = 0
    
    for ticket, subtasks in data.get("sub_tasks", {}).items():
        if not isinstance(subtasks, dict):
            continue
            
        for subtask_name, subtask_details in subtasks.items():
            if not isinstance(subtask_details, dict):
                continue
            
            pr_url = subtask_details.get("pr_url")
            if not pr_url:
                continue
            
            # Check if PR details are missing or incomplete
            existing_pr_details = subtask_details.get("pr_details", {})
            needs_restore = (
                not existing_pr_details or 
                existing_pr_details.get('version') != 2 or
                not existing_pr_details.get('comments')
            )
            
            if needs_restore:
                cached_pr_details = get_pr_details_from_cache(pr_url)
                if cached_pr_details:
                    subtask_details["pr_details"] = cached_pr_details
                    restored_count += 1
                    # Restored PR details from cache
    
    return restored_count


def cleanup_pr_cache():
    """
    Remove expired entries from cache.
    """
    current_time = time.time()
    max_age = 6 * 3600  # 6 hours
    
    with pr_cache_lock:
        expired_keys = []
        for key, entry in pr_cache.items():
            cache_timestamp = entry.get('timestamp', 0)
            if current_time - cache_timestamp > max_age:
                expired_keys.append(key)
        
        for key in expired_keys:
            del pr_cache[key]
        
        if expired_keys:
            pass  # Cleaned up expired PR cache entries


# Initialize cache on module import
load_pr_cache()