NORMALIZATION RULES (target: Third Normal Form, 3NF)
- 1NF: every column holds one atomic value. No arrays, delimited lists, JSON blobs or numbered repeating
  columns (phone1, phone2, ...) used to avoid proper relational modelling. Model repeating data as a child table.
- 2NF: in tables with a composite key, every non-key column depends on the WHOLE key. Move attributes that
  depend on only part of the key into their own table.
- 3NF: no non-key column may depend on another non-key column (transitive dependency). Do not copy a parent's
  attribute (e.g. user_email) into a child table that already has the parent's foreign key.
- A many-to-many relationship ALWAYS needs a junction table whose primary key is (or includes) the two foreign keys.
- Denormalize only for a clear, stated architectural reason. Every intentional denormalization must be listed
  with its reason in normalization.intentional_denormalizations; otherwise normalization.violations must be empty.
