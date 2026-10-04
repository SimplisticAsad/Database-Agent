"""Hand-written stand-ins for LLM answers on the example shop (used only by tests)."""

import copy


def _col(name, type_, nullable=False, source=None):
    return {"name": name, "type": type_, "nullable": nullable, "source_field": source or name}


def _fk(cols, table, ref_cols=("id",), on_delete="RESTRICT"):
    return {"columns": list(cols), "ref_table": table, "ref_columns": list(ref_cols), "on_delete": on_delete}


def _table(name, purpose, entities, columns, pk, fks=(), uniques=(), indexes=()):
    return {"name": name, "purpose": purpose, "source_entities": entities, "columns": columns,
            "primary_key": {"columns": pk}, "foreign_keys": list(fks),
            "unique_constraints": [list(u) for u in uniques], "indexes": list(indexes), "dependencies": []}


def _ix(cols, reason):
    return {"columns": list(cols), "unique": False, "reason": reason}


ARCHITECTURE = {
    "tables": [
        _table("users", "Registered customers", ["User"],
               [_col("id", "integer"), _col("email", "text"), _col("name", "text")], ["id"], uniques=[["email"]]),
        _table("categories", "Product categories", ["Category"],
               [_col("id", "integer"), _col("name", "text")], ["id"], uniques=[["name"]]),
        _table("products", "Products for sale", ["Product"],
               [_col("id", "integer"), _col("name", "text"), _col("price", "numeric(12,2)"), _col("stock", "integer")], ["id"]),
        _table("product_categories", "Junction: products <-> categories", [],
               [_col("product_id", "integer"), _col("category_id", "integer")], ["product_id", "category_id"],
               fks=[_fk(["product_id"], "products", on_delete="CASCADE"), _fk(["category_id"], "categories", on_delete="CASCADE")],
               indexes=[_ix(["category_id"], "find products of a category")]),
        _table("orders", "Customer orders", ["Order"],
               [_col("id", "integer"), _col("user_id", "integer"), _col("created_at", "timestamptz")], ["id"],
               fks=[_fk(["user_id"], "users")], indexes=[_ix(["user_id"], "list orders of a user")]),
        _table("order_items", "Order lines", ["OrderItem"],
               [_col("id", "integer"), _col("order_id", "integer"), _col("product_id", "integer"),
                _col("quantity", "integer"), _col("unit_price", "numeric(12,2)")], ["id"],
               fks=[_fk(["order_id"], "orders", on_delete="CASCADE"), _fk(["product_id"], "products")],
               uniques=[["order_id", "product_id"]], indexes=[_ix(["product_id"], "find lines for a product")]),
    ],
    "relationships": [
        {"parent_table": "users", "child_table": "orders", "cardinality": "one_to_many"},
        {"parent_table": "orders", "child_table": "order_items", "cardinality": "one_to_many"},
        {"parent_table": "products", "child_table": "order_items", "cardinality": "one_to_many"},
        {"parent_table": "products", "child_table": "categories", "cardinality": "many_to_many", "via_table": "product_categories"},
    ],
    "creation_order": ["users", "categories", "products", "product_categories", "orders", "order_items"],
    "normalization": {"target": "3NF", "analysis": ["every table is in 3NF"], "violations": [], "intentional_denormalizations": []},
}


def bad_architecture() -> dict:
    """order_items references a table that does not exist."""
    arch = copy.deepcopy(ARCHITECTURE)
    arch["tables"][5]["foreign_keys"][1]["ref_table"] = "product"
    return arch


