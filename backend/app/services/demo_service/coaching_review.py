"""Player verdicts on coaching suggestions.

A rule firing proves a suggestion is consistent with the parsed facts; it does
not prove the suggestion was worth showing. The verdicts collected here --
helpful / irrelevant / unsure, one per owner per suggestion -- are the evidence
that turns "how many suggestions fired" into "which rules earn their
thresholds". The per-rule summary is what threshold tuning reads
(docs/coaching_feedback_v1.md).
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy.exc import IntegrityError

from app.models.coaching import CoachingEvent, CoachingFeedback
from app.models.demo import Demo
from app.schemas.coaching import (
    COACHING_VERDICTS,
    CoachingEventOut,
    CoachingFeedbackOut,
    CoachingFeedbackSummary,
    CoachingRuleFeedbackSummary,
)
from app.services.demo_service._component import ServiceComponent
from app.services.demo_service._helpers import utc_now
from app.services.demo_service.errors import DemoGoneError
from app.services.demo_service.gone import gone_rows_raise, row_identity, rows_missing


class CoachingReview(ServiceComponent):
    """Reached as ``DemoService.coaching``."""

    def list_coaching_events(self, demo_id: str) -> list[CoachingEventOut]:
        events = (
            self.db.query(CoachingEvent)
            .filter(CoachingEvent.demo_id == demo_id)
            .order_by(CoachingEvent.tick_start.asc())
            .all()
        )
        feedback = self._feedback_by_event(demo_id)
        return [
            CoachingEventOut.model_validate(event).model_copy(update={"feedback": feedback.get(event.id)})
            for event in events
        ]

    def save_coaching_feedback(
        self,
        demo: Demo,
        event_id: str,
        *,
        verdict: str,
        note: str | None = None,
    ) -> CoachingFeedbackOut | None:
        """Upsert the owner's verdict; None when the event is not in this demo.

        Raises DemoGoneError when the match was deleted while this ran.
        """
        owner_id = self._service.require_owner_id()
        demo_id = row_identity(demo)
        with gone_rows_raise(self.db, demo_id=demo_id):
            return self._save_coaching_feedback(demo, event_id, owner_id, verdict=verdict, note=note)

    def _save_coaching_feedback(
        self,
        demo: Demo,
        event_id: str,
        owner_id: str,
        *,
        verdict: str,
        note: str | None,
    ) -> CoachingFeedbackOut | None:
        if verdict not in COACHING_VERDICTS:
            raise ValueError("Unsupported coaching verdict")
        event_exists = (
            self.db.query(CoachingEvent.id)
            .filter(CoachingEvent.id == event_id, CoachingEvent.demo_id == demo.id)
            .scalar()
        )
        if event_exists is None:
            return None
        cleaned_note = (note or "").strip() or None
        now = utc_now()
        row = (
            self.db.query(CoachingFeedback)
            .filter(CoachingFeedback.owner_id == owner_id, CoachingFeedback.event_id == event_id)
            .one_or_none()
        )
        if row is None:
            row = CoachingFeedback(
                id=str(uuid.uuid4()),
                demo_id=demo.id,
                event_id=event_id,
                owner_id=owner_id,
                verdict=verdict,
                note=cleaned_note,
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
        else:
            row.verdict = verdict
            row.note = cleaned_note
            row.updated_at = now
        try:
            self.db.commit()
        except IntegrityError:
            # Either the match was deleted since the lookup above (the demo
            # foreign key) or two verdicts for the same suggestion raced past it
            # and the unique constraint caught the second insert. The first is
            # a 404; apply the second as the update it would have been had the
            # lookup seen the first.
            if rows_missing(self.db, demo_id=row_identity(demo)):
                raise DemoGoneError("Demo was deleted") from None
            row = (
                self.db.query(CoachingFeedback)
                .filter(CoachingFeedback.owner_id == owner_id, CoachingFeedback.event_id == event_id)
                .one()
            )
            row.verdict = verdict
            row.note = cleaned_note
            row.updated_at = now
            self.db.commit()
        self.db.refresh(row)
        return CoachingFeedbackOut.model_validate(row)

    def clear_coaching_feedback(self, demo: Demo, event_id: str) -> bool:
        owner_id = self._service.require_owner_id()
        deleted = (
            self.db.query(CoachingFeedback)
            .filter(
                CoachingFeedback.owner_id == owner_id,
                CoachingFeedback.demo_id == demo.id,
                CoachingFeedback.event_id == event_id,
            )
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return deleted > 0

    def coaching_feedback_summary(self, demo_id: str | None = None) -> CoachingFeedbackSummary:
        """Verdict counts per rule across the owner's demos (or one demo).

        Only verdicts whose event still exists are counted: a re-parse that
        drops a suggestion also drops its verdict from the totals, so the
        summary always describes the suggestions a player can see today.
        """
        owner_id = self._service.require_owner_id()
        demo_query = self.db.query(Demo.id).filter(Demo.owner_id == owner_id)
        if demo_id is not None:
            demo_query = demo_query.filter(Demo.id == demo_id)
        demo_ids = [row[0] for row in demo_query.all()]
        if not demo_ids:
            return CoachingFeedbackSummary(
                demo_count=0, total=0, rated=0, helpful=0, irrelevant=0, unsure=0, rules=[]
            )

        events = (
            self.db.query(CoachingEvent.id, CoachingEvent.structured_context_json)
            .filter(CoachingEvent.demo_id.in_(demo_ids))
            .all()
        )
        verdicts = {
            row.event_id: row.verdict
            for row in self.db.query(CoachingFeedback)
            .filter(CoachingFeedback.owner_id == owner_id, CoachingFeedback.demo_id.in_(demo_ids))
            .all()
        }
        # ruleId lives inside the JSON context, so the grouping happens here
        # rather than in SQL; an owner's demos hold at most a few thousand rows.
        per_rule: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "helpful": 0, "irrelevant": 0, "unsure": 0}
        )
        for event_id, context in events:
            bucket = per_rule[_rule_id(context)]
            bucket["total"] += 1
            verdict = verdicts.get(event_id)
            if verdict in COACHING_VERDICTS:
                bucket[verdict] += 1
        rules = [
            CoachingRuleFeedbackSummary(
                rule_id=rule_id,
                total=bucket["total"],
                rated=bucket["helpful"] + bucket["irrelevant"] + bucket["unsure"],
                helpful=bucket["helpful"],
                irrelevant=bucket["irrelevant"],
                unsure=bucket["unsure"],
            )
            for rule_id, bucket in sorted(per_rule.items())
        ]
        return CoachingFeedbackSummary(
            demo_count=len(demo_ids),
            total=sum(rule.total for rule in rules),
            rated=sum(rule.rated for rule in rules),
            helpful=sum(rule.helpful for rule in rules),
            irrelevant=sum(rule.irrelevant for rule in rules),
            unsure=sum(rule.unsure for rule in rules),
            rules=rules,
        )

    def _feedback_by_event(self, demo_id: str) -> dict[str, CoachingFeedbackOut]:
        if self.owner_id is None:
            return {}
        rows = (
            self.db.query(CoachingFeedback)
            .filter(CoachingFeedback.demo_id == demo_id, CoachingFeedback.owner_id == self.owner_id)
            .all()
        )
        return {row.event_id: CoachingFeedbackOut.model_validate(row) for row in rows}


def _rule_id(context: object) -> str:
    # Mirrors ruleIdForEvent in frontend/lib/coaching-review.ts, including its
    # fallback for hand-written mock events, so both sides group the same way.
    if isinstance(context, dict):
        value = context.get("ruleId") or context.get("rule")
        if isinstance(value, str) and value.strip():
            return value
    return "mock"
