from typing import Any
from typing import Callable
import importlib

from django.conf import settings

from core.payments.backends.sci_backend import TransactionType


def _add_backend(func: Callable) -> Callable:
    """
    Decorator which adds currently set payment backend to function call.
    :param func: Function from this module, that as a first param takes in backend's name.
    :return: decorated function
    """
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        backend = importlib.import_module(settings.PAYMENT_BACKEND)
        assert hasattr(backend, func.__name__)
        return func(backend, *args, **kwargs)
    return wrapper


@_add_backend
def get_list_of_payments(
    backend: Any,
    requestor_eth_address: str = None,
    provider_eth_address: str = None,
    payment_ts: int = None,
    current_time: int = None,
    transaction_type: TransactionType = None,
) -> list:
    return backend.get_list_of_payments(
        requestor_eth_address   = requestor_eth_address,
        provider_eth_address    = provider_eth_address,
        payment_ts              = payment_ts,
        current_time            = current_time,
        transaction_type        = transaction_type,
    )


@_add_backend
def make_force_payment_to_provider(
    backend: Any,
    requestor_eth_address: str = None,
    provider_eth_address: str = None,
    value: int = None,
    payment_ts: int = None,
) -> None:
    return backend.make_force_payment_to_provider(
        requestor_eth_address   = requestor_eth_address,
        provider_eth_address    = provider_eth_address,
        value                   = value,
        payment_ts              = payment_ts,
    )


@_add_backend
def is_account_status_positive(
    backend: Any,
    client_eth_address: str = None,
    pending_value: int = None,
) -> bool:
    return backend.is_account_status_positive(
        client_eth_address      = client_eth_address,
        pending_value           = pending_value,
    )


@_add_backend
def get_transaction_count(backend: Any) -> int:
    return backend.get_transaction_count()