SCHEMA_STATEMENTS = [
    "CREATE TABLE users (id integer GENERATED ALWAYS AS IDENTITY, email text NOT NULL, name text NOT NULL, "
    "CONSTRAINT pk_users PRIMARY KEY (id), CONSTRAINT uq_users_email UNIQUE (email), "
    "CONSTRAINT ck_users_name CHECK (length(btrim(name)) > 0));",
    "CREATE TABLE categories (id integer GENERATED ALWAYS AS IDENTITY, name text NOT NULL, "
    "CONSTRAINT pk_categories PRIMARY KEY (id), CONSTRAINT uq_categories_name UNIQUE (name));",
    "CREATE TABLE products (id integer GENERATED ALWAYS AS IDENTITY, name text NOT NULL, price numeric(12,2) NOT NULL, "
    "stock integer NOT NULL DEFAULT 0, CONSTRAINT pk_products PRIMARY KEY (id), "
    "CONSTRAINT ck_products_price CHECK (price >= 0), CONSTRAINT ck_products_stock CHECK (stock >= 0));",
    "CREATE TABLE product_categories (product_id integer NOT NULL, category_id integer NOT NULL, "
    "CONSTRAINT pk_product_categories PRIMARY KEY (product_id, category_id), "
    "CONSTRAINT fk_product_categories_product_id FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE, "
    "CONSTRAINT fk_product_categories_category_id FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE);",
    "CREATE INDEX ix_product_categories_category_id ON product_categories (category_id);",
    "CREATE TABLE orders (id integer GENERATED ALWAYS AS IDENTITY, user_id integer NOT NULL, "
    "created_at timestamptz NOT NULL DEFAULT now(), CONSTRAINT pk_orders PRIMARY KEY (id), "
    "CONSTRAINT fk_orders_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT);",
    "CREATE INDEX ix_orders_user_id ON orders (user_id);",
    "CREATE TABLE order_items (id integer GENERATED ALWAYS AS IDENTITY, order_id integer NOT NULL, product_id integer NOT NULL, "
    "quantity integer NOT NULL, unit_price numeric(12,2) NOT NULL, CONSTRAINT pk_order_items PRIMARY KEY (id), "
    "CONSTRAINT uq_order_items_order_product UNIQUE (order_id, product_id), "
    "CONSTRAINT ck_order_items_quantity CHECK (quantity > 0), CONSTRAINT ck_order_items_unit_price CHECK (unit_price >= 0), "
    "CONSTRAINT fk_order_items_order_id FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE, "
    "CONSTRAINT fk_order_items_product_id FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT);",
    "CREATE INDEX ix_order_items_product_id ON order_items (product_id);",
]


def bad_schema_statements() -> list[str]:
    """Statically fine, but PostgreSQL rejects it: the CHECK names a column that does not exist."""
    statements = list(SCHEMA_STATEMENTS)
    statements[2] = statements[2].replace("CHECK (price >= 0)", "CHECK (prices >= 0)")
    return statements


def _fn(name, args, body_sql):
    return [f"DROP FUNCTION IF EXISTS {name}({args});", body_sql]


def crud_statements(strict_update: bool = True) -> list[str]:
    not_found = (
        "IF NOT FOUND THEN RAISE EXCEPTION 'user % not found', p_id USING ERRCODE = 'P0002'; END IF; "
        if strict_update else ""
    )
    statements: list[str] = []
    statements += _fn("create_user", "text, text",
        "CREATE FUNCTION create_user(p_email text, p_name text) RETURNS users LANGUAGE sql AS $$ "
        "INSERT INTO users (email, name) VALUES (p_email, p_name) RETURNING *; $$;")
    statements += _fn("get_user", "integer",
        "CREATE FUNCTION get_user(p_id integer) RETURNS SETOF users LANGUAGE sql AS $$ SELECT * FROM users WHERE id = p_id; $$;")
    statements += _fn("update_user", "integer, text, text",
        "CREATE FUNCTION update_user(p_id integer, p_email text, p_name text) RETURNS users LANGUAGE plpgsql AS $$ "
        "DECLARE r users; BEGIN UPDATE users SET email = p_email, name = p_name WHERE id = p_id RETURNING * INTO r; "
        f"{not_found}RETURN r; END $$;")
    statements += _fn("delete_user", "integer",
        "CREATE FUNCTION delete_user(p_id integer) RETURNS boolean LANGUAGE plpgsql AS $$ BEGIN "
        "DELETE FROM users WHERE id = p_id; RETURN FOUND; END $$;")
    statements += _fn("list_users", "",
        "CREATE FUNCTION list_users() RETURNS SETOF users LANGUAGE sql AS $$ SELECT * FROM users ORDER BY id; $$;")
    statements += _fn("create_product", "text, numeric, integer",
        "CREATE FUNCTION create_product(p_name text, p_price numeric, p_stock integer) RETURNS products LANGUAGE sql AS $$ "
        "INSERT INTO products (name, price, stock) VALUES (p_name, p_price, p_stock) RETURNING *; $$;")
    statements += _fn("create_order", "integer, jsonb",
        "CREATE FUNCTION create_order(p_user_id integer, p_items jsonb) RETURNS orders LANGUAGE plpgsql AS $$ "
        "DECLARE o orders; it record; BEGIN "
        "INSERT INTO orders (user_id) VALUES (p_user_id) RETURNING * INTO o; "
        "FOR it IN SELECT * FROM jsonb_to_recordset(p_items) AS x(product_id integer, quantity integer) LOOP "
        "UPDATE products SET stock = stock - it.quantity WHERE id = it.product_id AND stock >= it.quantity; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'unknown product or insufficient stock for %', it.product_id; END IF; "
        "INSERT INTO order_items (order_id, product_id, quantity, unit_price) "
        "SELECT o.id, p.id, it.quantity, p.price FROM products p WHERE p.id = it.product_id; "
        "END LOOP; RETURN o; END $$;")
    statements += _fn("get_orders_for_user", "integer",
        "CREATE FUNCTION get_orders_for_user(p_user_id integer) RETURNS SETOF orders LANGUAGE sql AS $$ "
        "SELECT * FROM orders WHERE user_id = p_user_id ORDER BY id; $$;")
    return statements


