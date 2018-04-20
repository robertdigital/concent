import datetime
from django.test import override_settings
from django.test import TestCase
import mock
from freezegun import freeze_time

from golem_messages.factories.tasks import ReportComputedTaskFactory
from golem_messages.message import FileTransferToken

from concent_api import settings
from core.tests.utils import ConcentIntegrationTestCase
from core.exceptions import UnexpectedResponse
from core.transfer_operations import create_file_transfer_token_for_concent
from core.transfer_operations import create_file_transfer_token_for_golem_client
from core.transfer_operations import request_upload_status
from utils.helpers import parse_datetime_to_timestamp

from utils.testing_helpers import generate_ecc_key_pair


def mock_send_request_to_cluster_correct_response(_headers, _request_http):
    mocked_object = mock.Mock()
    mocked_object.status_code = 200
    return mocked_object


def mock_send_incorrect_request_to_cluster_incorrect_response(_headers, _request_http):
    mocked_object = mock.Mock()
    mocked_object.status_code = 404
    return mocked_object


def mock_send_incorrect_request_to_cluster_unexpected_response(_headers, _request_http):
    mocked_object = mock.Mock()
    mocked_object.status_code = 500
    return mocked_object


(CONCENT_PRIVATE_KEY, CONCENT_PUBLIC_KEY)       = generate_ecc_key_pair()


@override_settings(
    CONCENT_PRIVATE_KEY       = CONCENT_PRIVATE_KEY,
    CONCENT_PUBLIC_KEY        = CONCENT_PUBLIC_KEY,
)
class RequestUploadStatusTest(ConcentIntegrationTestCase):
    def test_properly_work_of_request_upload_status_function(self):

        report_computed_task = self._get_deserialized_report_computed_task(
            subtask_id = '1',
            task_to_compute = self._get_deserialized_task_to_compute(
                task_id                         = '1/1',
                subtask_id                      = '1',
            )
        )

        with mock.patch('core.transfer_operations.send_request_to_cluster_storage', mock_send_request_to_cluster_correct_response):
            cluster_response = request_upload_status(report_computed_task)

        self.assertEqual(cluster_response, True)

        with mock.patch('core.transfer_operations.send_request_to_cluster_storage', mock_send_incorrect_request_to_cluster_incorrect_response):
            cluster_response_2 = request_upload_status(report_computed_task)

        self.assertEqual(cluster_response_2, False)

        with self.assertRaises(UnexpectedResponse):
            with mock.patch('core.transfer_operations.send_request_to_cluster_storage', mock_send_incorrect_request_to_cluster_unexpected_response):
                request_upload_status(report_computed_task)


@override_settings(
    CONCENT_PRIVATE_KEY=CONCENT_PRIVATE_KEY,
    CONCENT_PUBLIC_KEY=CONCENT_PUBLIC_KEY,
    MAXIMUM_DOWNLOAD_TIME=1800,
    CONCENT_MESSAGING_TIME=20,
    SUBTASK_VERIFICATION_TIME=50,
)
class FileTransferTokenCreationTest(TestCase):
    def setUp(self):
        self.time = datetime.datetime.strptime("2017-11-17 10:00:00", "%Y-%m-%d %H:%M:%S")
        self.deadline = 10
        self.authorized_client_public_key = b'7' * 64
        self.report_computed_task = ReportComputedTaskFactory(
            task_to_compute__compute_task_def__deadline=parse_datetime_to_timestamp(self.time) + self.deadline
        )

    def test_that_file_transfer_token_for_concent_is_never_out_of_date(self):
        report_computed_task = ReportComputedTaskFactory()

        token = create_file_transfer_token_for_concent(
            report_computed_task,
            self.authorized_client_public_key,
            FileTransferToken.Operation.download
        )
        self.assertTrue(token.timestamp < token.token_expiration_deadline)

    def test_that_download_file_transfer_token_for_golem_client_is_can_be_out_of_date(self):
        with freeze_time(self.time):
            new_time = self._get_deadline_exceeded_time_for_download_token()
            with freeze_time(new_time):
                download_token = create_file_transfer_token_for_golem_client(
                    self.report_computed_task,
                    self.authorized_client_public_key,
                    FileTransferToken.Operation.download
                )
                self.assertTrue(download_token.timestamp > download_token.token_expiration_deadline)

    def test_that_upload_file_transfer_token_for_golem_client_is_can_be_out_of_date(self):
        with freeze_time(self.time):
            new_time = self._get_deadline_exceeded_time_for_upload_token()
            with freeze_time(new_time):
                upload_token = create_file_transfer_token_for_golem_client(
                    self.report_computed_task,
                    self.authorized_client_public_key,
                    FileTransferToken.Operation.download
                )
                self.assertTrue(upload_token.timestamp > upload_token.token_expiration_deadline)

    def _get_deadline_exceeded_time_for_upload_token(self):
        return self.time + datetime.timedelta(
            seconds=settings.SUBTASK_VERIFICATION_TIME + self.deadline + 1
        )

    def _get_deadline_exceeded_time_for_download_token(self):
        return self.time + datetime.timedelta(
            seconds=3 * settings.CONCENT_MESSAGING_TIME + 2 * settings.MAXIMUM_DOWNLOAD_TIME + self.deadline + 1
        )