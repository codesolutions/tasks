#!/usr/bin/env python3
"""
Migration script for converting old PR data format to new schema.

This script migrates from the old format where PR comments were mixed
with notes (prefixed with "*PR*") to the new structured pr_details format.
"""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
import sys
import os

# Add the parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inc.utils.constants import DATA_FILE


def parse_pr_comment_note(note_text: str) -> dict:
    """
    Parse a note that starts with "*PR*" into structured comment data.
    
    Args:
        note_text: Note text in format "*PR* Author: Comment text"
        
    Returns:
        Dict with comment data or None if couldn't parse
    """
    # Pattern: "*PR* AuthorName: Comment text"
    pattern = r'^\*PR\*\s+([^:]+):\s*(.+)$'
    match = re.match(pattern, note_text, re.DOTALL)
    
    if match:
        author_name = match.group(1).strip()
        comment_text = match.group(2).strip()
        
        return {
            "id": f"imported_{hash(note_text)}",  # Generate unique ID
            "parent_id": None,
            "author": {
                "id": author_name.lower().replace(' ', '.'),
                "displayName": author_name
            },
            "text": comment_text,
            "created": datetime.now().isoformat() + 'Z',
            "updated": datetime.now().isoformat() + 'Z',
            "imported": True
        }
    
    return None


def migrate_subtask_pr_data(subtask_data: dict) -> bool:
    """
    Migrate PR data for a single subtask.
    
    Args:
        subtask_data: The subtask dict to migrate
        
    Returns:
        True if any changes were made
    """
    changed = False
    
    # Check if already migrated
    if subtask_data.get('pr_details', {}).get('version') == 2:
        return False
        
    pr_url = subtask_data.get('pr_url')
    if not pr_url:
        return False
        
    # Initialize new pr_details structure
    pr_details = {
        "meta": {
            "id": None,  # Will be populated by API
            "title": "Unknown PR",  # Will be populated by API
            "description": "",
            "author": {
                "id": "unknown",
                "displayName": "Unknown"
            },
            "created": datetime.now().isoformat() + 'Z',
            "updated": datetime.now().isoformat() + 'Z',
            "url": pr_url,
            "state": "OPEN",
            "merge_status": "UNKNOWN"
        },
        "reviewers": [],
        "comments": [],
        "diffs": [],
        "last_synced": None,  # Will be populated on next API poll
        "version": 2
    }
    
    # Extract PR ID from URL if possible
    pr_id_match = re.search(r'/pull-requests/(\d+)', pr_url)
    if pr_id_match:
        pr_details["meta"]["id"] = int(pr_id_match.group(1))
    
    # Migrate old pr_details if it exists
    old_pr_details = subtask_data.get('pr_details', {})
    if old_pr_details:
        # Migrate approvers to reviewers format
        approvers_formatted = old_pr_details.get('approvers_formatted', [])
        for approver_text in approvers_formatted:
            # Parse format like "✅ John Doe" or "❓ Jane Smith"  
            parts = approver_text.split(' ', 1)
            if len(parts) == 2:
                status_emoji, name = parts
                status = 'UNAPPROVED'  # default
                if status_emoji == '✅':
                    status = 'APPROVED'
                elif status_emoji == '❌':
                    status = 'NEEDS_WORK'
                
                pr_details["reviewers"].append({
                    "id": name.lower().replace(' ', '.'),
                    "displayName": name,
                    "status": status,
                    "approved_date": datetime.now().isoformat() + 'Z' if status == 'APPROVED' else None
                })
        
        # Try to extract title from status_text
        status_text = old_pr_details.get('status_text', '')
        if status_text and status_text not in ['waiting', 'merged', 'declined']:
            pr_details["meta"]["title"] = f"PR - {status_text}"
    
    # Extract and migrate PR comments from notes
    notes = subtask_data.get('notes', [])
    regular_notes = []
    pr_comments = []
    
    for note in notes:
        if isinstance(note, str) and note.startswith('*PR*'):
            # This is a PR comment
            comment_data = parse_pr_comment_note(note)
            if comment_data:
                pr_comments.append(comment_data)
                changed = True
        else:
            # Regular note, keep it
            regular_notes.append(note)
    
    # Update the subtask data
    if pr_comments:
        pr_details["comments"] = pr_comments
        subtask_data['notes'] = regular_notes  # Remove PR comments from notes
        changed = True
    
    # Store the new pr_details
    subtask_data['pr_details'] = pr_details
    changed = True
    
    # Keep old fields for backward compatibility
    # pr_url and pr_status will remain unchanged
    
    return changed


def migrate_data_file(data_file_path: str, backup: bool = True) -> bool:
    """
    Migrate the entire data file to new PR format.
    
    Args:
        data_file_path: Path to the data file
        backup: Whether to create a backup
        
    Returns:
        True if migration was performed
    """
    if not os.path.exists(data_file_path):
        print(f"Data file {data_file_path} does not exist")
        return False
        
    # Load current data
    try:
        with open(data_file_path, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error loading data file: {e}")
        return False
    
    # Check if already migrated
    if data.get('pr_data_version') == 2:
        print("Data file already migrated to version 2")
        return False
        
    # Create backup if requested
    if backup:
        backup_path = f"{data_file_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(data_file_path, backup_path)
        print(f"Backup created: {backup_path}")
    
    # Migrate each subtask
    total_migrated = 0
    for project_name, subtasks in data.get('sub_tasks', {}).items():
        if not isinstance(subtasks, dict):
            continue
            
        for subtask_name, subtask_data in subtasks.items():
            if not isinstance(subtask_data, dict):
                continue
                
            if migrate_subtask_pr_data(subtask_data):
                total_migrated += 1
                print(f"Migrated PR data for {project_name}/{subtask_name}")
    
    # Mark as migrated
    data['pr_data_version'] = 2
    data['pr_data_migrated'] = datetime.now().isoformat() + 'Z'
    
    # Save migrated data
    try:
        with open(data_file_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Migration completed: {total_migrated} subtasks migrated")
        return True
    except Exception as e:
        print(f"Error saving migrated data: {e}")
        return False


def main():
    """Main migration function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate PR data to new schema')
    parser.add_argument('--no-backup', action='store_true', 
                       help='Skip creating backup file')
    parser.add_argument('--data-file', default=DATA_FILE,
                       help='Path to data file (default: jira_data.json)')
    
    args = parser.parse_args()
    
    print("Starting PR data migration...")
    success = migrate_data_file(args.data_file, backup=not args.no_backup)
    
    if success:
        print("Migration completed successfully!")
        return 0
    else:
        print("Migration failed or was not needed")
        return 1


if __name__ == '__main__':
    sys.exit(main())