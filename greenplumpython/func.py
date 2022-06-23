import functools
import inspect
import re
import textwrap
from typing import Callable, Iterable, Optional, Union
from uuid import uuid4

from .db import Database
from .expr import Expr
from .table import Table
from .type import to_pg_type


class FunctionCall(Expr):
    def __init__(
        self,
        func_name: str,
        args: Iterable[Expr] = [],
        group_by: Optional[Iterable[Union[Expr, str]]] = None,
        as_name: Optional[str] = None,
        db: Optional[Database] = None,
    ) -> None:
        table: Optional[Table] = None
        for arg in args:
            if isinstance(arg, Expr) and arg.table is not None:
                if table is None:
                    table = arg.table
                elif table.name != arg.table.name:
                    raise Exception("Cannot pass arguments from more than one tables")
        super().__init__(as_name, table=table, db=db)
        self._func_name = func_name
        self._args = args
        self._group_by = group_by

    def _serialize(self) -> str:
        args_string = ",".join([str(arg) for arg in self._args]) if any(self._args) else ""
        return f"{self._func_name}({args_string})"

    def to_table(self) -> Table:
        from_caluse = f"FROM {self.table.name}" if self.table is not None else ""
        group_by_columns = (
            ",".join([str(column) for column in self._group_by])
            if self._group_by is not None
            else ""
        )
        group_by_clause = f"GROUP BY {group_by_columns}" if self._group_by is not None else ""
        parents = [self.table] if self.table is not None else []
        orig_func_table = Table(
            " ".join(
                [
                    f"SELECT {str(self)}",
                    "," + group_by_columns if group_by_columns != "" else "",
                    from_caluse,
                    group_by_clause,
                ]
            ),
            db=self._db,
            parents=parents,
        )
        return Table(
            f"SELECT * FROM {orig_func_table.name}", parents=[orig_func_table], db=self._db
        )

    @property
    def qualified_func_name(self) -> str:
        return self._func_name


class ArrayFunctionCall(FunctionCall):
    def _serialize(self) -> str:
        args_string = (
            ",".join([f"array_agg({str(arg)})" for arg in self._args]) if any(self._args) else ""
        )
        return f"{self._func_name}({args_string})"


def function(name: str, db: Database) -> Callable[..., FunctionCall]:
    def make_function_call(*args: Expr, as_name: Optional[str] = None) -> FunctionCall:
        return FunctionCall(name, args, as_name=as_name, db=db)

    return make_function_call


def aggregate(name: str, db: Database) -> Callable[..., FunctionCall]:
    def make_function_call(
        *args: Expr,
        group_by: Optional[Iterable[Union[Expr, str]]] = None,
        as_name: Optional[str] = None,
    ) -> FunctionCall:
        return FunctionCall(name, args, group_by=group_by, as_name=as_name, db=db)

    return make_function_call


# FIXME: Add test cases for optional parameters
def create_function(
    func: Callable,
    name: Optional[str] = None,
    schema: Optional[str] = None,
    temp: bool = True,
    replace_if_exists: bool = False,
    language_handler: str = "plpython3u",
) -> Callable:
    @functools.wraps(func)
    def make_function_call(
        *args: Expr, as_name: Optional[str] = None, db: Optional[Database] = None
    ) -> FunctionCall:
        or_replace = "OR REPLACE" if replace_if_exists else ""
        schema_name = "pg_temp" if temp else (schema if schema is not None else "")
        func_name = func.__name__ if name is None else name
        if len(func_name) > 64:  # i.e. NAMELEN in PostgreSQL
            raise Exception("Function name should be no longer than 64 bytes.")
        qualified_func_name = ".".join([schema_name, func_name])
        if not temp and name is None:
            raise NotImplementedError("Name is required for a non-temp function")
        func_sig = inspect.signature(func)
        func_args_string = ",".join(
            [
                f"{param.name} {to_pg_type(param.annotation)}"
                for param in func_sig.parameters.values()
            ]
        )
        # FIXME: include things in func.__closure__
        func_lines = textwrap.dedent(inspect.getsource(func)).split("\n")
        func_body = "\n".join([line for line in func_lines if re.match(r"^\s", line)])
        if db is None:
            for arg in args:
                if isinstance(arg, Expr) and arg.db is not None:
                    db = arg.db
                    break
        if db is None:
            raise Exception("Database is required to create function")
        db.execute(
            (
                f"CREATE {or_replace} FUNCTION {qualified_func_name} ({func_args_string}) "
                f"RETURNS {to_pg_type(func_sig.return_annotation)} "
                f"LANGUAGE {language_handler} "
                f"AS $$\n"
                f"{textwrap.dedent(func_body)} $$"
            ),
            has_results=False,
        )
        return FunctionCall(qualified_func_name, args=args, as_name=as_name, db=db)

    return make_function_call


# FIXME: Add test cases for optional parameters
def create_aggregate(
    trans_func: Callable,
    name: Optional[str] = None,
    schema: Optional[str] = None,
    temp: bool = True,
    replace_if_exists: bool = False,
    language_handler: str = "plpython3u",
) -> Callable:
    @functools.wraps(trans_func)
    def make_function_call(
        *args: Expr,
        group_by: Optional[Iterable[Union[Expr, str]]] = None,
        as_name: Optional[str] = None,
        db: Optional[Database] = None,
    ) -> FunctionCall:
        trans_func_call = create_function(
            trans_func, "func_" + uuid4().hex, schema, temp, replace_if_exists, language_handler
        )(*args, as_name=as_name, db=db)
        or_replace = "OR REPLACE" if replace_if_exists else ""
        schema_name = "pg_temp" if temp else schema if schema is not None else ""
        agg_name = trans_func.__name__ if name is None else name
        qualified_agg_name = ".".join([schema_name, agg_name])
        if not temp and name is None:
            raise NotImplementedError("Name is required for a non-temp function")
        sig = inspect.signature(trans_func)
        param_list = iter(sig.parameters.values())
        state_param = next(param_list)
        args_string = ",".join(
            [f"{param.name} {to_pg_type(param.annotation)}" for param in param_list]
        )
        trans_func_call.db.execute(
            f"""
            CREATE {or_replace} AGGREGATE {qualified_agg_name} ({args_string}) (
                SFUNC = {trans_func_call.qualified_func_name},
                STYPE = {to_pg_type(state_param.annotation)}
            )
            """,
            has_results=False,
        )
        return FunctionCall(
            qualified_agg_name, args=args, group_by=group_by, as_name=as_name, db=db
        )

    return make_function_call


# FIXME: Add test cases for optional parameters
def create_array_function(
    func: Callable,
    name: Optional[str] = None,
    schema: Optional[str] = None,
    temp: bool = True,
    replace_if_exists: bool = False,
    language_handler: str = "plpython3u",
) -> Callable:
    @functools.wraps(func)
    def make_function_call(
        *args: Expr,
        group_by: Optional[Iterable[Union[Expr, str]]] = None,
        as_name: Optional[str] = None,
        db: Optional[Database] = None,
    ) -> ArrayFunctionCall:
        array_func_call = create_function(
            func, name, schema, temp, replace_if_exists, language_handler
        )(*args, as_name=as_name, db=db)
        return ArrayFunctionCall(
            array_func_call.qualified_func_name,
            args=args,
            group_by=group_by,
            as_name=as_name,
            db=db,
        )

    return make_function_call