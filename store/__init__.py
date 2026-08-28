"""Единственная точка доступа к SQLite: схема, миграции, репозитории по сущности."""
from .db import connect, create_schema
from .errors import DuplicateChatError, DuplicateHitError
from .repositories.delivery_queue import DeliveryQueueRepo, DeliveryTask
from .repositories.hits import Hit, HitsRepo
from .repositories.keywords import Keyword, KeywordsRepo
from .repositories.receivers import Receiver, ReceiversRepo
from .repositories.settings import SettingsRepo
from .repositories.sources import Source, SourcesRepo

__all__ = [
    "connect",
    "create_schema",
    "DuplicateHitError",
    "DuplicateChatError",
    "Hit",
    "HitsRepo",
    "Keyword",
    "KeywordsRepo",
    "Source",
    "SourcesRepo",
    "Receiver",
    "ReceiversRepo",
    "SettingsRepo",
    "DeliveryTask",
    "DeliveryQueueRepo",
]
