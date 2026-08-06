from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
import logging

from .backup_service import run_database_backup

logger = logging.getLogger(__name__)

# Global scheduler instance so we can access it from views
_scheduler = None

def get_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
    return _scheduler

def reschedule_backup_job():
    scheduler = get_scheduler()
    
    try:
        from .models import BackupSetting
        backup_setting = BackupSetting.get_settings()
        
        freq = backup_setting.backup_frequency
        t = backup_setting.backup_time
        
        if freq == 'NEVER':
            # Remove the job if it exists
            if scheduler.get_job("daily_database_backup"):
                scheduler.remove_job("daily_database_backup")
                logger.info("APScheduler removed backup job because frequency is NEVER.")
            return

        # Configure cron trigger based on frequency
        kwargs = {
            'hour': t.hour,
            'minute': t.minute,
        }
        
        if freq == 'WEEKLY':
            kwargs['day_of_week'] = 'sun' # run on Sundays
        elif freq == 'MONTHLY':
            kwargs['day'] = '1' # run on 1st of month
            
        trigger = CronTrigger(**kwargs)
        
        scheduler.add_job(
            run_database_backup,
            trigger=trigger,
            id="daily_database_backup",
            replace_existing=True,
        )
        logger.info(f"APScheduler rescheduled backup job: {freq} at {t}")
    except Exception as e:
        logger.error(f"Failed to reschedule backup job: {e}")

def start_scheduler():
    scheduler = get_scheduler()
    reschedule_backup_job()
    scheduler.start()
    logger.info("APScheduler started.")
