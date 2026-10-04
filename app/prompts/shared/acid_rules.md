ACID REQUIREMENTS
- Atomicity: a multi-step operation (e.g. create an order AND its items AND adjust stock) must be one PostgreSQL
  function/procedure so that it succeeds or fails as a unit. Never rely on the caller to wrap steps in a transaction.
- Consistency: all PRIMARY KEY, FOREIGN KEY, UNIQUE, NOT NULL and CHECK constraints must keep holding. Never bypass,
  disable, defer-forever or weaken a constraint to make an operation succeed. Do not catch constraint errors and hide
  them: let SQLSTATE errors (23505 unique, 23503 foreign key, 23502 not null, 23514 check) reach the caller.
- Isolation: where concurrent writers could break an invariant (stock decrement, balance update, check-then-insert),
  use a single atomic statement (UPDATE ... SET x = x - n WHERE x >= n), SELECT ... FOR UPDATE, or a constraint,
  instead of read-then-write without locking. Do not take unnecessarily broad locks.
- Durability: use ordinary transactional commits only. Do not simulate or alter durability behaviour.
- Never issue COMMIT/ROLLBACK/BEGIN inside functions; the caller owns the transaction boundary.
