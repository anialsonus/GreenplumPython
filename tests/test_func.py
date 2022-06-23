import inspect
from typing import List

import pytest

import greenplumpython as gp


@pytest.fixture
def db():
    db = gp.database(host="localhost", dbname="gpadmin")
    yield db
    db.close()


def test_plain_func(db: gp.Database):
    version = gp.function("version", db)
    for row in version().to_table().fetch():
        assert "Greenplum" in row["version"]


def test_set_returning_func(db: gp.Database):
    generate_series = gp.function("generate_series", db)
    results = generate_series(0, 9, as_name="id").to_table().fetch()
    assert sorted([row["id"] for row in results]) == list(range(10))


# TODO: Test other data types
def test_create_func(db: gp.Database):
    @gp.create_function
    def add(a: int, b: int) -> int:
        return a + b

    for row in add(1, 2, as_name="result", db=db).to_table().fetch():
        assert row["result"] == 1 + 2
        assert row["result"] == inspect.unwrap(add)(1, 2)


def test_create_func_multiline(db: gp.Database):
    @gp.create_function
    def my_max(a: int, b: int) -> int:
        if a > b:
            return a
        else:
            return b

    for row in my_max(1, 2, as_name="result", db=db).to_table().fetch():
        assert row["result"] == max(1, 2)
        assert row["result"] == inspect.unwrap(my_max)(1, 2)


# fmt: off
def test_create_func_tab_indent(db: gp.Database):
	@gp.create_function
	def my_min(a: int, b: int) -> int:
		if a < b:
			return a
		else:
			return b

	for row in my_min(1, 2, as_name="result", db=db).to_table().fetch():
		assert row["result"] == min(1, 2)
		assert row["result"] == inspect.unwrap(my_min)(1, 2)
# fmt: on


def test_func_on_one_column(db: gp.Database):
    rows = [(i,) for i in range(-10, 0)]
    series = gp.values(rows, db=db, column_names=["id"])
    abs = gp.function("abs", db=db)
    results = abs(series["id"]).to_table().fetch()
    assert sorted([row["abs"] for row in results]) == list(range(1, 11))


def test_func_on_multi_columns(db: gp.Database):
    @gp.create_function
    def multiply(a: int, b: int) -> int:
        return a * b

    rows = [(i, i) for i in range(10)]
    series = gp.values(rows, db=db, column_names=["a", "b"])
    results = multiply(series["a"], series["b"], as_name="result").to_table().fetch()
    assert sorted([row["result"] for row in results]) == [i * i for i in range(10)]


def test_func_on_more_than_one_table(db: gp.Database):
    div = gp.function("div", db=db)
    rows = [(1,) for _ in range(10)]
    t1 = gp.values(rows, db=db, column_names=["i"])
    t2 = gp.values(rows, db=db, column_names=["i"])
    with pytest.raises(Exception) as exc_info:
        div(t1["i"], t2["i"])
    # FIXME: Create more specific exception classes and remove this
    assert "Cannot pass arguments from more than one tables" == str(exc_info.value)


def test_simple_agg(db: gp.Database):
    rows = [(i,) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    count = gp.aggregate("count", db=db)

    results = list(count(numbers["val"]).to_table().fetch())
    assert len(results) == 1 and results[0]["count"] == 10


def test_agg_group_by(db: gp.Database):
    rows = [(i, i % 2 == 0) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val", "is_even"])
    count = gp.aggregate("count", db=db)

    results = list(count(numbers["val"], group_by=["is_even"]).to_table().fetch())
    assert len(results) == 2
    for row in results:
        assert ("is_even" in row) and (row["is_even"] is not None) and (row["count"] == 5)


def test_agg_group_by_multi_columns(db: gp.Database):
    rows = [(i, i % 2 == 0, i % 3 == 0) for i in range(6)]  # 0, 1, 2, 3, 4, 5
    numbers = gp.values(rows, db=db, column_names=["val", "is_even", "is_multiple_of_3"])
    count = gp.aggregate("count", db=db)

    results = list(
        count(numbers["val"], group_by=["is_even", "is_multiple_of_3"]).to_table().fetch()
    )
    assert len(results) == 2 * 2
    for row in results:
        assert (
            ("is_even" in row)
            and (row["is_even"] is not None)
            and ("is_multiple_of_3" in row)
            and (row["is_multiple_of_3"] is not None)
        )
        assert (
            (row["is_even"] and row["is_multiple_of_3"] and row["count"] == 1)  # 0
            or (row["is_even"] and not row["is_multiple_of_3"] and row["count"] == 2)  # 2, 4
            or (not row["is_even"] and row["is_multiple_of_3"] and row["count"] == 1)  # 3
            or (not row["is_even"] and not row["is_multiple_of_3"] and row["count"] == 2)  # 1, 5
        )


def test_create_agg(db: gp.Database):
    @gp.create_aggregate
    def my_sum(result: int, val: int) -> int:
        if result is None:
            return val
        return result + val

    rows = [(1,) for _ in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    results = list(my_sum(numbers["val"], as_name="result").to_table().fetch())
    assert len(results) == 1 and results[0]["result"] == 10


def test_create_agg_multi_args(db: gp.Database):
    @gp.create_aggregate
    def manhattan_distance(result: int, a: int, b: int) -> int:
        if result is None:
            return abs(a - b)
        return result + abs(a - b)

    rows = [(1, 2) for _ in range(10)]
    vectors = gp.values(rows, db=db, column_names=["a", "b"])
    results = list(
        manhattan_distance(vectors["a"], vectors["b"], as_name="result").to_table().fetch()
    )
    assert len(results) == 1 and results[0]["result"] == 10


def test_func_long_name(db: gp.Database):
    @gp.create_function
    def loooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooong() -> None:
        return

    with pytest.raises(Exception) as exc_info:
        loooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooong(db=db)
    # FIXME: Create more specific exception classes and remove this
    assert "Function name should be no longer than 64 bytes." == str(exc_info.value)


def test_array_func(db: gp.Database):
    @gp.create_array_function
    def my_sum(val_list: List[int]) -> int:
        return sum(val_list)

    rows = [(1,) for _ in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    results = list(my_sum(numbers["val"], as_name="result").to_table().fetch())
    assert len(results) == 1 and results[0]["result"] == 10


def test_array_func_group_by(db: gp.Database):
    @gp.create_array_function
    def my_sum(val_list: List[int]) -> int:
        return sum(val_list)

    rows = [(1, i % 2 == 0) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val", "is_even"])
    results = list(
        my_sum(numbers["val"], group_by=["is_even"], as_name="result").to_table().fetch()
    )
    assert len(results) == 2
    for row in results:
        assert ("is_even" in row) and (row["is_even"] is not None) and (row["result"] == 5)