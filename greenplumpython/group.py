"""
This module creates a Python object TableRowGroup for group by table.
"""
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterable,
    List,
    MutableSet,
    Optional,
    Set,
)

from greenplumpython.expr import Expr, serialize

if TYPE_CHECKING:
    from greenplumpython.func import FunctionExpr
    from greenplumpython.table import Table


class TableGroupingSets:
    """
    Represents a group of rows in a :class:`~table.Table` generated by
    :func:`~table.Table.group_by`.
    """

    def __init__(self, table: "Table", grouping_sets: List[Iterable["Expr"]]) -> None:
        self._table = table
        self._grouping_sets = grouping_sets

    def apply(
        self,
        func: Callable[["Table"], "FunctionExpr"],
        expand: bool = False,
        as_name: Optional[str] = None,
    ) -> "Table":
        """
        Apply a function to the grouping set.

        Args:
            func: An aggregate function to be applied to
            expand: expand field of composite returning type
            as_name: rename returning column

        Returns:
            Table: resulted Table

        Example:
            .. code-block::  python

                numbers.group_by("is_even").apply(lambda _: count())
        """
        return func(self._table).bind(group_by=self).apply(expand=expand, as_name=as_name)

    def assign(self, **new_columns: Callable[["Table"], Any]) -> "Table":
        """
        Assigns new columns to the current grouping sets. **Existing columns
        cannot be reassigned**.

        Args:
            new_columns: a :class:`dict` whose keys are column names and values
                are :class:`Callable`s returning column data when applied to the
                current :class:`TableGroupingSets`.

        Returns:
            :class:`Table` with the new columns.


        Example:
            .. code-block::  python

                count = gp.aggregate_function("count")
                results = numbers.group_by().assign(count=lambda t: count(t["val"]))

        """
        from greenplumpython.table import Table

        targets: List[str] = list(self.flatten())
        for k, f in new_columns.items():
            v: Any = f(self.table).bind(group_by=self)
            if isinstance(v, Expr) and not (v.table is None or v.table == self.table):
                raise Exception("Newly included columns must be based on the current table")
            targets.append(f"{serialize(v)} AS {k}")
        return Table(
            f"SELECT {','.join(targets)} FROM {self.table.name} {self.clause()}",
            parents=[self.table],
        )

    def union(self, other: Callable[["Table"], "TableGroupingSets"]) -> "TableGroupingSets":
        """
        Union with another :class:`TableGroupingSets` so that when applying an
        agggregate function to the list, the function will be applied to
        each grouping set individually.

        Args:
            other: a :class:`Callable` returning the result of
                :func:`Table.group_by()`when applied to the current :class:`Table`.
        """
        return TableGroupingSets(
            self._table,
            self._grouping_sets + other(self._table)._grouping_sets,
        )

    def flatten(self) -> Set[str]:
        """:meta private:"""
        item_set: MutableSet[Expr] = set()
        for grouping_set in self._grouping_sets:
            for item in grouping_set:
                assert isinstance(item, str), f"Grouping item {item} is not a column name."
                item_set.add(item)
        return item_set

    @property
    def table(self) -> "Table":
        """
        Returns :class:`~table.Table` associated for GROUP BY

        Returns:
            Table
        """
        return self._table

    # FIXME: Make this function package-private
    def clause(self) -> str:
        """:meta private:"""
        grouping_sets_str = [
            f"({','.join([item for item in grouping_set])})" for grouping_set in self._grouping_sets
        ]
        return "GROUP BY GROUPING SETS " + f"({','.join(grouping_sets_str)})"
