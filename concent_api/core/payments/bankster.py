from typing import List
from typing import Optional
from typing import Tuple
from typing import Union
import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce

from golem_messages.message.tasks import SubtaskResultsAccepted
from golem_sci.events import BatchTransferEvent
from golem_sci.events import ForcedPaymentEvent

from common.constants import ConcentUseCase
from common.helpers import deserialize_message
from common.helpers import ethereum_public_key_to_address
from common.helpers import parse_timestamp_to_utc_datetime
from common.logging import log
from core.constants import ETHEREUM_ADDRESS_LENGTH
from core.exceptions import TooSmallProviderDeposit
from core.models import Client
from core.models import DepositAccount
from core.models import DepositClaim
from core.models import Subtask
from core.payments import service
from core.payments.backends.sci_backend import TransactionType
from core.subtask_helpers import get_or_create_safely
from core.utils import adjust_transaction_hash
from core.validation import validate_bytes_public_key
from core.validation import validate_list_of_transaction_timestamp
from core.validation import validate_uuid


logger = logging.getLogger(__name__)


def claim_deposit(
    subtask_id: str,
    concent_use_case: ConcentUseCase,
    requestor_ethereum_address: str,
    provider_ethereum_address: str,
    subtask_cost: int,
    requestor_public_key: bytes,
    provider_public_key: bytes,
) -> Tuple[Optional[DepositClaim], Optional[DepositClaim]]:
    """
    The purpose of this operation is to check whether the clients participating in a use case have enough funds in their
    deposits to cover all the costs associated with the use case in the pessimistic scenario.
    """

    assert isinstance(concent_use_case, ConcentUseCase)
    assert isinstance(requestor_ethereum_address, str)
    assert isinstance(provider_ethereum_address, str)
    assert isinstance(subtask_cost, int) and subtask_cost > 0

    assert concent_use_case in [ConcentUseCase.FORCED_ACCEPTANCE, ConcentUseCase.ADDITIONAL_VERIFICATION]
    assert len(requestor_ethereum_address) == ETHEREUM_ADDRESS_LENGTH
    assert len(provider_ethereum_address) == ETHEREUM_ADDRESS_LENGTH
    assert provider_ethereum_address != requestor_ethereum_address

    validate_bytes_public_key(requestor_public_key, 'requestor_public_key')
    validate_bytes_public_key(provider_public_key, 'provider_public_key')
    validate_uuid(subtask_id)

    is_claim_against_provider: bool = (
        concent_use_case == ConcentUseCase.ADDITIONAL_VERIFICATION and
        settings.ADDITIONAL_VERIFICATION_COST > 0
    )

    # Bankster creates Client and DepositAccount objects (if they don't exist yet) for the requestor
    # and also for the provider if there's a non-zero claim against his account.
    # This is done in single database transaction.
    with transaction.atomic(using='control'):
        requestor_client: Client = get_or_create_safely(Client, public_key=requestor_public_key)

        requestor_deposit_account: DepositAccount = get_or_create_safely(
            DepositAccount,
            client=requestor_client,
            ethereum_address=requestor_ethereum_address,
        )

        if is_claim_against_provider:
            provider_client: Client = get_or_create_safely(Client, public_key=provider_public_key)

            provider_deposit_account: DepositAccount = get_or_create_safely(
                DepositAccount,
                client=provider_client,
                ethereum_address=provider_ethereum_address,
            )

    # Bankster asks SCI about the amount of funds available in requestor's deposit.
    requestor_deposit = service.get_deposit_value(client_eth_address=requestor_ethereum_address)  # pylint: disable=no-value-for-parameter

    # If the amount claimed from provider's deposit is non-zero,
    # Bankster asks SCI about the amount of funds available in his deposit.
    if is_claim_against_provider:
        provider_deposit = service.get_deposit_value(client_eth_address=provider_ethereum_address)  # pylint: disable=no-value-for-parameter

    # Bankster puts database locks on DepositAccount objects
    # that will be used as payers in newly created DepositClaims.
    with transaction.atomic(using='control'):
        # Bankster sums the amounts of all existing DepositClaims that have the same payer as the one being processed.
        aggregated_claims_against_requestor = DepositClaim.objects.filter(
            payer_deposit_account=requestor_deposit_account
        ).aggregate(
            sum_of_existing_claims=Coalesce(Sum('amount'), 0)
        )

        # If the existing claims against requestor's deposit are greater or equal to his current deposit,
        # we can't add a new claim.
        if requestor_deposit <= aggregated_claims_against_requestor['sum_of_existing_claims']:
            return (None, None)

        # Deposit lock for requestor.
        claim_against_requestor = DepositClaim(
            subtask_id=subtask_id,
            payee_ethereum_address=provider_ethereum_address,
            amount=subtask_cost,
            concent_use_case=concent_use_case,
            payer_deposit_account=requestor_deposit_account,
        )
        claim_against_requestor.full_clean()
        claim_against_requestor.save()

        if is_claim_against_provider:
            # Bankster sums the amounts of all existing DepositClaims where the provider is the payer.
            aggregated_claims_against_provider = DepositClaim.objects.filter(
                payer_deposit_account=provider_deposit_account
            ).aggregate(
                sum_of_existing_claims=Coalesce(Sum('amount'), 0)
            )

            # If the total of existing claims and the current claim is greater or equal to the current deposit,
            # we can't add a new claim.
            if provider_deposit <= aggregated_claims_against_provider['sum_of_existing_claims'] + settings.ADDITIONAL_VERIFICATION_COST:
                claim_against_requestor.delete()
                raise TooSmallProviderDeposit

        # Deposit lock for provider.
        if is_claim_against_provider:
            claim_against_provider = DepositClaim(
                subtask_id=subtask_id,
                payee_ethereum_address=ethereum_public_key_to_address(
                    settings.CONCENT_ETHEREUM_PUBLIC_KEY
                ),
                amount=settings.ADDITIONAL_VERIFICATION_COST,
                concent_use_case=concent_use_case,
                payer_deposit_account=provider_deposit_account,
            )
            claim_against_provider.full_clean()
            claim_against_provider.save()
        else:
            claim_against_provider = None  # type: ignore

    return (claim_against_requestor, claim_against_provider)


