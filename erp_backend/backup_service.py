import os
import shutil
import time
from datetime import datetime
from django.conf import settings
from .models import BackupSetting

def run_database_backup():
    """
    Backs up the SQLite database to the configured backup directory
    and performs a cleanup of old backups based on retention days.
    """
    # 1. Fetch backup directory and retention days
    try:
        backup_settings = BackupSetting.get_settings()
        backup_dir = backup_settings.backup_directory
        retention_days = backup_settings.retention_days
    except Exception:
        backup_dir = None
        retention_days = 30
        
    if not backup_dir:
        backup_dir = "C:/ERP_Backups"

    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir, exist_ok=True)

    # 2. Check engine
    db_engine = settings.DATABASES['default']['ENGINE']
    if 'sqlite3' not in db_engine:
        print(f"Skipping backup: Not an SQLite database ({db_engine})")
        return False, "Not an SQLite database"

    db_path = settings.DATABASES['default']['NAME']
    if not os.path.exists(db_path):
        return False, "Database file not found"

    # 3. Create Backup
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"ERP_Backup_{timestamp}.sqlite3"
    backup_filepath = os.path.join(backup_dir, backup_filename)

    try:
        shutil.copy2(db_path, backup_filepath)
        print(f"Successfully backed up database to {backup_filepath}")
    except Exception as e:
        print(f"Failed to backup database: {e}")
        return False, str(e)

    # 4. Auto Cleanup
    cleanup_old_backups(backup_dir, retention_days)

    return True, backup_filepath

def cleanup_old_backups(backup_dir, retention_days):
    """
    Scans the backup directory for files matching ERP_Backup_* 
    and deletes files older than retention_days.
    """
    current_time = time.time()
    retention_seconds = retention_days * 86400

    for filename in os.listdir(backup_dir):
        if filename.startswith("ERP_Backup_") and filename.endswith(".sqlite3"):
            filepath = os.path.join(backup_dir, filename)
            try:
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > retention_seconds:
                    os.remove(filepath)
                    print(f"Cleaned up old backup: {filepath}")
            except Exception as e:
                print(f"Failed to cleanup {filepath}: {e}")
