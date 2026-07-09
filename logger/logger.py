import logging
import os
import sys
from datetime import datetime
from config.settings import settings

# Global logger setup flag
_configured = False

def get_logger(name="platform"):
    """Returns a logger with the specified name."""
    return logging.getLogger(name)

def setup_global_logger():
    """Initializes global logging: console and logs/platform.log."""
    global _configured
    if _configured:
        return
    
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture everything at root level, filter in handlers

    # Console Handler (Human-friendly)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # General Platform File Handler (Structured)
    os.makedirs(settings.log_dir, exist_ok=True)
    platform_log_path = os.path.join(settings.log_dir, "platform.log")
    file_handler = logging.FileHandler(platform_log_path, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    _configured = True
    logging.getLogger("platform").info("Global logger initialized.")

def setup_pipeline_logger(execution_id: str):
    """Sets up a file handler dedicated to a specific pipeline execution."""
    root_logger = logging.getLogger()
    
    pipeline_log_path = os.path.join(settings.log_dir, f"pipeline_{execution_id}.log")
    
    # Check if handler already exists to prevent duplicate handlers
    for handler in root_logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == os.path.abspath(pipeline_log_path):
            return handler

    pipeline_handler = logging.FileHandler(pipeline_log_path, encoding='utf-8')
    pipeline_handler.setLevel(logging.DEBUG)
    pipeline_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    pipeline_handler.setFormatter(pipeline_formatter)
    root_logger.addHandler(pipeline_handler)
    
    logging.getLogger("platform").debug(f"Pipeline logger for execution {execution_id} attached.")
    return pipeline_handler

def remove_pipeline_logger(handler):
    """Removes a specific pipeline execution handler."""
    root_logger = logging.getLogger()
    root_logger.removeHandler(handler)
    handler.close()
    logging.getLogger("platform").debug("Pipeline logger detached.")
