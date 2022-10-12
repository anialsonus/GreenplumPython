import inspect
import string
from os import environ
from typing import List

import pytest

import greenplumpython as gp
from greenplumpython.builtin.function import generate_series
from greenplumpython.func import AggregateFunction, NormalFunction
from tests import db


@pytest.fixture
def series(db: gp.Database):
    rows = [(i, i) for i in range(10)]
    return gp.values(rows, db=db, column_names=["a", "b"])


def test_plain_func(db: gp.Database):
    version = gp.function("version")
    for row in version(db=db).to_table().fetch():
        assert "Greenplum" in row["version"] or row["version"].startswith("PostgreSQL")


def test_set_returning_func(db: gp.Database):
    results = generate_series(0, 9, db=db).rename("id").to_table().fetch()
    assert sorted([row["id"] for row in results]) == list(range(10))


# TODO: Test other data types
def test_create_func(db: gp.Database):
    @gp.create_function
    def add(a: int, b: int) -> int:
        return a + b

    for row in db.apply(lambda: add(1, 2)).rename("result").to_table().fetch():
        assert row["result"] == 1 + 2
        assert row["result"] == add.unwrap()(1, 2)


def test_create_func_multiline(db: gp.Database):
    @gp.create_function
    def my_max(a: int, b: int) -> int:
        if a > b:
            return a
        else:
            return b

    for row in db.apply(lambda: my_max(1, 2)).rename("result").to_table().fetch():
        assert row["result"] == max(1, 2)
        assert row["result"] == my_max.unwrap()(1, 2)


# fmt: off
def test_create_func_tab_indent(db: gp.Database):
	@gp.create_function
	def my_min(a: int, b: int) -> int:
		if a < b:
			return a
		else:
			return b

	for row in db.apply(lambda: my_min(1, 2)).rename("result").to_table().fetch():
		assert row["result"] == min(1, 2)
		assert row["result"] == my_min.unwrap()(1, 2)
# fmt: on


def test_func_on_one_column(db: gp.Database):
    rows = [(i,) for i in range(-10, 0)]
    series = gp.values(rows, db=db, column_names=["id"])
    abs = gp.function("abs")
    results = abs(series["id"], db=db).to_table().fetch()
    assert sorted([row["abs"] for row in results]) == list(range(1, 11))


def test_func_on_multi_columns(db: gp.Database, series: gp.Table):
    @gp.create_function
    def multiply(a: int, b: int) -> int:
        return a * b

    results = multiply(series["a"], series["b"]).rename("result").to_table().fetch()
    assert sorted([row["result"] for row in results]) == [i * i for i in range(10)]


def test_func_on_more_than_one_table(db: gp.Database):
    div = gp.function("div")
    rows = [(1,) for _ in range(10)]
    t1 = gp.values(rows, db=db, column_names=["i"])
    t2 = gp.values(rows, db=db, column_names=["i"])
    with pytest.raises(Exception) as exc_info:
        div(t1["i"], t2["i"], db=db)
    # FIXME: Create more specific exception classes and remove this
    assert "Cannot pass arguments from more than one tables" == str(exc_info.value)


