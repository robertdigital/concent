from unittest import TestCase

import mock
from django.test import override_settings

from golem_messages.factories.tasks import TaskToComputeFactory
from golem_messages.message.tasks import ReportComputedTask
from golem_messages.message.tasks import SubtaskResultsRejected
from golem_messages.message.tasks import TaskToCompute
from golem_messages.utils import encode_hex
from common.constants import ErrorCode
from common.testing_helpers import generate_ecc_key_pair

from core.constants import ETHEREUM_ADDRESS_LENGTH
from core.constants import MESSAGE_TASK_ID_MAX_LENGTH
from core.message_handlers import are_keys_and_addresses_unique_in_message_subtask_results_accepted
from core.tests.utils import ConcentIntegrationTestCase
from core.exceptions import FrameNumberValidationError
from core.exceptions import HashingAlgorithmError
from core.exceptions import Http400
from core.validation import validate_all_messages_identical
from core.validation import validate_ethereum_addresses
from core.validation import validate_golem_message_subtask_results_rejected
from core.validation import validate_id_value
from core.validation import validate_frames
from core.validation import validate_secure_hash_algorithm
from core.validation import validate_subtask_price_task_to_compute


(CONCENT_PRIVATE_KEY, CONCENT_PUBLIC_KEY) = generate_ecc_key_pair()


def mocked_message_with_price(price):
    message_with_price = mock.create_autospec(spec=TaskToCompute, spec_set=True)
    message_with_price.price = price
    return message_with_price


class TestValidateGolemMessageSubtaskResultsRejected(TestCase):
    def test_that_exception_is_raised_when_subtask_results_rejected_is_of_wrong_type(self):
        with self.assertRaises(Http400):
            validate_golem_message_subtask_results_rejected(None)

    def test_that_exception_is_raised_when_subtask_results_rejected_contains_invalid_task_to_compute(self):
        report_computed_task = ReportComputedTask(task_to_compute=TaskToCompute())
        subtask_results_rejected = SubtaskResultsRejected(report_computed_task=report_computed_task)
        with self.assertRaises(Http400):
            validate_golem_message_subtask_results_rejected(subtask_results_rejected)


class ValidatorsTest(TestCase):
    def test_that_function_raises_http400_when_ethereum_addres_has_wrong_type(self):
        with self.assertRaises(Http400):
            validate_ethereum_addresses(int('1' * ETHEREUM_ADDRESS_LENGTH), 'a' * ETHEREUM_ADDRESS_LENGTH)

        with self.assertRaises(Http400):
            validate_ethereum_addresses('a' * ETHEREUM_ADDRESS_LENGTH, int('1' * ETHEREUM_ADDRESS_LENGTH))

        with self.assertRaises(Http400):
            validate_ethereum_addresses(int('1' * ETHEREUM_ADDRESS_LENGTH), int('1' * ETHEREUM_ADDRESS_LENGTH))

    def test_that_function_raises_http400_when_ethereum_addres_has_wrong_length(self):
        with self.assertRaises(Http400):
            validate_ethereum_addresses('a' * 5, 'b' * 5)

        with self.assertRaises(Http400):
            validate_ethereum_addresses('a' * 5, 'b' * ETHEREUM_ADDRESS_LENGTH)

        with self.assertRaises(Http400):
            validate_ethereum_addresses('a' * ETHEREUM_ADDRESS_LENGTH, 'b' * 5)

    def test_that_function_raise_http400_when_price_is_not_int(self):
        with self.assertRaises(Http400):
            validate_subtask_price_task_to_compute(mocked_message_with_price('5'))

    def test_that_function_raise_http400_when_price_is_negative(self):
        with self.assertRaises(Http400):
            validate_subtask_price_task_to_compute(mocked_message_with_price(-5))

    def test_that_function_will_not_return_anything_when_price_is_zero(self):  # pylint: disable=no-self-use
        validate_subtask_price_task_to_compute(mocked_message_with_price(0))


