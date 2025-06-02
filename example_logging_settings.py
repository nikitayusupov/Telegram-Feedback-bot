"""
Пример настроек логирования для разных окружений.

Скопируйте этот файл в logging_settings.py и настройте под свои нужды.
"""

import logging
from utils.logging_config import setup_logging

def setup_production_logging():
    """Настройки для продакшена - только INFO и выше, только в файл"""
    return setup_logging(
        log_level=logging.INFO,
        log_dir="logs",
        console_output=False  # В продакшене логи только в файл
    )

def setup_development_logging():
    """Настройки для разработки - все уровни включая DEBUG, в файл и консоль"""
    return setup_logging(
        log_level=logging.DEBUG,
        log_dir="logs_dev",
        console_output=True  # В разработке удобно видеть логи в консоли
    )

def setup_testing_logging():
    """Настройки для тестирования - только WARNING и выше, только в файл"""
    return setup_logging(
        log_level=logging.WARNING,
        log_dir="logs_test",
        console_output=False  # В тестах логи только в файл
    )

# Примеры использования:
# 
# В bot.py можно заменить:
# logger = setup_logging(log_level=logging.INFO, log_dir="logs")
# 
# На:
# from logging_settings import setup_production_logging
# logger = setup_production_logging()
#
# Или использовать переменную окружения:
# import os
# env = os.getenv('ENVIRONMENT', 'production')
# if env == 'development':
#     logger = setup_development_logging()
# elif env == 'testing':
#     logger = setup_testing_logging()
# else:
#     logger = setup_production_logging() 