def test_simple_agg(db: gp.Database):
    rows = [(i,) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    count = gp.aggregate_function("count")

    results = list(count(numbers["val"], db=db).to_table().fetch())
    assert len(results) == 1 and results[0]["count"] == 10


def test_agg_group_by(db: gp.Database):
    rows = [(i, i % 2 == 0) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val", "is_even"])
    count = gp.aggregate_function("count")

    # FIXME: Remove extraneous rename() in group_by() after spearating Expr
    # with NamedExpr.
    results = list(
        count(
            numbers["val"],
            group_by=numbers.group_by(lambda t: t["is_even"].rename("is_even")),
            db=db,
        )
        .to_table()
        .fetch()
    )
    assert len(results) == 2
    for row in results:
        assert ("is_even" in row) and (row["is_even"] is not None) and (row["count"] == 5)


def test_agg_group_by_multi_columns(db: gp.Database):
    rows = [(i, i % 2 == 0, i % 3 == 0) for i in range(6)]  # 0, 1, 2, 3, 4, 5
    numbers = gp.values(rows, db=db, column_names=["val", "is_even", "is_multiple_of_3"])
    count = gp.aggregate_function("count")

    results = list(
        count(
            numbers["val"],
            group_by=numbers.group_by(
                lambda t: [
                    t["is_even"].rename("is_even"),
                    t["is_multiple_of_3"].rename("is_multiple_of_3"),
                ]
            ),
            db=db,
        )
        .to_table()
        .fetch()
    )
    assert len(results) == 4  # 2 attributes * 2 possible values per attribute
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


@gp.create_aggregate
def my_sum(result: int, val: int) -> int:
    if result is None:
        return val
    return result + val


def test_create_agg(db: gp.Database):
    rows = [(1,) for _ in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    results = list(my_sum(numbers["val"]).rename("result").to_table().fetch())
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
        manhattan_distance(vectors["a"], vectors["b"]).rename("result").to_table().fetch()
    )
    assert len(results) == 1 and results[0]["result"] == 10


def test_func_long_name(db: gp.Database):
    def loooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooong() -> None:
        return

    with pytest.raises(Exception) as exc_info:
        gp.create_function(
            loooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooong
        )
    # FIXME: Create more specific exception classes and remove this
    assert "Function name should be shorter than 64 bytes." == str(exc_info.value)


def test_create_func_with_optional_param(db: gp.Database):
    @gp.create_function(language_handler="plcontainer")
    def func_opt_param() -> None:
        return

    assert isinstance(func_opt_param, NormalFunction)


def test_create_func_with_optional_param(db: gp.Database):
    @gp.create_aggregate(language_handler="plcontainer")
    def agg_opt_param() -> None:
        return

    assert isinstance(agg_opt_param, AggregateFunction)


@gp.create_array_function
def my_sum_array(val_list: List[int]) -> int:
    return sum(val_list)


def test_array_func(db: gp.Database):
    rows = [(1,) for _ in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    results = list(my_sum_array(numbers["val"]).rename("result").to_table().fetch())
    assert len(results) == 1 and results[0]["result"] == 10


def test_array_func_group_by(db: gp.Database):
    rows = [(1, i % 2 == 0) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val", "is_even"])
    results = list(
        my_sum_array(
            numbers["val"], group_by=numbers.group_by(lambda t: t["is_even"].rename("is_even"))
        )
        .rename("result")
        .to_table()
        .fetch()
    )
    assert len(results) == 2
    assert list(list(results)[0].keys()) == ["result", "is_even"]
    for row in results:
        assert ("is_even" in row) and (row["is_even"] is not None) and (row["result"] == 5)


def test_array_func_group_by_return_composite(db: gp.Database):
    class array_sum:
        _sum: int
        _count: int

    @gp.create_array_function
    def my_count_sum(val_list: List[int]) -> array_sum:
        return {"_sum": sum(val_list), "_count": len(val_list)}

    # fmt: off
    rows = [(1, "a",), (1, "a",), (1, "b",), (1, "a",), (1, "b",), (1, "b",)]
    # fmt: on
    numbers = gp.values(rows, db=db, column_names=["val", "lab"])
    ret = (
        my_count_sum(numbers["val"], group_by=numbers.group_by(lambda t: t["lab"].rename("lab")))
        .to_table()
        .fetch()
    )
    assert sorted(list(ret)[0].keys()) == sorted(["_sum", "_count", "lab"])
    for row in list(ret):
        assert row["_sum"] == 3
        assert row["_count"] == 3


def test_func_return_composite_type(db: gp.Database):
    class Person:
        _first_name: str
        _last_name: str

    @gp.create_function
    def create_person(first: str, last: str) -> Person:
        return {"_first_name": first, "_last_name": last}

    for row in create_person("Amy", "An", db=db).to_table().fetch():
        assert row["_first_name"] == "Amy" and row["_last_name"] == "An"


class Pair:
    _num: int
    _next: int


@gp.create_function
def create_pair(num: int) -> Pair:
    return {"_num": num, "_next": num + 1}


def test_func_composite_type_column(db: gp.Database):
    rows = [(i,) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    for row in create_pair(numbers["val"], db=db).to_table().fetch():
        assert row["_next"] == row["_num"] + 1


def test_func_composite_type_setof(db: gp.Database):
    class Pair:
        _num: int
        _next: int

    @gp.create_function
    def create_pair_tuple(num: int) -> List[Pair]:
        return [(num, num + 1) for _ in range(5)]

    rows = [(i,) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    ret = list(create_pair_tuple(numbers["val"], db=db).to_table().fetch())
    assert len(ret) == 50
    dict_record = {i: 0 for i in range(10)}
    for row in ret:
        dict_record[row["_num"]] += 1
        assert row["_next"] == row["_num"] + 1
    for key in dict_record:
        assert dict_record[key] == 5


class Stat:
    sum: int
    count: int


@gp.create_array_function
def my_stat(val_list: List[int]) -> Stat:
    return {"sum": sum(val_list), "count": len(val_list)}


def test_array_func_composite_type(db: gp.Database):
    rows = [(i,) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    ret = list(my_stat(numbers["val"], db=db).to_table().fetch())
    for row in ret:
        assert row["sum"] == sum(list([i for i in range(10)])) and row["count"] == len(rows)


def test_func_apply_single_column(db: gp.Database):
    rows = [(i,) for i in range(-10, 0)]
    series = gp.values(rows, db=db, column_names=["id"])
    abs = gp.function("abs")
    result = series.apply(lambda t: abs(t["id"])).to_table().fetch()
    assert len(list(result)) == 10
    for row in result:
        assert row["abs"] >= 0


@gp.create_function
def label(type_or_type: str, num: int) -> str:
    return type_or_type + str(num)


def test_func_apply_const_and_column(db: gp.Database):
    rows = [(i,) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    result = numbers.apply(lambda t: label("label", t["val"])).to_table().fetch()
    assert len(list(result)) == 10
    for row in result:
        assert row["label"].startswith("label")


def test_func_apply_join(db: gp.Database):
    # fmt: off
    rows1 = [(1, "a1",), (2, "a2",), (3, "a3",)]
    rows2 = [(1, "b1",), (2, "b2",), (3, "b3",)]
    # fmt: on
    t1 = gp.values(rows1, db=db, column_names=["id1", "n1"])
    t2 = gp.values(rows2, db=db, column_names=["id2", "n2"])
    ret = t1.join(
        t2, cond=lambda t1, t2: t1["id1"] == t2["id2"], self_columns={"id1"}, other_columns={"n2"}
    )
    result = ret.apply(lambda t: label(t["n2"], t["id1"])).to_table().fetch()
    for row in list(result):
        assert row["label"][1] == row["label"][2]


def test_func_composite_type_column_apply(db: gp.Database):
    rows = [(i,) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])
    for row in numbers.apply(lambda tab: create_pair(tab["val"])).to_table().fetch():
        assert row["_next"] == row["_num"] + 1


def test_array_func_apply(db: gp.Database):
    rows = [(1,) for _ in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])

    results = list(numbers["val"].apply(my_sum_array).rename("my_sum").to_table().fetch())
    assert len(results) == 1 and results[0]["my_sum"] == 10


def test_array_func_group_by_composite_apply(db: gp.Database):
    rows = [(1, i % 2 == 0) for i in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val", "is_even"])
    results = list(
        numbers.group_by(lambda t: t["is_even"].rename("is_even"))
        .apply(lambda tab: my_stat(tab["val"]))
        .to_table()
        .fetch()
    )
    assert sorted(list(results)[0].keys()) == sorted(["sum", "count", "is_even"])
    for row in results:
        assert all(
            ["is_even" in row, row["is_even"] is not None, row["sum"] == 5, row["count"] == 5]
        )


@gp.create_array_function
def my_sum_const(label: str, val_list: List[int], initial: int) -> str:
    return label + " : " + str(sum(val_list) + initial)


def test_array_func_const_apply(db: gp.Database):
    rows = [(1,) for _ in range(10)]
    numbers = gp.values(rows, db=db, column_names=["val"])

    results = list(
        numbers.apply(lambda tab: my_sum_const("sum", tab["val"], 5))
        .rename("my_sum")
        .to_table()
        .fetch()
    )
    assert len(results) == 1 and results[0]["my_sum"] == "sum : 15"


def test_array_func_group_by_attribute(db: gp.Database):
    # fmt: off
    rows = [("a", i, 5,) for i in range(10)]
    # fmt: on
    numbers = gp.values(rows, db=db, column_names=["label", "val", "initial"])
    results = list(
        numbers.group_by(lambda t: [t["label"].rename("label"), t["initial"].rename("initial")])
        .apply(lambda tab: my_sum_const(tab["label"], tab["val"], tab["initial"]))
        .rename("my_sum")
        .to_table()
        .fetch()
    )
    assert len(results) == 1 and results[0]["my_sum"] == "a : 50"


def test_func_return_list_composite(db: gp.Database):
    class ShoppingCart:
        customer: str
        items: List[str]

    @gp.create_function
    def add_to_cart(customer: str, items: List[str]) -> ShoppingCart:
        return {"customer": customer, "items": items}

    results = db.apply(lambda: add_to_cart("alice", ["apple"])).to_table().fetch()
    for row in results:
        assert row["customer"] == "alice" and row["items"] == ["apple"]