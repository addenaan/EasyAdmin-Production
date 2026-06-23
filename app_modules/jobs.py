import threading
import uuid
from datetime import datetime


class BackgroundJobManager:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def _now(self):
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def start(self, title, target, *args, **kwargs):
        job_id = uuid.uuid4().hex[:12]
        record = {
            'id': job_id,
            'title': title,
            'status': 'queued',
            'progress': 0,
            'message': 'Queued',
            'created_at': self._now(),
            'started_at': None,
            'finished_at': None,
            'result': None,
            'error': None
        }
        with self._lock:
            self._jobs[job_id] = record
        thread = threading.Thread(target=self._run, args=(job_id, target, args, kwargs), daemon=True)
        thread.start()
        return job_id

    def _run(self, job_id, target, args, kwargs):
        self.update(job_id, status='running', progress=5, message='Processing', started_at=self._now())
        try:
            result = target(job_id, *args, **kwargs)
            self.update(job_id, status='complete', progress=100, message='Complete', finished_at=self._now(), result=result)
        except Exception as exc:
            self.update(job_id, status='failed', message=str(exc), error=str(exc), finished_at=self._now())

    def update(self, job_id, **fields):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, limit=25):
        with self._lock:
            jobs = list(self._jobs.values())[-limit:]
            return [dict(job) for job in reversed(jobs)]


job_manager = BackgroundJobManager()
