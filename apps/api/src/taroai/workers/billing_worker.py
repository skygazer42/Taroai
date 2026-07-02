from pydantic import BaseModel

from taroai.workers.models import BillingAggregationJob, JobEnvelope, JobType
from taroai.workers.queue import JobQueue


class BillingWorker(BaseModel):
    queue: JobQueue
    worker_id: str = "billing_worker"
    lease_seconds: int = 300

    def process_next(self) -> JobEnvelope | None:
        job = self.queue.claim(
            JobType.BILLING_AGGREGATION,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None
        BillingAggregationJob.model_validate(job.payload)
        return self.queue.ack(job.id)
