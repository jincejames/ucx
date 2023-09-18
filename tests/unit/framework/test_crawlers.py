import os
import sys
from dataclasses import dataclass

import pytest
from databricks.sdk.service import sql

from databricks.labs.ucx.framework.crawlers import (
    CrawlerBase,
    RuntimeBackend,
    StatementExecutionBackend,
)

from ..framework.mocks import MockBackend


@dataclass
class Foo:
    first: str
    second: bool


@dataclass
class Bar:
    first: str
    second: bool
    third: float


def test_invalid():
    with pytest.raises(ValueError):
        CrawlerBase(MockBackend(), "a.a.a", "b", "c")


def test_full_name():
    cb = CrawlerBase(MockBackend(), "a", "b", "c")
    assert "a.b.c" == cb._full_name


def test_snapshot_appends_to_existing_table():
    b = MockBackend()
    cb = CrawlerBase(b, "a", "b", "c")

    result = cb._snapshot(fetcher=lambda: [], loader=lambda: [Foo(first="first", second=True)])

    assert [Foo(first="first", second=True)] == result
    assert [Foo(first="first", second=True)] == b.rows_written_for("a.b.c", "append")


def test_snapshot_appends_to_new_table():
    b = MockBackend()
    cb = CrawlerBase(b, "a", "b", "c")

    def fetcher():
        msg = ".. TABLE_OR_VIEW_NOT_FOUND .."
        raise RuntimeError(msg)

    result = cb._snapshot(fetcher=fetcher, loader=lambda: [Foo(first="first", second=True)])

    assert [Foo(first="first", second=True)] == result
    assert [Foo(first="first", second=True)] == b.rows_written_for("a.b.c", "append")


def test_snapshot_wrong_error():
    b = MockBackend()
    cb = CrawlerBase(b, "a", "b", "c")

    def fetcher():
        msg = "always fails"
        raise ValueError(msg)

    with pytest.raises(ValueError):
        cb._snapshot(fetcher=fetcher, loader=lambda: [Foo(first="first", second=True)])


def test_statement_execution_backend_execute_happy(mocker):
    execute_statement = mocker.patch("databricks.sdk.service.sql.StatementExecutionAPI.execute_statement")
    execute_statement.return_value = sql.ExecuteStatementResponse(
        status=sql.StatementStatus(state=sql.StatementState.SUCCEEDED)
    )

    seb = StatementExecutionBackend(mocker.Mock(), "abc")

    seb.execute("CREATE TABLE foo")

    execute_statement.assert_called_with(
        warehouse_id="abc",
        statement="CREATE TABLE foo",
        catalog=None,
        schema=None,
        disposition=sql.Disposition.INLINE,
        format=sql.Format.JSON_ARRAY,
        byte_limit=None,
        wait_timeout=None,
    )


def test_statement_execution_backend_fetch_happy(mocker):
    # this will improve once https://github.com/databricks/databricks-sdk-py/pull/295 merged
    execute_fetch_all = mocker.patch(
        "databricks.labs.ucx.mixins.sql.StatementExecutionExt.execute_fetch_all", return_value=[1, 2, 3]
    )

    seb = StatementExecutionBackend(mocker.Mock(), "abc")

    result = seb.fetch("SELECT id FROM range(3)")

    assert [1, 2, 3] == result

    execute_fetch_all.assert_called_with("abc", "SELECT id FROM range(3)")


def test_statement_execution_backend_save_table_overwrite(mocker):
    seb = StatementExecutionBackend(mocker.Mock(), "abc")
    with pytest.raises(NotImplementedError):
        seb.save_table("a.b.c", [1, 2, 3], mode="overwrite")


def test_statement_execution_backend_save_table_empty_records(mocker):
    execute_sql = mocker.patch("databricks.labs.ucx.mixins.sql.StatementExecutionExt.execute")

    seb = StatementExecutionBackend(mocker.Mock(), "abc")

    seb.save_table("a.b.c", [])

    execute_sql.assert_not_called()


def test_statement_execution_backend_save_table_two_records(mocker):
    execute_sql = mocker.patch("databricks.labs.ucx.mixins.sql.StatementExecutionExt.execute")

    seb = StatementExecutionBackend(mocker.Mock(), "abc")

    seb.save_table("a.b.c", [Foo("aaa", True), Foo("bbb", False)])

    assert [
        mocker.call(
            "abc", "CREATE TABLE IF NOT EXISTS a.b.c (first STRING NOT NULL, second BOOLEAN NOT NULL) USING DELTA"
        ),
        mocker.call("abc", "INSERT INTO a.b.c (first, second) VALUES ('aaa', TRUE), ('bbb', FALSE)"),
    ] == execute_sql.mock_calls


def test_runtime_backend_execute(mocker):
    from unittest import mock

    with mock.patch.dict(os.environ, {"DATABRICKS_RUNTIME_VERSION": "14.0"}):
        pyspark_sql_session = mocker.Mock()
        sys.modules["pyspark.sql.session"] = pyspark_sql_session

        rb = RuntimeBackend()

        rb.execute("CREATE TABLE foo")

        rb._spark.sql.assert_called_with("CREATE TABLE foo")


def test_runtime_backend_fetch(mocker):
    from unittest import mock

    with mock.patch.dict(os.environ, {"DATABRICKS_RUNTIME_VERSION": "14.0"}):
        pyspark_sql_session = mocker.Mock()
        sys.modules["pyspark.sql.session"] = pyspark_sql_session

        rb = RuntimeBackend()
        rb._spark.sql().collect.return_value = [1, 2, 3]

        result = rb.fetch("SELECT id FROM range(3)")

        assert [1, 2, 3] == result

        rb._spark.sql.assert_called_with("SELECT id FROM range(3)")


def test_runtime_backend_save_table(mocker):
    from unittest import mock

    with mock.patch.dict(os.environ, {"DATABRICKS_RUNTIME_VERSION": "14.0"}):
        pyspark_sql_session = mocker.Mock()
        sys.modules["pyspark.sql.session"] = pyspark_sql_session

        rb = RuntimeBackend()

        rb.save_table("a.b.c", [Foo("aaa", True), Foo("bbb", False)])

        rb._spark.createDataFrame.assert_called_with(
            [Foo(first="aaa", second=True), Foo(first="bbb", second=False)],
            "first STRING NOT NULL, second BOOLEAN NOT NULL",
        )
        rb._spark.createDataFrame().write.saveAsTable.assert_called_with("a.b.c", mode="append")