class TestValidateAllMessagesIdentical(ConcentIntegrationTestCase):

    def setUp(self):
        super().setUp()
        self.report_computed_task = self._get_deserialized_report_computed_task()

    def test_that_function_pass_when_in_list_is_one_item(self):

        try:
            validate_all_messages_identical([self.report_computed_task])
        except Exception:  # pylint: disable=broad-except
            self.fail()

    def test_that_function_pass_when_in_list_are_two_same_report_computed_task(self):
        try:
            validate_all_messages_identical([self.report_computed_task, self.report_computed_task])
        except Exception:  # pylint: disable=broad-except
            self.fail()

    def test_that_function_raise_http400_when_any_slot_will_be_different_in_messages(self):
        different_report_computed_task = self._get_deserialized_report_computed_task(size = 10)
        with self.assertRaises(Http400):
            validate_all_messages_identical([self.report_computed_task, different_report_computed_task])


class TestValidateIdValue(TestCase):

    def test_that_function_should_pass_when_value_is_allowed(self):
        correct_values = [
            'a',
            '0',
            'a0',
            '0a',
            'a-_b'
        ]

        for value in correct_values:
            try:
                validate_id_value(value, 'test')
            except Exception:  # pylint: disable=broad-except
                self.fail()

    def test_that_function_should_raise_exception_when_value_is_not_allowed(self):
        incorrect_values = [
            '*',
            'a()',
            '0@',
            'a+0',
            '0+a',
        ]

        for value in incorrect_values:
            try:
                validate_id_value(value, 'test')
            except Http400 as exception:
                self.assertEqual(exception.error_code, ErrorCode.MESSAGE_VALUE_NOT_ALLOWED)

    def test_that_function_should_raise_exception_when_value_is_not_a_string(self):
        incorrect_values = 1

        try:
            validate_id_value(incorrect_values, 'test')
        except Http400 as exception:
            self.assertEqual(exception.error_code, ErrorCode.MESSAGE_VALUE_WRONG_TYPE)

    def test_that_function_should_raise_exception_when_value_is_blank(self):
        incorrect_values = ''

        try:
            validate_id_value(incorrect_values, 'test')
        except Http400 as exception:
            self.assertEqual(exception.error_code, ErrorCode.MESSAGE_VALUE_BLANK)

    def test_that_function_should_raise_exception_when_value_is_too_long(self):
        incorrect_values = 'a' * (MESSAGE_TASK_ID_MAX_LENGTH + 1)

        try:
            validate_id_value(incorrect_values, 'test')
        except Http400 as exception:
            self.assertEqual(exception.error_code, ErrorCode.MESSAGE_VALUE_WRONG_LENGTH)


class TestInvalidHashAlgorithms(TestCase):

    def test_that_validation_should_raise_exception_when_checksum_is_invalid(self):
        invalid_values_with_expected_error_code = {
            123456789: ErrorCode.MESSAGE_FILES_CHECKSUM_WRONG_TYPE,
            '': ErrorCode.MESSAGE_FILES_CHECKSUM_EMPTY,
            'sha14452d71687b6bc2c9389c3349fdc17fbd73b833b': ErrorCode.MESSAGE_FILES_CHECKSUM_WRONG_FORMAT,
            'sha2:4452d71687b6bc2c9389c3349fdc17fbd73b833b': ErrorCode.MESSAGE_FILES_CHECKSUM_INVALID_ALGORITHM,
            'sha1:xyz2d71687b6bc2c9389c3349fdc17fbd73b833b': ErrorCode.MESSAGE_FILES_CHECKSUM_INVALID_SHA1_HASH,
            'sha1:': ErrorCode.MESSAGE_FILES_CHECKSUM_INVALID_SHA1_HASH,
        }
        for invalid_value, error_code in invalid_values_with_expected_error_code.items():
            with self.assertRaises(HashingAlgorithmError) as context:
                validate_secure_hash_algorithm(invalid_value)
            self.assertEqual(context.exception.error_code, error_code)