CRUD_FUNCTIONS = [
    ("create_user", "users", "create"), ("get_user", "users", "get"), ("update_user", "users", "update"),
    ("delete_user", "users", "delete"), ("list_users", "users", "list"), ("create_product", "products", "create"),
    ("create_order", "orders", "custom"), ("get_orders_for_user", "orders", "list"),
]


def crud_script(strict_update: bool = True) -> dict:
    return {
        "statements": crud_statements(strict_update),
        "functions": [{"name": n, "table": t, "operation": op, "description": n} for n, t, op in CRUD_FUNCTIONS],
        "skipped_tables": [{"table": "product_categories", "reason": "junction table; managed by link operations later"},
                           {"table": "categories", "reason": "not exercised by the MVP shop flow"},
                           {"table": "order_items", "reason": "created atomically through create_order"}],
    }


def schema_script(statements=None) -> dict:
    return {"statements": statements or SCHEMA_STATEMENTS, "notes": []}


# --- BDD scenarios ------------------------------------------------------------------------------
def step(keyword, text, sql=None, expect=None, capture=None):
    out = {"keyword": keyword, "text": text}
    if sql:
        out["sql"] = sql
    if expect:
        out["expect"] = expect
    if capture:
        out["capture"] = capture
    return out


def scenario(name, category, *steps):
    return {"name": name, "category": category, "steps": list(steps)}


_ERR = lambda state: {"outcome": "error", "sqlstate": state}  # noqa: E731
_NEW_USER = step("Given", "a user exists", "SELECT id FROM create_user('a@example.com', 'Ann')", capture={"user_id": 0})
_NEW_PRODUCT = lambda stock: step("And", f"a product with stock {stock} exists",  # noqa: E731
                                  f"SELECT id FROM create_product('Pen', 10.00, {stock})", capture={"product_id": 0})
_ORDER_JSON = lambda qty: ("jsonb_build_array(jsonb_build_object('product_id', {{product_id}}, 'quantity', %s))" % qty)  # noqa: E731


def zero_quantity_scenario(expected_state: str) -> dict:
    return scenario(
        "Reject an order line with zero quantity", "boundary", _NEW_USER, _NEW_PRODUCT(5),
        step("When", "I order zero units",
             f"SELECT * FROM create_order({{{{user_id}}}}, {_ORDER_JSON(0)})", expect=_ERR(expected_state)),
    )


