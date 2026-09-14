class PersistenceError(RuntimeError):
    pass


class IdempotencyConflictError(PersistenceError):
    pass


class OptimisticConcurrencyError(PersistenceError):
    pass


class StaleLeaseError(PersistenceError):
    pass