@override_settings(CONCENT_PUBLIC_KEY=CONCENT_PUBLIC_KEY)
class TestAreEthereumAddressesAndKeysUnique(ConcentIntegrationTestCase):

    def setUp(self):
        super().setUp()

        self.task_to_compute_1 = TaskToComputeFactory(
            requestor_ethereum_address=encode_hex(self.REQUESTOR_PUBLIC_KEY),
            requestor_ethereum_public_key=encode_hex(self.REQUESTOR_PUB_ETH_KEY),
            requestor_public_key=encode_hex(self.REQUESTOR_PUBLIC_KEY),
        )
        self.task_to_compute_2 = TaskToComputeFactory(
            requestor_ethereum_address=encode_hex(self.REQUESTOR_PUBLIC_KEY),
            requestor_ethereum_public_key=encode_hex(self.REQUESTOR_PUB_ETH_KEY),
            requestor_public_key=encode_hex(self.REQUESTOR_PUBLIC_KEY),
        )

    def create_subtask_results_accepted_list(self, task_to_compute_1, task_to_compute_2) -> list:
        subtask_results_accepted_list = [
            self._get_deserialized_subtask_results_accepted(
                payment_ts="2018-02-05 12:00:16",
                task_to_compute=task_to_compute_1
            ),
            self._get_deserialized_subtask_results_accepted(
                payment_ts="2018-02-05 12:00:16",
                task_to_compute=task_to_compute_2
            ),
        ]
        return subtask_results_accepted_list

    def test_that_if_the_same_values_given_method_should_return_true(self):
        subtask_results_accepted_list = self.create_subtask_results_accepted_list(self.task_to_compute_1, self.task_to_compute_2)
        result = are_keys_and_addresses_unique_in_message_subtask_results_accepted(subtask_results_accepted_list)
        self.assertTrue(result)

    def test_that_if_different_ethereum_addresses_are_given_method_should_return_false(self):
        self.task_to_compute_2 = mock.Mock(spec_set=TaskToCompute)
        self.task_to_compute_2.requestor_ethereum_address = encode_hex(self.DIFFERENT_REQUESTOR_PUBLIC_KEY)
        self.task_to_compute_2.requestor_ethereum_public_key = encode_hex(self.REQUESTOR_PUB_ETH_KEY)
        self.task_to_compute_2.requestor_public_key = encode_hex(self.REQUESTOR_PUBLIC_KEY)
        subtask_results_accepted_list = [self.task_to_compute_1, self.task_to_compute_2]
        result = are_keys_and_addresses_unique_in_message_subtask_results_accepted(subtask_results_accepted_list)
        self.assertFalse(result)

    def test_that_if_different_ethereum_public_keys_are_given_method_should_return_false(self):
        self.task_to_compute_2.requestor_ethereum_public_key = encode_hex(self.DIFFERENT_REQUESTOR_PUB_ETH_KEY)
        subtask_results_accepted_list = self.create_subtask_results_accepted_list(self.task_to_compute_1, self.task_to_compute_2)
        result = are_keys_and_addresses_unique_in_message_subtask_results_accepted(subtask_results_accepted_list)
        self.assertFalse(result)

    def test_that_if_different_public_keys_are_given_method_should_return_false(self):
        self.task_to_compute_2.requestor_public_key = encode_hex(self.DIFFERENT_REQUESTOR_PUBLIC_KEY)
        subtask_results_accepted_list = self.create_subtask_results_accepted_list(self.task_to_compute_1, self.task_to_compute_2)
        result = are_keys_and_addresses_unique_in_message_subtask_results_accepted(subtask_results_accepted_list)
        self.assertFalse(result)


class TestFramesListValidaation(TestCase):

    def test_that_list_of_ints_is_valid(self):
        try:
            validate_frames([1, 2])
        except Exception:  # pylint: disable=broad-except
            self.fail()

    def test_that_if_frames_is_not_a_list_of_ints_method_should_raise_exception(self):
        with self.assertRaises(FrameNumberValidationError):
            validate_frames({'1': 1})

        with self.assertRaises(FrameNumberValidationError):
            validate_frames((1, 2))

    def test_that_if_frames_are_not_grater_than_0_method_should_raise_exception(self):
        with self.assertRaises(FrameNumberValidationError):
            validate_frames([-1, 1])

        with self.assertRaises(FrameNumberValidationError):
            validate_frames([0, 1])

    def test_that_if_frames_are_not_one_after_the_other_method_should_pass(self):
        try:
            validate_frames([1, 3, 5])
        except Exception:  # pylint: disable=broad-except
            self.fail()

    def test_that_if_frames_are_not_integers_method_should_raise_exception(self):
        with self.assertRaises(FrameNumberValidationError):
            validate_frames(['1', '2'])
