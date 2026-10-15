-- Runs once, on the first start of an empty data volume (docker-entrypoint-initdb.d).
-- Creates the database the API test suite uses, so a clean clone can run `pytest` without
-- any manual step. The tests never touch the main `codelens` development database.
-- Existing volume? This script will not re-run; the test suite creates the database itself
-- when it is missing (see apps/api/tests/db_utils.py).
CREATE DATABASE codelens_test;
