"""
logging_config.py

Advanced logging configuration with daily rotating log files.
"""

import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path


def setup_logging(log_level=logging.INFO, log_dir="logs", console_output=False):
    """
    Set up logging with daily rotating file handlers and optional console output.
    
    Args:
        log_level: Logging level (default: INFO)
        log_dir: Directory to store log files (default: "logs")
        console_output: Whether to also output logs to console (default: False)
    """
    # Create logs directory if it doesn't exist
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    
    # Create custom formatter
    formatter = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)s:%(name)s:%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear any existing handlers
    root_logger.handlers.clear()
    
    # Console handler (optional)
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    
    # Daily rotating file handler
    today = datetime.now().strftime("%Y-%m-%d")
    log_filename = log_path / f"bot_{today}.log"
    
    # TimedRotatingFileHandler automatically creates new files daily
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_filename,
        when='midnight',  # Rotate at midnight
        interval=1,       # Every 1 day
        backupCount=30,   # Keep 30 days of logs
        encoding='utf-8'
    )
    
    # Set the suffix for rotated files to include date
    file_handler.suffix = "%Y-%m-%d"
    
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # Create a specific logger for the bot
    bot_logger = logging.getLogger("feedback_bot")
    bot_logger.setLevel(log_level)
    
    # Log the configuration (this will only appear in file if console_output=False)
    output_info = "file only" if not console_output else "file and console"
    bot_logger.info(f"Logging configured - Level: {logging.getLevelName(log_level)}, Output: {output_info}")
    bot_logger.info(f"Log files will be stored in: {log_path.absolute()}")
    bot_logger.info(f"Current log file: {log_filename}")
    bot_logger.info("Daily log rotation enabled (midnight, keep 30 days)")
    
    return bot_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the specified name.
    
    Args:
        name: Name for the logger
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name) 