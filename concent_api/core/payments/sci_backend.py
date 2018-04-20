import math
from enum import Enum

from django.conf    import settings

from core.constants import ETHEREUM_ADDRESS_LENGTH
from utils.singleton import ConcentRPC


def get_list_of_payments(
    requestor_eth_address:  str,
    provider_eth_address:   str,
    payment_ts:             int,
    current_time:           int,
    transaction_type:       str,
):
    """
    Function which return list of transactions from payment API
    where timestamp >= T0
    """
    assert isinstance(requestor_eth_address,    str) and len(requestor_eth_address) == ETHEREUM_ADDRESS_LENGTH
    assert isinstance(provider_eth_address,     str) and len(provider_eth_address)  == ETHEREUM_ADDRESS_LENGTH
    assert isinstance(payment_ts,               int) and payment_ts     >= 0
    assert isinstance(current_time,             int) and current_time   > 0
    assert isinstance(transaction_type,         Enum) and transaction_type in TransactionType

    block_distance = math.ceil((current_time - payment_ts) / settings.AVERAGE_BLOCK_TIME)
    last_block_before_payment = ConcentRPC().get_block_number() - block_distance  # type: ignore  # pylint: disable=no-member

    if transaction_type == TransactionType.FORCE:
        payments_list = ConcentRPC().get_forced_payments(  # type: ignore  # pylint: disable=no-member
            requestor_address   = requestor_eth_address,
            provider_address    = provider_eth_address,
            from_block          = last_block_before_payment,
            to_block            = ConcentRPC().get_block_number(),  # type: ignore  # pylint: disable=no-member
        )
    elif transaction_type == TransactionType.BATCH:
        payments_list = ConcentRPC().get_batch_transfers(  # type: ignore  # pylint: disable=no-member
            payer_address   = requestor_eth_address,
            payee_address   = provider_eth_address,
            from_block      = last_block_before_payment,
            to_block        = ConcentRPC().get_block_number(),  # type: ignore  # pylint: disable=no-member
        )

    return payments_list


def make_force_payment_to_provider(
    requestor_eth_address:  str,
    provider_eth_address:   str,
    value:                  int,
    payment_ts:             int,
):
    """
    Concent makes transaction from requestor's deposit to provider's account on amount 'value'.
    If there is less then 'value' on requestor's deposit, Concent transfers as much as possible.
    """
    assert isinstance(requestor_eth_address,    str) and len(requestor_eth_address) == ETHEREUM_ADDRESS_LENGTH
    assert isinstance(provider_eth_address,     str) and len(provider_eth_address)  == ETHEREUM_ADDRESS_LENGTH
    assert isinstance(payment_ts,               int) and payment_ts   >= 0
    assert isinstance(value,                    int) and value        >= 0

    requestor_account_balance = ConcentRPC().get_deposit_value(requestor_eth_address)  # type: ignore  # pylint: disable=no-member
    if requestor_account_balance < value:
        value = requestor_account_balance

    ConcentRPC().force_payment(  # type: ignore  # pylint: disable=no-member
        requestor_address   = requestor_eth_address,
        provider_address    = provider_eth_address,
        value               = value,
        closure_time        = payment_ts,
    )


def is_account_status_positive(
    client_eth_address:     str,
    pending_value = 0,
):
    assert isinstance(client_eth_address,       str) and len(client_eth_address) == ETHEREUM_ADDRESS_LENGTH
    assert isinstance(pending_value,            int) and pending_value >= 0

    client_acc_balance = ConcentRPC().get_deposit_value(client_eth_address)  # type: ignore  # pylint: disable=no-member

    return client_acc_balance > pending_value


class TransactionType(Enum):
    BATCH = 'batch'
    FORCE = 'force'