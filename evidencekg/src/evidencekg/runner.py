from .db import dump
from .review_queue import TERMINAL_STATES, Queue


def run(store, run_id, worker, max_calls=0):
    if type(max_calls) is not int or max_calls < 0:
        raise ValueError("max_calls must be a nonnegative integer")
    queue = Queue(store)
    with store.write():
        row = queue.run(run_id)
        cfg = queue._contract(row)
        # A completed/exhausted run never calls a worker or changes its archived identity.
        if row["state"] in TERMINAL_STATES:
            return queue.review_status(run_id)
        identity = {
            "model": worker.model,
            "effort": getattr(worker, "effort", "administrator-configured"),
            "adapter": getattr(worker, "identity", type(worker).__module__ + "." + type(worker).__qualname__),
        }
        if not isinstance(worker.model, str) or not worker.model.strip():
            raise ValueError("Explicit worker model required")
        if cfg["model"] not in ("unspecified", worker.model):
            raise ValueError("Resume requires the frozen model identity")
        if cfg.get("worker_identity", identity) != identity:
            raise ValueError("Resume requires the frozen model, effort and adapter identity")
        if "worker_identity" not in cfg:
            cfg["model"], cfg["worker_identity"] = worker.model, identity
            store.db.execute("UPDATE runs SET runner_config_json=? WHERE id=?", (dump(cfg), run_id))
            store.audit("worker_identity_frozen", identity, run_id)
        store.db.execute("UPDATE runs SET state='running' WHERE id=?", (run_id,))
    calls = 0
    try:
        while not max_calls or calls < max_calls:
            task = queue.next_review_task(run_id, worker.model)
            if task is None:
                break
            calls += 1
            try:
                result = worker.call(task)
            except BaseException as exc:
                queue.fail(run_id, task["task_id"], task["lease_id"], exc)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                break
            try:
                queue.submit_review_result(
                    run_id, task["task_id"], task["lease_id"], task["input_sha"], result
                )
            except ValueError:
                # Rejections are durable; the next lease consumes the same finite retry budget.
                continue
        with store.write():
            queue._advance(queue.run(run_id))
            if queue.run(run_id)["state"] not in TERMINAL_STATES:
                store.db.execute("UPDATE runs SET state='paused' WHERE id=?", (run_id,))
    finally:
        queue._status_file(run_id, force=True)
    return queue.review_status(run_id)
