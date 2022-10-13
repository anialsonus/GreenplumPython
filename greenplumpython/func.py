"""
This module creates a Python object Func which able creation and calling of Greenplum UDF and UDA.
"""
import functools
import inspect
import re
import textwrap
from typing import Any, Callable, Dict, Optional, Set, Tuple
from uuid import uuid4

from greenplumpython.db import Database
from greenplumpython.expr import Column, Expr
from greenplumpython.group import TableGroupingSets
from greenplumpython.table import Table
from greenplumpython.type import primitive_type_map, to_pg_const, to_pg_type


class FunctionExpr(Expr):
    """
    Inherited from :class:`~expr.Expr`.

    A Function object associated with a Greenplum function which can be called and applied to
    Greenplum data.
    """

    def __init__(
        self,
        func: "_AbstractFunction",
        args: Tuple[Any] = [],
        group_by: Optional[TableGroupingSets] = None,
        table: Optional[Table] = None,
        db: Optional[Database] = None,
    ) -> None:
        if table is None and group_by is not None:
            table = group_by.table
        for arg in args:
            if isinstance(arg, Expr) and arg.table is not None:
                if table is None:
                    table = arg.table
                elif table.name != arg.table.name:
                    raise Exception("Cannot pass arguments from more than one tables")
        super().__init__(table=table, db=db)
        self._func = func
        self._args = args
        self._group_by = group_by

    def bind(
        self,
        group_by: Optional[TableGroupingSets] = None,
        table: Optional[Table] = None,
        db: Optional[Database] = None,
    ):
        return FunctionExpr(
            self._func,
            self._args,
            group_by=group_by,
            table=table,
            db=db if db is not None else self._db,
        )

    def serialize(self) -> str:
        self.function.create_in_db(self._db)
        args_string = (
            ",".join(
                [
                    str(arg) if isinstance(arg, Expr) else to_pg_const(arg)
                    for arg in self._args
                    if arg is not None
                ]
            )
            if any(self._args)
            else ""
        )
        return f"{self._func.qualified_name}({args_string})"

    def to_table(self) -> Table:
        """
        Returns the result table of the self function applied on args, with potential GROUP BY if
        it is an Aggregation function.
        """
        self.function.create_in_db(self._db)
        from_caluse = f"FROM {self.table.name}" if self.table is not None else ""
        group_by_clause = self._group_by.clause() if self._group_by is not None else ""
        parents = [self.table] if self.table is not None else []
        assert (
            self.function.returning_composite is not None
        ), "Whether returning composite is required to call the function."
        if self.function.returning_composite and self._as_name is None:
            self._as_name = "func_" + uuid4().hex
        grouping_col_names = self._group_by.flatten() if self._group_by is not None else None
        # FIXME: The names of GROUP BY exprs can collide with names of fields in
        # the comosite type, making the column names of the resulting table not
        # unique. This can be mitigated after we implement table column
        # inference by raising an error when the function gets called.
        grouping_cols = (
            [Column(name, self._table).serialize() for name in grouping_col_names]
            if grouping_col_names is not None
            else None
        )
        orig_func_table = Table(
            " ".join(
                [
                    f"SELECT {str(self)}",
                    ("," + ",".join(grouping_cols)) if grouping_cols is not None else "",
                    from_caluse,
                    group_by_clause,
                ]
            ),
            db=self._db,
            parents=parents,
        )
        # We use 2 `Table`s here because on GPDB 6X and PostgreSQL <= 9.6, a
        # function returning records that contains more than one attributes
        # will be called multiple times if we do
        # ```sql
        # SELECT (func(a, b)).* FROM t;
        # ```
        # which might cause performance issue. To workaround we need to do
        # ```sql
        # WITH func_call AS (
        #     SELECT func(a, b) AS result FROM t
        # )
        # SELECT (result).* FROM func_call;
        # ```
        rebased_grouping_cols = (
            [Column(name, orig_func_table).serialize() for name in grouping_col_names]
            if grouping_col_names is not None
            else None
        )
        results = (
            "*"
            if not self.function.returning_composite
            else f"({self._as_name}).*"
            if rebased_grouping_cols is None
            else f"{(','.join(rebased_grouping_cols))}, ({self._as_name}).*"
        )
        return Table(
            f"SELECT {str(results)} FROM {orig_func_table.name}",
            db=self._db,
            parents=[orig_func_table],
        )

    @property
    def function(self) -> "_AbstractFunction":
        return self._func


