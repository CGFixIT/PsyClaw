"""Constructors for typed error classes that had no direct call sites in tests."""

from __future__ import annotations

from utils.errors import (
    CorpusEmptyError,
    NetCommandNotInstalledError,
    NetConnectRuntimeError,
)


def test_corpus_empty_error_code() -> None:
    err = CorpusEmptyError("no docs")
    assert err.code == "CORPUS_EMPTY"
    assert "no docs" in err.message


def test_net_command_not_installed_error_code() -> None:
    err = NetCommandNotInstalledError("arp missing")
    assert err.code == "NET_COMMAND_NOT_INSTALLED"


def test_net_connect_runtime_error_code() -> None:
    err = NetConnectRuntimeError("neighbor cache failed", details={"rc": 1})
    assert err.code == "NETCONNECT_RUNTIME_ERROR"
    assert err.details == {"rc": 1}
