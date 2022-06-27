import inspect

import pytest

import greenplumpython as gp
from greenplumpython import type


@pytest.fixture
def db():
    db = gp.database(host="localhost", dbname="gpadmin")
    yield db
    db.close()


def test_type_create(db: gp.Database):
    class Person:
        _first_name: str
        _last_name: str

    type.create_type(Person, db, as_name="Person", is_temp=False)
    with pytest.raises(Exception) as exc_info:
        type.create_type(Person, db, as_name="Person", is_temp=False)
    assert 'type "person" already exists\n' in str(exc_info.value)


def test_type_create_temp(db: gp.Database):
    class Person:
        _first_name: str
        _last_name: str

    type.create_type(Person, db, as_name="Person")
    query = f"""
                    SELECT n.nspname as "Schema",
                      pg_catalog.format_type(t.oid, NULL) AS "Name",
                      pg_catalog.obj_description(t.oid, 'pg_type') as "Description"
                    FROM pg_catalog.pg_type t
                         LEFT JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                    WHERE (t.typrelid = 0 OR (SELECT c.relkind = 'c' FROM pg_catalog.pg_class c WHERE c.oid = t.typrelid))
                      AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_type el WHERE el.oid = t.typelem AND el.typarray = t.oid)
                          AND n.nspname <> 'pg_catalog'
                          AND n.nspname <> 'information_schema'
                      AND pg_catalog.pg_type_is_visible(t.oid)
                    ORDER BY 1, 2;
                """
    ret = db.execute(query)
    exists = False
    for row in ret:
        if row["Name"] == "person" and row["Schema"].startswith("pg_temp"):
            exists = True
    assert exists


def test_type_drop(db: gp.Database):
    class Person:
        _first_name: str
        _last_name: str

    type.create_type(Person, db, as_name="Person", is_temp=False)
    type.drop_type("Person", db)
    type.create_type(Person, db, as_name="Person", is_temp=False)


def test_create_type_rec(db: gp.Database):
    class Person:
        _first_name: str
        _last_name: str

    class Couple:
        _first_person: Person
        _second_person: Person

    def create_couple() -> Couple:
        return Couple

    # FIXME : In this case, program will create twice Person type
    #         when creating Couple type with different type_name
    func_sig = inspect.signature(create_couple)
    type.create_type(func_sig.return_annotation, db, as_name="Couple")
    with pytest.raises(Exception) as exc_info:
        type.create_type(func_sig.return_annotation, db, as_name="Couple")
    assert 'type "couple" already exists\n' in str(exc_info.value)
