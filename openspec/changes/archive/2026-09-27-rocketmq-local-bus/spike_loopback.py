"""Spike loopback: produce + consume via Proxy on localhost:18081."""
from __future__ import annotations

import sys
import time
import uuid

from rocketmq import (
    ClientConfiguration,
    Credentials,
    FilterExpression,
    Message,
    Producer,
    SimpleConsumer,
)

ENDPOINTS = "127.0.0.1:18081"
TOPIC = "spike_loopback"
GROUP = "gid_spike_loopback"
TAG = "spike.test"
BODY = f"spike-loopback-{uuid.uuid4().hex[:12]}".encode("utf-8")


def main() -> int:
    conf = ClientConfiguration(ENDPOINTS, Credentials(), request_timeout=5)

    # Do not pre-declare topics: Producer.startup() waits forever on settings sync
    # when topics= is set (known SDK behavior with Proxy on host-mapped ports).
    producer = Producer(conf)
    producer.startup()
    try:
        msg = Message()
        msg.topic = TOPIC
        msg.tag = TAG
        msg.body = BODY
        msg.keys = "spike-key"
        receipt = producer.send(msg)
        print(f"SEND_OK message_id={receipt.message_id} body={BODY!r}")
    finally:
        producer.shutdown()

    consumer = SimpleConsumer(
        conf,
        GROUP,
        subscription={TOPIC: FilterExpression(TAG)},
    )
    consumer.startup()
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            messages = consumer.receive(8, 30)
            if not messages:
                print("RECV_WAIT empty batch, retry...")
                continue
            for m in messages:
                body = (
                    m.body
                    if isinstance(m.body, (bytes, bytearray))
                    else bytes(m.body)
                )
                print(
                    f"RECV_OK message_id={m.message_id} tag={m.tag} body={body!r}"
                )
                consumer.ack(m)
                if body == BODY:
                    print("LOOPBACK_PASS")
                    return 0
            print("RECV_OTHER continuing...")
        print("LOOPBACK_FAIL timeout waiting for test message", file=sys.stderr)
        return 1
    finally:
        consumer.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
