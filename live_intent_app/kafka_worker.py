"""Long-running on-prem Kafka consumer for inference workers."""

from __future__ import annotations

import json
import logging
import os

from kafka import KafkaConsumer

from .pipeline import LiveIntentPipeline
from .settings import load_local_environment
from .transports import kafka_options, required

load_local_environment()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    log = logging.getLogger("live_intent.kafka")
    options = kafka_options()
    options.pop("client_id", None)
    consumer = KafkaConsumer(
        required("KAFKA_TOPIC"),
        group_id=os.getenv("KAFKA_CONSUMER_GROUP", "live-intent-inference"),
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        **options,
    )
    pipeline = LiveIntentPipeline()
    for message in consumer:
        result = pipeline.process(json.loads(message.value.decode("utf-8")))
        consumer.commit()
        log.info(
            "Inferred event=%s intent=%s saved=%s",
            result["event_id"],
            result["intent"],
            result["saved"],
        )


if __name__ == "__main__":
    main()
