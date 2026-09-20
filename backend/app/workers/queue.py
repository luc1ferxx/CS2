"""Reliable-queue primitives for the parse worker.

The queue itself is only a dispatch signal -- the database is the durable record
of what has to happen. What this module adds is the guarantee that a message
which left the queue is still accounted for somewhere: while a consumer holds a
message it sits in that consumer's processing list, and the consumer holds a
lease that it must keep renewing. When the lease lapses, any other worker may
return that consumer's in-flight messages to the queue.

Redis has no persistence in this deployment, so none of this survives a Redis
restart. The database reconciliation pass in DemoService is the layer that does;
see reclaim_stale_parse_jobs.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)

CONSUMERS_KEY = "cs2-demo-coach:worker:consumers"
LEASE_KEY_PREFIX = "cs2-demo-coach:worker:lease:"


def new_consumer_id() -> str:
    """Build an id that is unique per worker process, not per deployment.

    A restarted container gets a new id on purpose: the old id's processing list
    then has no live lease behind it, which is exactly the signal the reaper
    needs to return whatever that process was holding when it died.
    """
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def processing_key(queue_name: str, consumer_id: str) -> str:
    return f"{queue_name}:processing:{consumer_id}"


def lease_key(consumer_id: str) -> str:
    return f"{LEASE_KEY_PREFIX}{consumer_id}"


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def known_consumers(redis_client: Any) -> list[str]:
    members = redis_client.smembers(CONSUMERS_KEY) or []
    return [text for text in (_as_str(member) for member in members) if text]


def count_in_flight(redis_client: Any, queue_name: str) -> int:
    """Total messages held by every registered consumer.

    Diagnostics reports this beside the queue length: once messages move to a
    processing list on their way out, a plain LLEN of the queue no longer
    accounts for everything that is still owed.
    """
    total = 0
    for consumer_id in known_consumers(redis_client):
        total += int(redis_client.llen(processing_key(queue_name, consumer_id)) or 0)
    return total


class ParseQueue:
    """One worker process's view of the shared parse queue."""

    def __init__(
        self,
        redis_client: Any,
        *,
        queue_name: str,
        consumer_id: str,
        lease_ttl_seconds: int,
    ) -> None:
        self.redis = redis_client
        self.queue_name = queue_name
        self.consumer_id = consumer_id
        self.lease_ttl_seconds = lease_ttl_seconds
        self.processing_key = processing_key(queue_name, consumer_id)
        self.lease_key = lease_key(consumer_id)

    def register(self) -> None:
        self.redis.sadd(CONSUMERS_KEY, self.consumer_id)
        self.renew_lease()

    def renew_lease(self) -> None:
        self.redis.setex(self.lease_key, self.lease_ttl_seconds, self.consumer_id)

    def reserve(self, *, timeout: int) -> str | None:
        """Move one message from the queue onto this consumer's processing list.

        Returns the raw payload, which must be handed back to release() or
        requeue() once the job is done -- until then the message is still owned
        by this consumer and is recoverable if the process dies.
        """
        return _as_str(self.redis.brpoplpush(self.queue_name, self.processing_key, timeout))

    def release(self, payload: str) -> None:
        """Drop a finished message. Payloads carry a unique job id, so a count
        of 1 can never remove the wrong entry."""
        self.redis.lrem(self.processing_key, 1, payload)

    def requeue(self, payload: str) -> None:
        self.redis.lrem(self.processing_key, 1, payload)
        self.redis.lpush(self.queue_name, payload)

    def unregister(self) -> None:
        self.redis.delete(self.lease_key)
        self.redis.srem(CONSUMERS_KEY, self.consumer_id)

    def reap(self, *, on_orphan: Callable[[dict[str, Any]], bool]) -> int:
        """Return in-flight messages held by consumers whose lease has lapsed.

        `on_orphan` gets the decoded payload and decides whether the message
        should go back to the queue -- it returns False for a job that has been
        failed for good, so a demo that crashes the parser every time does not
        cycle forever. The message is moved either way once the callback has had
        its turn: a callback that raises must not leave the message stranded in a
        dead consumer's list, and a duplicate delivery is harmless because
        claim_parse_job is a compare-and-set.
        """
        reclaimed = 0
        for consumer_id in self._dead_consumers():
            source = processing_key(self.queue_name, consumer_id)
            while True:
                payload = _as_str(self.redis.lindex(source, -1))
                if payload is None:
                    break
                if self._return_one(source, payload, on_orphan):
                    reclaimed += 1
            self.redis.srem(CONSUMERS_KEY, consumer_id)
            logger.info("Reaped consumer %s", consumer_id)
        return reclaimed

    def _dead_consumers(self) -> Iterable[str]:
        for consumer_id in known_consumers(self.redis):
            if consumer_id == self.consumer_id:
                continue
            if self.redis.exists(lease_key(consumer_id)):
                continue
            yield consumer_id

    def _return_one(
        self,
        source: str,
        payload: str,
        on_orphan: Callable[[dict[str, Any]], bool],
    ) -> bool:
        requeue = True
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError):
            logger.warning("Discarding unreadable message from %s", source)
            requeue = False
        else:
            try:
                requeue = on_orphan(decoded)
            except Exception:
                # The database side failed. Put the message back anyway and let
                # reconciliation sort the row out; losing it is the one outcome
                # this whole module exists to prevent.
                logger.exception("Orphan handler failed; requeueing anyway")
                requeue = True

        if requeue:
            moved = self.redis.rpoplpush(source, self.queue_name)
        else:
            moved = self.redis.rpop(source)
        return moved is not None
