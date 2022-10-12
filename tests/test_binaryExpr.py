from typing import Callable

import pytest

import greenplumpython as gp
from greenplumpython.builtin.function import generate_series
from tests import db


def test_expr_bin_equal_int(db: gp.Database):
    rows = [(1,), (2,), (3,), (2,)]
    t = gp.values(rows, db=db).save_as("temp1", temp=True, column_names=["id"])
    b1: Callable[[gp.Table], gp.Expr] = lambda t: t["id"] == 2
    assert str(b1(t)) == "(temp1.id = 2)"
    ret = t[b1].fetch()
    assert len(list(ret)) == 2


def test_expr_bin_equal_str(db: gp.Database):
    rows = [("aaa",), ("bbb",), ("ccc",)]
    t = gp.values(rows, db=db).save_as("temp2", temp=True, column_names=["id"])
    b2: Callable[[gp.Table], gp.Expr] = lambda t: t["id"] == "aaa"
    assert str(b2(t)) == "(temp2.id = 'aaa')"
    ret = t[b2].fetch()
    assert len(list(ret)) == 1


def test_expr_bin_equal_none(db: gp.Database):
    rows = [("aa",), (None,), ("cc",)]
    t = gp.values(rows, db=db).save_as("temp3", temp=True, column_names=["id"])
    b3: Callable[[gp.Table], gp.Expr] = lambda t: t["id"] == None
    assert str(b3(t)) == "(temp3.id IS NULL)"
    ret = t[b3].fetch()
    assert len(list(ret)) == 1


def test_expr_bin_equal_2expr(db: gp.Database):
    rows = [(1,), (2,), (3,)]
    t1 = gp.values(rows, db=db).save_as("temp4", temp=True, column_names=["id"])
    t2 = gp.values(rows, db=db).save_as("temp5", temp=True, column_names=["id"])
    b4: Callable[[gp.Table, gp.Table], gp.Expr] = lambda t1, t2: t1["id"] == t2["id"]
    assert str(b4(t1, t2)) == "(temp4.id = temp5.id)"
    ret = t1.join(t2, cond=b4).fetch()
    assert len(list(ret)) == 3


def test_expr_bin_equal_bool(db: gp.Database):
    rows = [(True,), (False,), (False,), (True,)]
    t = gp.values(rows, db=db).save_as("temp1", temp=True, column_names=["id"])
    b5: Callable[[gp.Table], gp.Expr] = lambda t: t["id"] == True
    assert str(b5(t)) == "(temp1.id = true)"
    ret = t[b5].fetch()
    assert len(list(ret)) == 2


@pytest.fixture
def table_num(db: gp.Database):
    t = db.apply(lambda: generate_series(0, 9)).rename("id").to_table()
    return t


def test_expr_bin_lt(table_num: gp.Table):
    ret = table_num[lambda t: t["id"] < 3].fetch()
    assert len(list(ret)) == 3


def test_expr_bin_le(table_num: gp.Table):
    ret = table_num[lambda t: t["id"] <= 3].fetch()
    assert len(list(ret)) == 4


def test_expr_bin_gt(table_num: gp.Table):
    ret = table_num[lambda t: t["id"] > 3].fetch()
    assert len(list(ret)) == 6


def test_expr_bin_ge(table_num: gp.Table):
    ret = table_num[lambda t: t["id"] >= 3].fetch()
    assert len(list(ret)) == 7


def test_expr_bin_ne(table_num: gp.Table):
    ret = table_num[lambda t: t["id"] != 3].fetch()
    assert len(list(ret)) == 9


def test_expr_bin_and(table_num: gp.Table):
    ret = table_num[lambda t: (t["id"] >= 3) & (t["id"] < 8)].fetch()
    assert len(list(ret)) == 5
    for row in ret:
        assert 3 <= row["id"] < 8


def test_expr_bin_or(db: gp.Database):
    rows = [(1,), (2,), (3,), (-2,)]
    t = gp.values(rows, db=db, column_names=["id"])
    ret = t[lambda t: (t["id"] >= 3) | (t["id"] < 0)].fetch()
    assert len(list(ret)) == 2
    for row in ret:
        assert 3 <= row["id"] or row["id"] < 0


def test_table_like(db: gp.Database):
    rows = [("aaa",), ("bba",), ("acac",)]
    t = gp.values(rows, db=db, column_names=["id"])
    result = t[lambda t: t["id"].like(r"a%")].fetch()
    assert len(list(result)) == 2
    result = t[lambda t: t["id"].like(r"%a")].fetch()
    assert len(list(result)) == 2
    result = t[lambda t: t["id"].like(r"%a%")].fetch()
    assert len(list(result)) == 3
    result = t[lambda t: t["id"].like(r"a%c")].fetch()
    assert len(list(result)) == 1
    result = t[lambda t: t["id"].like(r"_a%")].fetch()
    assert len(list(result)) == 1


def test_table_add(db: gp.Database):
    nums = gp.values([(i,) for i in range(10)], db, column_names=["num"])
    results = nums.extend("add", lambda t: t["num"] + 1)

    for row in results.fetch():
        assert row["num"] + 1 == row["add"]


def test_table_sub(db: gp.Database):
    nums = gp.values([(i,) for i in range(10)], db, column_names=["num"])
    results = nums.extend("sub", lambda t: t["num"] - 1)

    for row in results.fetch():
        assert row["num"] - 1 == row["sub"]


def test_table_mul(db: gp.Database):
    nums = gp.values([(i,) for i in range(10)], db, column_names=["num"])
    results = nums.extend("mul", lambda t: t["num"] * t["num"])

    for row in results.fetch():
        assert row["num"] ** 2 == row["mul"]


def test_table_true_div(db: gp.Database):
    nums = gp.values([(i,) for i in range(1, 10)], db, column_names=["num"])
    results = nums.extend("div", lambda t: t["num"] / t["num"])

    for row in results.fetch():
        assert row["div"] == 1


def test_table_true_div_integers(db: gp.Database):
    nums = gp.values([(i,) for i in range(4, 5)], db, column_names=["num"])
    results = nums.extend("div", lambda t: t["num"] / 2)

    for row in results.fetch():
        assert row["div"] == 2


def test_table_true_div_integer_float(db: gp.Database):
    nums = gp.values([(i,) for i in range(5, 8, 2)], db, column_names=["num"])
    float_type = gp.get_type("float", db)
    results = nums.extend("div", lambda t: float_type(t["num"]) / 2)

    for row in results.fetch():
        assert row["div"] == 2.5 or row["div"] == 3.5


def test_table_true_div_zero(db: gp.Database):
    nums = gp.values([(i,) for i in range(5)], db, column_names=["num"])
    with pytest.raises(Exception) as exc_info:
        nums.extend("div", lambda t: t["num"] / t["num"]).fetch()

    assert "division by zero\n" == str(exc_info.value)