def finalize_payment(deposit_claim: DepositClaim) -> Optional[str]:
    """
    This operation tells Bankster to pay out funds from deposit.
    For each claim, Bankster uses SCI to submit an Ethereum transaction to the Ethereum client which then propagates it
    to the rest of the network.
    Hopefully the transaction is included in one of the upcoming blocks on the blockchain.
    """

    assert isinstance(deposit_claim, DepositClaim)
    assert deposit_claim.tx_hash is None

    # Bankster asks SCI about the amount of funds available on the deposit account listed in the DepositClaim.
    available_funds = service.get_deposit_value(  # pylint: disable=no-value-for-parameter
        client_eth_address=deposit_claim.payer_deposit_account.ethereum_address
    )

    # Bankster begins a database transaction and puts a database lock on the DepositAccount object.
    with transaction.atomic(using='control'):
        DepositAccount.objects.select_for_update().get(
            pk=deposit_claim.payer_deposit_account_id
        )

        # Bankster sums the amounts of all existing DepositClaims that have the same payer as the one being processed.
        aggregated_client_claims = DepositClaim.objects.filter(
            payer_deposit_account=deposit_claim.payer_deposit_account
        ).exclude(
            pk=deposit_claim.pk
        ).aggregate(
            sum_of_existing_claims=Coalesce(Sum('amount'), 0)
        )

        # Bankster subtracts that value from the amount of funds available in the deposit.
        available_funds_without_claims = available_funds - aggregated_client_claims['sum_of_existing_claims']

        # If the result is negative or zero, Bankster removes the DepositClaim object being processed.
        if available_funds_without_claims <= 0:
            deposit_claim.delete()
            return None

        # Otherwise if the result is lower than DepositAccount.amount,
        # Bankster sets this field to the amount that's actually available.
        elif available_funds_without_claims < deposit_claim.amount:
            deposit_claim.amount = available_funds_without_claims

        # If the DepositClaim still exists at this point, Bankster uses SCI to create an Ethereum transaction.
        if deposit_claim.concent_use_case == ConcentUseCase.FORCED_ACCEPTANCE:
            ethereum_transaction_hash = service.force_subtask_payment(  # pylint: disable=no-value-for-parameter
                requestor_eth_address=deposit_claim.payer_deposit_account.ethereum_address,
                provider_eth_address=deposit_claim.payee_ethereum_address,
                value=deposit_claim.amount,
                subtask_id=deposit_claim.subtask_id,
            )
        elif deposit_claim.concent_use_case == ConcentUseCase.ADDITIONAL_VERIFICATION:
            subtask = Subtask.objects.filter(subtask_id=deposit_claim.subtask_id).first()  # pylint: disable=no-member
            if subtask is not None:
                task_to_compute = deserialize_message(subtask.task_to_compute.data.tobytes())
                if task_to_compute.requestor_ethereum_address == deposit_claim.payer_deposit_account.ethereum_address:
                    ethereum_transaction_hash = service.force_subtask_payment(  # pylint: disable=no-value-for-parameter
                        requestor_eth_address=deposit_claim.payer_deposit_account.ethereum_address,
                        provider_eth_address=deposit_claim.payee_ethereum_address,
                        value=deposit_claim.amount,
                        subtask_id=deposit_claim.subtask_id,
                    )
                elif task_to_compute.provider_ethereum_address == deposit_claim.payer_deposit_account.ethereum_address:
                    ethereum_transaction_hash = service.cover_additional_verification_cost(  # pylint: disable=no-value-for-parameter
                        provider_eth_address=deposit_claim.payer_deposit_account.ethereum_address,
                        value=deposit_claim.amount,
                        subtask_id=deposit_claim.subtask_id,
                    )
                else:
                    assert False
        else:
            assert False

        # Bankster puts transaction ID in DepositClaim.tx_hash.
        ethereum_transaction_hash = adjust_transaction_hash(ethereum_transaction_hash)
        deposit_claim.tx_hash = ethereum_transaction_hash
        deposit_claim.full_clean()
        deposit_claim.save()

        service.call_on_confirmed_transaction(  # pylint: disable=no-value-for-parameter
            tx_hash=deposit_claim.tx_hash,
            callback=lambda _: discard_claim(deposit_claim),
        )

    return deposit_claim.tx_hash


