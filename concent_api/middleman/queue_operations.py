from asyncio import IncompleteReadError
from asyncio import Queue
from asyncio import StreamReader
from asyncio import StreamWriter
from collections import OrderedDict  # noqa, pylint: disable=unused-import
from logging import getLogger
from logging import Logger
from typing import Dict

from django.conf import settings

from common.helpers import get_current_utc_timestamp
from middleman.constants import CONNECTION_COUNTER_LIMIT
from middleman.constants import MessageTrackerItem
from middleman.constants import RequestQueueItem
from middleman.constants import ResponseQueueItem
from middleman.utils import QueuePool
from middleman_protocol.constants import PayloadType
from middleman_protocol.constants import REQUEST_ID_FOR_RESPONSE_FOR_INVALID_FRAME
from middleman_protocol.exceptions import BrokenEscapingInFrameMiddlemanProtocolError
from middleman_protocol.exceptions import FrameInvalidMiddlemanProtocolError
from middleman_protocol.exceptions import MiddlemanProtocolError
from middleman_protocol.exceptions import PayloadTypeInvalidMiddlemanProtocolError
from middleman_protocol.exceptions import SignatureInvalidMiddlemanProtocolError
from middleman_protocol.message import ErrorFrame
from middleman_protocol.message import GolemMessageFrame
from middleman_protocol.registry import create_middleman_protocol_message
from middleman_protocol.stream_async import handle_frame_receive_async
from middleman_protocol.stream_async import map_exception_to_error_code
from middleman_protocol.stream_async import send_over_stream_async

ERRORS_THAT_CAUSE_CURRENT_ITERATION_ENDS = (
    FrameInvalidMiddlemanProtocolError,
    BrokenEscapingInFrameMiddlemanProtocolError,
    SignatureInvalidMiddlemanProtocolError,
    PayloadTypeInvalidMiddlemanProtocolError
)

logger = getLogger()


async def request_producer(
    request_queue: Queue,
    response_queue: Queue,
    reader: StreamReader,
    connection_id: int,
) -> None:
    while True:
        try:
            frame = await handle_frame_receive_async(reader, settings.CONCENT_PUBLIC_KEY)
        except IncompleteReadError:
            logger.info(f"Client has closed the connection: {connection_id}. Ending 'request_producer' coroutine")
            break
        except ERRORS_THAT_CAUSE_CURRENT_ITERATION_ENDS as exception:
            logger.info("Received invalid message")
            await response_queue.put(
                ResponseQueueItem(
                    create_error_frame(exception),
                    REQUEST_ID_FOR_RESPONSE_FOR_INVALID_FRAME,
                    get_current_utc_timestamp()
                )
            )
            continue

        logger.info(f"Received message from Concent: request ID = {frame.request_id}, connection ID = {connection_id}")
        item = RequestQueueItem(
            connection_id,
            frame.request_id,
            frame.payload,
            get_current_utc_timestamp()
        )
        await request_queue.put(item)


async def request_consumer(
    request_queue: Queue,
    response_queue_pool: QueuePool,
    message_tracker: Dict[int, MessageTrackerItem],
    signing_service_writer: StreamWriter
) -> None:
    message_tracker = message_tracker  # type: OrderedDict[int, MessageTrackerItem]
    signing_service_request_id = 0
    while True:
        item: RequestQueueItem = await request_queue.get()
        assert isinstance(item, RequestQueueItem)
        if item.connection_id not in response_queue_pool:
            logger.info(f"No matching queue for connection id: {item.connection_id}")
            request_queue.task_done()
            continue

        signing_service_request_id = (signing_service_request_id + 1) % CONNECTION_COUNTER_LIMIT
        message_tracker[signing_service_request_id] = MessageTrackerItem(
            item.concent_request_id,
            item.connection_id,
            item.message,
            get_current_utc_timestamp()
        )
        frame = create_middleman_protocol_message(PayloadType.GOLEM_MESSAGE, item.message, signing_service_request_id)
        await send_over_stream_async(frame, signing_service_writer, settings.CONCENT_PRIVATE_KEY)
        request_queue.task_done()


async def response_producer(
    response_queue_pool: Dict[int, Queue],
    signing_service_reader: StreamReader,
    message_tracker: Dict[int, MessageTrackerItem],
) -> None:
    message_tracker = message_tracker  # type: OrderedDict[int, MessageTrackerItem]
    while True:
        try:
            frame = await handle_frame_receive_async(signing_service_reader, settings.SIGNING_SERVICE_PUBLIC_KEY)
        except IncompleteReadError:
            logger.info(f"Signing Service has closed the connection. Ending 'response_producer' coroutine")
            break
        except ERRORS_THAT_CAUSE_CURRENT_ITERATION_ENDS as exception:
            logger.info(f"Received invalid message: error code={map_exception_to_error_code(exception)}")
            print(exception)
            continue

        logger.info(
            f"Received message from Signing Service: request ID = {frame.request_id}"
        )
        if frame.request_id not in message_tracker:
            logger.info(f"There is no entry in Message Tracker for request ID = {frame.request_id}, skipping...")
            continue

        current_track: MessageTrackerItem = message_tracker[frame.request_id]
        if current_track.connection_id not in response_queue_pool:
            logger.info(f"Response queue for {current_track.connection_id} doesn't exist anymore, skipping...")
            # there will be no more processing for current_track, it should be removed from message_tracker
            del message_tracker[frame.request_id]
            continue

        discard_entries_for_lost_messages(frame.request_id, message_tracker, logger)
        await response_queue_pool[current_track.connection_id].put(
            ResponseQueueItem(
                message=frame.payload,
                concent_request_id=current_track.concent_request_id,
                timestamp=get_current_utc_timestamp(),
            )
        )
        # message has been processed, now it should be removed from message_tracker
        del message_tracker[frame.request_id]


async def response_consumer(
    response_queue: Queue,
    writer: StreamWriter,
    connection_id: int
) -> None:
    while True:
        item = await response_queue.get()
        assert isinstance(item, ResponseQueueItem)
        frame: GolemMessageFrame = create_middleman_protocol_message(
            PayloadType.GOLEM_MESSAGE,
            item.message,
            item.concent_request_id,
        )

        await send_over_stream_async(frame, writer, settings.CONCENT_PRIVATE_KEY)
        logger.info(
            f"Message (request ID={frame.request_id}) for Concent has been sent for connection ID={connection_id}"
        )
        response_queue.task_done()


def discard_entries_for_lost_messages(
    current_request_id: int,
    message_tracker: Dict[int, MessageTrackerItem],
    logger_: Logger
) -> None:
    message_tracker = message_tracker  # type: OrderedDict[int, MessageTrackerItem]
    lost_messages_counter = 0
    for signinig_service_request_id in message_tracker.keys():
        if signinig_service_request_id == current_request_id:
            break
        lost_messages_counter += 1

    if lost_messages_counter == len(message_tracker):
        logger_.warning(f"Signing Service request ID has not been found - this should not happen")
        return

    for _ in range(lost_messages_counter):
        request_id, item = message_tracker.popitem(last=False)
        logger_.info(
            f"Dropped message: Signing Service request ID={request_id}, "
            f"Concent connection ID={item.connection_id}, "
            f"messsage={item.message}, "
            f"received at: {item.timestamp}"
        )


def create_error_frame(exception: MiddlemanProtocolError) -> ErrorFrame:
    error_code = map_exception_to_error_code(exception)
    return ErrorFrame(
        (error_code, str(exception)),
        REQUEST_ID_FOR_RESPONSE_FOR_INVALID_FRAME,
    )