class ArrayFunctionExpr(FunctionExpr):
    """
    Inherited from :class:`FunctionExpr`.

    Specialized for an Array Function.
    It will array aggregate all the columns given by the user.
    """

    def serialize(self) -> str:
        args_string_list = []
        args_string = ""
        grouping_col_names = self._group_by.flatten() if self._group_by is not None else None
        grouping_cols = (
            {Column(name, self._table) for name in grouping_col_names}
            if grouping_col_names is not None
            else None
        )
        if any(self._args):
            for i in range(len(self._args)):
                if self._args[i] is None:
                    continue
                if isinstance(self._args[i], Expr):
                    if grouping_cols is None or self._args[i] not in grouping_cols:
                        s = f"array_agg({str(self._args[i])})"  # type: ignore
                    else:
                        s = str(self._args[i])  # type: ignore
                else:
                    s = to_pg_const(self._args[i])  # type: ignore
                args_string_list.append(s)
            args_string = ",".join(args_string_list)
        return f"{self._func.qualified_name}({args_string})"

    def bind(
        self,
        group_by: Optional[TableGroupingSets] = None,
        table: Optional[Table] = None,
        db: Optional[Database] = None,
    ):
        return ArrayFunctionExpr(
            self._func,
            self._args,
            group_by=group_by,
            table=table,
            db=db if db is not None else self._db,
        )


# The parent class for all database functions.
# It is not a Callable by design to prevent misuse.
class _AbstractFunction:
    def __init__(
        self,
        wrapped_func: Optional[Callable[..., Any]],
        name: Optional[str],
        schema: Optional[str],
        returning_composite: Optional[bool],
    ) -> None:
        NAMEDATALEN = 64  # See definition in PostgreSQL
        if not (
            (name is not None and len(name) < NAMEDATALEN)
            or (wrapped_func is not None and len(wrapped_func.__name__) < NAMEDATALEN)
        ):
            raise Exception(f"Function name should be shorter than {NAMEDATALEN} bytes.")
        qualified_name = (
            (name if schema is None else f"{schema}.{name}")
            if name is not None
            else f"pg_temp.{wrapped_func.__name__}"
            if wrapped_func is not None
            else None
        )
        assert qualified_name is not None
        assert (
            qualified_name not in _global_scope
        ), f'Function named "{qualified_name}" has been defined before.'
        self._qualified_name = qualified_name
        _global_scope[qualified_name] = self
        self._returning_composite = returning_composite

    @property
    def qualified_name(self) -> str:
        return self._qualified_name

    @property
    def returning_composite(self) -> Optional[bool]:
        return self._returning_composite

    def create_in_db(self, _: Database) -> None:
        raise NotImplementedError("Cannot create abstract function in database")


_global_scope: Dict[str, _AbstractFunction] = {}