def settle_overdue_acceptances(
    requestor_ethereum_address: str,
    provider_ethereum_address: str,
    acceptances: List[SubtaskResultsAccepted],
    requestor_public_key: bytes,
) -> Optional[DepositClaim]:
    """
    The purpose of this operation is to calculate the total amount that the requestor owes provider for completed
    computations and transfer that amount from requestor's deposit.
    The caller is responsible for making sure that the payment is legitimate and should be performed.
    Bankster simply calculates the amount and executes it.
    """

    assert isinstance(requestor_ethereum_address, str)
    assert isinstance(provider_ethereum_address, str)
    assert all([isinstance(acceptance, SubtaskResultsAccepted) for acceptance in acceptances])

    assert len(requestor_ethereum_address) == ETHEREUM_ADDRESS_LENGTH
    assert len(provider_ethereum_address) == ETHEREUM_ADDRESS_LENGTH
    assert provider_ethereum_address != requestor_ethereum_address

    with transaction.atomic(using='control'):
        requestor_client: Client = get_or_create_safely(Client, public_key=requestor_public_key)

    with transaction.atomic(using='control'):
        requestor_deposit_account: DepositAccount = get_or_create_safely(
            DepositAccount,
            client=requestor_client,
            ethereum_address=requestor_ethereum_address
        )

    # Bankster asks SCI about the amount of funds available in requestor's deposit.
    requestor_deposit_value = service.get_deposit_value(client_eth_address=requestor_ethereum_address)  # pylint: disable=no-value-for-parameter

    # Bankster begins a database transaction and puts a database lock on the DepositAccount object.
    with transaction.atomic(using='control'):
        DepositAccount.objects.select_for_update().get(
            pk=requestor_deposit_account.pk
        )

        # Bankster sums the amounts of all existing DepositClaims that have the same payer as the one being processed.
        sum_of_existing_requestor_claims = DepositClaim.objects.filter(
            payer_deposit_account=requestor_deposit_account
        ).aggregate(
            sum_of_existing_claims=Coalesce(Sum('amount'), 0)
        )

        assert sum_of_existing_requestor_claims['sum_of_existing_claims'] >= 0

        # If the existing claims against requestor's deposit are greater or equal to his current deposit,
        # we can't add a new claim.
        if requestor_deposit_value <= sum_of_existing_requestor_claims['sum_of_existing_claims']:
            return None

        # Concent defines time T0 equal to oldest payment_ts from passed SubtaskResultAccepted messages from
        # subtask_results_accepted_list.
        oldest_payments_ts = min(subtask_results_accepted.payment_ts for subtask_results_accepted in acceptances)

        # Concent gets list of transactions from payment API where timestamp >= T0.
        list_of_transactions = service.get_list_of_payments(  # pylint: disable=no-value-for-parameter
            requestor_eth_address=requestor_ethereum_address,
            provider_eth_address=provider_ethereum_address,
            payment_ts=oldest_payments_ts,
            transaction_type=TransactionType.BATCH,
        )

        validate_list_of_transaction_timestamp(list_of_transactions, acceptances)

        # Concent gets list of forced payments from payment API where T0 <= payment_ts + PAYMENT_DUE_TIME.
        list_of_forced_payments = service.get_list_of_payments(  # pylint: disable=no-value-for-parameter
            requestor_eth_address=requestor_ethereum_address,
            provider_eth_address=provider_ethereum_address,
            payment_ts=oldest_payments_ts,
            transaction_type=TransactionType.FORCE,
        )

        (_amount_paid, amount_pending) = get_provider_payment_info(
            list_of_forced_payments=list_of_forced_payments,
            list_of_payments=list_of_transactions,
            subtask_results_accepted_list=acceptances,
        )

        # Bankster compares the amount with the available deposit minus the existing claims against requestor's account.
        # If the whole amount can't be paid, Concent lowers it to pay as much as possible.
        requestor_payable_amount = min(
            amount_pending,
            requestor_deposit_value - sum_of_existing_requestor_claims['sum_of_existing_claims'],
        )

        logger.info(
            f'requestor_payable_amount is {requestor_payable_amount} for ethereum address {requestor_ethereum_address}.'
        )

        if requestor_payable_amount <= 0:
            return None

        # This is time T2 (end time) equal to youngest payment_ts from passed SubtaskResultAccepted messages from
        # subtask_results_accepted_list.
        youngest_payment_ts = max(subtask_results_accepted.payment_ts for subtask_results_accepted in acceptances)

        transaction_hash = service.make_force_payment_to_provider(  # pylint: disable=no-value-for-parameter
            requestor_eth_address=requestor_ethereum_address,
            provider_eth_address=provider_ethereum_address,
            value=requestor_payable_amount,
            payment_ts=youngest_payment_ts,
        )
        transaction_hash = adjust_transaction_hash(transaction_hash)

        # Deposit lock for requestor.
        claim_against_requestor = DepositClaim(
            payee_ethereum_address=provider_ethereum_address,
            payer_deposit_account=requestor_deposit_account,
            amount=requestor_payable_amount,
            concent_use_case=ConcentUseCase.FORCED_PAYMENT,
            tx_hash=transaction_hash,
            closure_time=parse_timestamp_to_utc_datetime(youngest_payment_ts),
        )
        claim_against_requestor.full_clean()
        claim_against_requestor.save()

    return claim_against_requestor


