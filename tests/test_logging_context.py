import logging
from io import StringIO

from courier.infrastructure.logging_context import CourierLogContextFilter, log_context


def test_log_context_adds_context_fields(caplog):
    logger = logging.getLogger("courier.test_logging_context")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(CourierLogContextFilter())
    handler.setFormatter(logging.Formatter("%(courier_context)s%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    caplog.set_level(logging.INFO, logger=logger.name)

    with log_context(request_id="wh_123", event_id="evt_456", message_id="msg_789"):
        logger.info("forwarded")

    assert "[request_id=wh_123 event_id=evt_456 message_id=msg_789] forwarded" in stream.getvalue()


def test_log_context_resets_fields_after_exit(caplog):
    logger = logging.getLogger("courier.test_logging_context.reset")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(CourierLogContextFilter())
    handler.setFormatter(logging.Formatter("%(courier_context)s%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    caplog.set_level(logging.INFO, logger=logger.name)

    with log_context(message_id="msg_789"):
        logger.info("inside")
    logger.info("outside")

    assert "[message_id=msg_789] inside" in stream.getvalue()
    assert "outside" in stream.getvalue()
    assert "[message_id=msg_789] outside" not in stream.getvalue()


def test_nested_log_context_preserves_outer_fields(caplog):
    logger = logging.getLogger("courier.test_logging_context.nested")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(CourierLogContextFilter())
    handler.setFormatter(logging.Formatter("%(courier_context)s%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    caplog.set_level(logging.INFO, logger=logger.name)

    with log_context(request_id="wh_123", event_id="evt_456"):
        with log_context(message_id="msg_789"):
            logger.info("inside")
        logger.info("after nested")

    assert "[request_id=wh_123 event_id=evt_456 message_id=msg_789] inside" in stream.getvalue()
    assert "[request_id=wh_123 event_id=evt_456] after nested" in stream.getvalue()