class NormalFunction(_AbstractFunction):
    def __init__(
        self,
        wrapped_func: Optional[Callable[..., Any]] = None,
        name: Optional[str] = None,
        schema: Optional[str] = None,
        returning_composite: Optional[bool] = None,
        language_handler: str = "plpython3u",
    ) -> None:
        super().__init__(wrapped_func, name, schema, returning_composite)
        self._created_in_dbs: Optional[Set[Database]] = set() if wrapped_func is not None else None
        self._wrapped_func = wrapped_func
        self._language_handler = language_handler

    def unwrap(self) -> Callable[..., Any]:
        """
        Get the wrapped Python function in the database function.
        """
        assert self._wrapped_func is not None, "No Python function is wrapped inside."
        return self._wrapped_func

    def create_in_db(self, db: Database) -> None:
        if self._wrapped_func is None:  # Function has already existed.
            return
        assert self._created_in_dbs is not None
        if db not in self._created_in_dbs:
            func_sig = inspect.signature(self._wrapped_func)
            func_args = ",".join(
                [
                    f"{param.name} {to_pg_type(param.annotation, db)}"
                    for param in func_sig.parameters.values()
                ]
            )
            # -- Loading Python code of Function
            # FIXME: include things in func.__closure__
            func_lines = textwrap.dedent(inspect.getsource(self._wrapped_func)).split("\n")
            func_body = "\n".join([line for line in func_lines if re.match(r"^\s", line)])
            return_type = to_pg_type(func_sig.return_annotation, db, for_return=True)
            self._returning_composite = func_sig.return_annotation not in primitive_type_map
            assert (
                db.execute(
                    (
                        f"CREATE FUNCTION {self._qualified_name} ({func_args}) "
                        f"RETURNS {return_type} "
                        f"AS $$\n"
                        f"{textwrap.dedent(func_body)} $$"
                        f"LANGUAGE {self._language_handler};"
                    ),
                    has_results=False,
                )
                is None
            )
            self._created_in_dbs.add(db)

    def __call__(self, *args: Any, db: Optional[Database] = None) -> FunctionExpr:
        return FunctionExpr(self, args, db=db)


def function(
    name: str, schema: Optional[str] = None, returning_composite: bool = False
) -> NormalFunction:
    """
    A wrap in order to call function

    Example:
        .. code-block::  Python
            generate_series = gp.function("generate_series", db)

    """
    if name not in _global_scope:
        return NormalFunction(name=name, schema=schema, returning_composite=returning_composite)
    func = _global_scope[name]
    assert isinstance(func, NormalFunction), f'"{name}" is not a normal function'
    return func


class AggregateFunction(_AbstractFunction):
    def __init__(
        self,
        transition_func: Optional[NormalFunction] = None,
        name: Optional[str] = None,
        schema: Optional[str] = None,
        returning_composite: Optional[bool] = None,
    ) -> None:
        super().__init__(
            transition_func.unwrap() if transition_func is not None else None,
            name,
            schema,
            returning_composite,
        )
        self._transition_func = transition_func
        self._created_in_dbs: Optional[Set[Database]] = (
            set() if transition_func is not None else None
        )

    @property
    def transition_function(self) -> NormalFunction:
        assert (
            self._transition_func is not None
        ), f'Transition function of the aggregate function "{self.qualified_name}" is unknown.'
        return self._transition_func

    def create_in_db(self, db: Database) -> None:
        if self._transition_func is None:  # Function has already existed.
            return
        assert self._created_in_dbs is not None
        if db not in self._created_in_dbs:
            self._transition_func.create_in_db(db)
            sig = inspect.signature(self.transition_function.unwrap())
            param_list = iter(sig.parameters.values())
            state_param = next(param_list)
            args_string = ",".join(
                [f"{param.name} {to_pg_type(param.annotation, db)}" for param in param_list]
            )
            self._returning_composite = sig.return_annotation not in primitive_type_map
            # -- Creation of UDA in Greenplum
            db.execute(
                f"""
                CREATE AGGREGATE {self.qualified_name} ({args_string}) (
                    SFUNC = {self.transition_function.qualified_name},
                    STYPE = {to_pg_type(state_param.annotation, db)}
                )
                """,
                has_results=False,
            )
            self._created_in_dbs.add(db)

    def __call__(
        self,
        *args: Any,
        group_by: Optional[TableGroupingSets] = None,
        db: Optional[Database] = None,
    ) -> FunctionExpr:
        return FunctionExpr(self, args, db=db, group_by=group_by)