def suite(zero_state: str = "23505", include_missing_update: bool = False) -> dict:
    users = [
        scenario("Create a valid user", "happy_path",
                 step("When", "I create a user", "SELECT email FROM create_user('a@example.com', 'Ann')",
                      expect={"rows": [["a@example.com"]]})),
        scenario("Prevent duplicate email", "constraint_violation", _NEW_USER,
                 step("When", "I create another user with the same email", "SELECT * FROM create_user('a@example.com', 'Bob')",
                      expect=_ERR("23505"))),
        scenario("Reject a user without a name", "constraint_violation",
                 step("When", "I create a user with NULL name", "SELECT * FROM create_user('b@example.com', NULL)",
                      expect=_ERR("23502"))),
        scenario("User lifecycle", "lifecycle", _NEW_USER,
                 step("When", "I read the user", "SELECT name FROM get_user({{user_id}})", expect={"rows": [["Ann"]]}),
                 step("And", "I rename the user", "SELECT name FROM update_user({{user_id}}, 'a@example.com', 'Anna')",
                      expect={"rows": [["Anna"]]}),
                 step("And", "I delete the user", "SELECT delete_user({{user_id}})", expect={"rows": [[True]]}),
                 step("Then", "the user is gone", "SELECT count(*) FROM get_user({{user_id}})", expect={"rows": [[0]]})),
    ]
    if include_missing_update:
        users.append(scenario(
            "Updating a missing user fails", "constraint_violation", _NEW_USER,
            step("And", "the user is deleted", "SELECT delete_user({{user_id}})"),
            step("When", "I update the deleted user", "SELECT * FROM update_user({{user_id}}, 'x@example.com', 'X')",
                 expect=_ERR("P0002"))))
    orders = [
        scenario("Order reduces stock and stores items", "happy_path", _NEW_USER, _NEW_PRODUCT(5),
                 step("When", "I order 2 units", f"SELECT id FROM create_order({{{{user_id}}}}, {_ORDER_JSON(2)})",
                      capture={"order_id": 0}),
                 step("Then", "stock is reduced", "SELECT stock FROM products WHERE id = {{product_id}}", expect={"rows": [[3]]}),
                 step("And", "the line stores the price",
                      "SELECT quantity, unit_price FROM order_items WHERE order_id = {{order_id}}",
                      expect={"rows": [[2, "10.00"]]})),
        scenario("Order for a non-existent user is rejected", "constraint_violation", _NEW_USER, _NEW_PRODUCT(5),
                 step("And", "the user is deleted", "SELECT delete_user({{user_id}})"),
                 step("When", "I order for the deleted user", f"SELECT * FROM create_order({{{{user_id}}}}, {_ORDER_JSON(1)})",
                      expect=_ERR("23503"))),
        scenario("Insufficient stock leaves no partial order", "transaction", _NEW_USER, _NEW_PRODUCT(1),
                 step("When", "I order more than the stock", f"SELECT * FROM create_order({{{{user_id}}}}, {_ORDER_JSON(2)})",
                      expect=_ERR("P0001")),
                 step("Then", "no order exists", "SELECT count(*) FROM orders", expect={"rows": [[0]]}),
                 step("And", "stock is unchanged", "SELECT stock FROM products WHERE id = {{product_id}}", expect={"rows": [[1]]})),
        scenario("Deleting an order cascades to its items", "relationship", _NEW_USER, _NEW_PRODUCT(5),
                 step("And", "an order exists", f"SELECT id FROM create_order({{{{user_id}}}}, {_ORDER_JSON(1)})",
                      capture={"order_id": 0}),
                 step("When", "I delete the order", "DELETE FROM orders WHERE id = {{order_id}}"),
                 step("Then", "its items are gone", "SELECT count(*) FROM order_items WHERE order_id = {{order_id}}",
                      expect={"rows": [[0]]})),
        scenario("A purchased product cannot be deleted", "relationship", _NEW_USER, _NEW_PRODUCT(5),
                 step("And", "an order exists", f"SELECT id FROM create_order({{{{user_id}}}}, {_ORDER_JSON(1)})"),
                 step("When", "I delete the product", "DELETE FROM products WHERE id = {{product_id}}", expect=_ERR("23503"))),
        scenario("Negative price is rejected", "boundary",
                 step("When", "I create a product with a negative price", "SELECT * FROM create_product('Bad', -1, 1)",
                      expect=_ERR("23514"))),
        zero_quantity_scenario(zero_state),
    ]
    return {"features": [{"name": "User management", "scenarios": users},
                         {"name": "Order placement", "scenarios": orders}]}


def fixed_zero_quantity_fix() -> dict:
    return {"features": [{"name": "Order placement", "scenarios": [zero_quantity_scenario("23514")]}]}
