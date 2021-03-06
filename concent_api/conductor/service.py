import logging
from typing import Any
from typing import Dict
from typing import List

from django.db import transaction
from django.db.models import Q

from common.constants import ErrorCode
from common.helpers import parse_timestamp_to_utc_datetime
from common.logging import log
from common.logging import LoggingLevel
from conductor.exceptions import VerificationRequestAlreadyInitiatedError
from conductor.models import BlenderCropScriptParameters
from conductor.models import BlenderSubtaskDefinition
from conductor.models import Frame
from conductor.models import ResultTransferRequest
from conductor.models import UploadReport
from conductor.models import VerificationRequest
from core.tasks import result_upload_finished


logger = logging.getLogger(__name__)


def update_upload_report(file_path: str, result_transfer_request: ResultTransferRequest) -> None:
    """
    Updates UploadReport objects with `path` related to given file path
    and schedules `result_upload_finished` task if not scheduled before.

    """

    assert isinstance(file_path, str)
    assert isinstance(result_transfer_request, ResultTransferRequest)

    UploadReport.objects.select_for_update().filter(
        path=file_path
    ).update(
        result_transfer_request=result_transfer_request
    )

    if result_transfer_request.upload_finished is False:
        # If both ResultTransferRequest and BlenderVerificationRequest that refer to the same file exist,
        # and they both have upload_finished set to False, crash with an error. It should not happen.
        if VerificationRequest.objects.filter(
            Q(source_package_path=file_path) | Q(result_package_path=file_path),
            upload_finished=False,
        ).exists():
            log(
                logger,
                f'`update_upload_report` called but VerificationRequest with ID {result_transfer_request.subtask_id} is already initiated.',
                subtask_id=result_transfer_request.subtask_id,
                logging_level=LoggingLevel.ERROR,
            )
            raise VerificationRequestAlreadyInitiatedError(
                f'`update_upload_report` called but VerificationRequest with ID {result_transfer_request.subtask_id} is already initiated.',
                ErrorCode.CONDUCTOR_VERIFICATION_REQUEST_ALREADY_INITIATED
            )

        result_transfer_request.upload_finished = True
        result_transfer_request.full_clean()
        result_transfer_request.save()

        def call_result_upload_finished() -> None:
            result_upload_finished.delay(result_transfer_request.subtask_id)

        transaction.on_commit(
            call_result_upload_finished,
            using='storage',
        )


def store_verification_request_and_blender_subtask_definition(
    subtask_id: str,
    source_package_path: str,
    result_package_path: str,
    output_format: str,
    scene_file: str,
    verification_deadline: int,
    blender_parameters: Dict[str, Any],
) -> tuple:
    verification_request = VerificationRequest(
        subtask_id=subtask_id,
        source_package_path=source_package_path,
        result_package_path=result_package_path,
        verification_deadline=parse_timestamp_to_utc_datetime(verification_deadline),
    )
    verification_request.full_clean()
    verification_request.save()

    blender_crop_script_parameters = _store_blender_crop_script_parameters(blender_parameters)

    blender_subtask_definition = BlenderSubtaskDefinition(
        verification_request=verification_request,
        output_format=BlenderSubtaskDefinition.OutputFormat[output_format].name,
        scene_file=scene_file,
        blender_crop_script_parameters=blender_crop_script_parameters,
    )
    blender_subtask_definition.full_clean()
    blender_subtask_definition.save()

    return (verification_request, blender_subtask_definition)


def store_frames(
    blender_subtask_definition: BlenderSubtaskDefinition,
    frame_list: List[int],
) -> None:
    for frame in frame_list:
        store_frame = Frame(
            blender_subtask_definition=blender_subtask_definition,
            number=frame,
        )
        store_frame.full_clean()
        store_frame.save()


def filter_frames_by_blender_subtask_definition(blender_subtask_definition: BlenderSubtaskDefinition) -> list:
    return list(Frame.objects.filter(blender_subtask_definition=blender_subtask_definition).values_list('number', flat=True))


def _store_blender_crop_script_parameters(blender_parameters: Any) -> BlenderCropScriptParameters:
    """
    Create and save BlenderCropScriptParameters model in database.
    String conversion and splicing is done to avoid DecimalField exceptions on full_clean.
    [:11] is needed for DecimalField(max_digits=10, decimal_places=9).
    """

    blender_crop_script_parameters = BlenderCropScriptParameters(
        resolution_x=blender_parameters['resolution'][0],
        resolution_y=blender_parameters['resolution'][1],
        samples=blender_parameters['samples'],
        use_compositing=blender_parameters['use_compositing'],
        borders_x_min=(str(blender_parameters['borders_x'][0]))[:11],
        borders_x_max=(str(blender_parameters['borders_x'][1]))[:11],
        borders_y_min=(str(blender_parameters['borders_y'][0]))[:11],
        borders_y_max=(str(blender_parameters['borders_y'][1]))[:11],
    )
    blender_crop_script_parameters.full_clean()
    blender_crop_script_parameters.save()

    return blender_crop_script_parameters