def sum_payments(payments: List[Union[ForcedPaymentEvent, BatchTransferEvent]]) -> int:
    assert isinstance(payments, list)

    return sum([item.amount for item in payments])


def sum_subtask_price(subtask_results_accepted_list: List[SubtaskResultsAccepted]) -> int:
    assert isinstance(subtask_results_accepted_list, list)

    return sum(
        [subtask_results_accepted.task_to_compute.price for subtask_results_accepted in subtask_results_accepted_list]
    )


def get_provider_payment_info(
    list_of_forced_payments: List[ForcedPaymentEvent],
    list_of_payments: List[BatchTransferEvent],
    subtask_results_accepted_list: List[SubtaskResultsAccepted],
) -> Tuple[int, int]:
    assert isinstance(list_of_payments, list)
    assert isinstance(list_of_forced_payments, list)
    assert isinstance(subtask_results_accepted_list, list)

    force_payments_price = sum_payments(list_of_forced_payments)
    payments_price = sum_payments(list_of_payments)
    subtasks_price = sum_subtask_price(subtask_results_accepted_list)

    amount_paid = payments_price + force_payments_price
    amount_pending = subtasks_price - amount_paid

    return (amount_paid, amount_pending)


def discard_claim(deposit_claim: DepositClaim) -> bool:
    """ This operation tells Bankster to discard the claim. Claim is simply removed, freeing the funds. """

    assert isinstance(deposit_claim, DepositClaim)

    with transaction.atomic(using='control'):
        try:
            DepositAccount.objects.select_for_update().get(
                pk=deposit_claim.payer_deposit_account_id
            )
        except DepositAccount.DoesNotExist:
            assert False

        if deposit_claim.tx_hash is None:
            claim_removed = False
        else:
            deposit_claim.delete()
            claim_removed = True

    log(
        logger,
        f'After removing DepositClaim. Was DepositClaim removed: {claim_removed}. Tx_hash: {deposit_claim.tx_hash}',
        subtask_id=deposit_claim.subtask_id,
    )
    return claim_removed