def aggregate_function(
    name: str, schema: Optional[str] = None, returning_composite: bool = False
) -> AggregateFunction:
    """
    A wrap in order to call an aggregate function

    Example:
        .. code-block::  Python
            count = gp.aggregate_function("count", db=db)
    """
    if name not in _global_scope:
        return AggregateFunction(name=name, schema=schema, returning_composite=returning_composite)
    func = _global_scope[name]
    assert isinstance(func, AggregateFunction), f'"{name}" is not an aggregate function'
    return func


# FIXME: Add test cases for optional parameters
def create_function(
    wrapped_func: Optional[Callable[..., Any]] = None, language_handler: str = "plpython3u"
) -> NormalFunction:
    """
    Creates a User Defined Function (UDF) in database from the given Python
    function.

    Args:
        wrapped_func : the Python function to be wrapped into a database function
        language_handler language handler to run the UDF, defaults to plpython3u,
            will also support plcontainer later.

    Returns:
        a database function

    Example:
        .. code-block::  Python

            @gp.create_function
            def multiply(a: int, b: int) -> int:
                return a * b

            multiply(series["a"], series["b"])

    """
    # If user needs extra parameters when creating a function
    if wrapped_func is None:
        return functools.partial(create_function, language_handler=language_handler)
    return NormalFunction(wrapped_func=wrapped_func, language_handler=language_handler)


# FIXME: Add test cases for optional parameters
def create_aggregate(
    transition_func: Optional[Callable[..., Any]] = None, language_handler: str = "plpython3u"
) -> AggregateFunction:
    """
    Creates a User Defined Aggregate (UDA) in Database using the given Python
    function as the state transition function.

    Args:
        transition_func : python function
        language_handler : language handler to run the aggregate function in database,
            defaults to plpython3u, will also support plcontainer later.

    Returns:
        A database aggregate function.

    Example:
        .. code-block::  Python

            @gp.create_aggregate
            def my_sum(result: int, val: int) -> int:
                if result is None:
                    return val
                return result + val

            rows = [(1,) for _ in range(10)]
            numbers = gp.values(rows, db=db, column_names=["val"])
            my_sum(numbers["val"])

    """
    # If user needs extra parameters when creating a function
    if transition_func is None:
        return functools.partial(create_aggregate, language_handler=language_handler)
    return AggregateFunction(
        transition_func=NormalFunction(
            transition_func,
            name="func_" + uuid4().hex,
            schema="pg_temp",
            language_handler=language_handler,
        )
    )


class ArrayFunction(NormalFunction):
    def __call__(
        self,
        *args: Any,
        group_by: Optional[TableGroupingSets] = None,
        db: Optional[Database] = None,
    ) -> ArrayFunctionExpr:
        return ArrayFunctionExpr(self, args=args, group_by=group_by, db=db)


# FIXME: Add test cases for optional parameters
def create_array_function(
    wrapped_func: Optional[Callable[..., Any]] = None, language_handler: str = "plpython3u"
) -> ArrayFunction:
    """
    Creates a User Defined Array Function (UDF) in database from the given Python
    function.

    Args:
        wrapped_func: python function
        language_handler: language handler to run the UDF, defaults to plpython3u,
            will also support plcontainer later.

    Returns:
        Callable : FunctionCall

    Example:
            .. code-block::  Python

                @gp.create_array_function
                def my_sum(val_list: List[int]) -> int:
                    return sum(val_list)

                rows = [(1, i % 2 == 0) for i in range(10)]
                numbers = gp.values(rows, db=db, column_names=["val", "is_even"])
                results = list(
                    my_sum(numbers["val"], group_by=numbers.group_by("is_even")).rename=("result")
                    .to_table()
                    .fetch()
                )
    """
    # If user needs extra parameters when creating a function
    if wrapped_func is None:
        return functools.partial(create_array_function, language_handler=language_handler)
    return ArrayFunction(wrapped_func=wrapped_func, language_handler=language_handler)
