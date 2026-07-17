"""Database connection helpers. DSN from .env (DESK_DSN); no secrets in code."""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DSN = "host=127.0.0.1 port=55432 dbname=trading_desk user=desk password=desk"


def dsn() -> str:
    return os.environ.get("DESK_DSN", DEFAULT_DSN)


def connect(**kwargs) -> psycopg.Connection:
    return psycopg.connect(dsn(), **kwargs)
