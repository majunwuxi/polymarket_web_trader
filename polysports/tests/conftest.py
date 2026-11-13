"""测试环境防护：只允许 loopback，本目录使用的示例均为模拟数据。"""
import ipaddress
import socket

import pytest


def _loopback(host):
    if isinstance(host, bytes):
        host = host.decode()
    if isinstance(host, str) and host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


@pytest.fixture(autouse=True)
def deny_external_network(monkeypatch):
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    original_sendto = socket.socket.sendto

    def check(address):
        if not isinstance(address, tuple) or not address or not _loopback(address[0]):
            raise RuntimeError("测试禁止外部网络请求；上游数据必须使用明确标记的模拟数据")

    def safe_connect(sock, address):
        check(address)
        return original_connect(sock, address)

    def safe_connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)

    def safe_create(address, *args, **kwargs):
        check(address)
        return original_create(address, *args, **kwargs)

    def safe_getaddrinfo(host, *args, **kwargs):
        if host is not None and not _loopback(host):
            raise RuntimeError("测试禁止外部网络请求；上游数据必须使用明确标记的模拟数据")
        return original_getaddrinfo(host, *args, **kwargs)

    def safe_sendto(sock, data, *args):
        if not args:
            raise RuntimeError("测试禁止未指定目的地址的 UDP 请求")
        check(args[-1])
        return original_sendto(sock, data, *args)

    monkeypatch.setattr(socket, "getaddrinfo", safe_getaddrinfo)
    monkeypatch.setattr(socket.socket, "sendto", safe_sendto)
    monkeypatch.setattr(socket.socket, "connect", safe_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", safe_connect_ex)
    monkeypatch.setattr(socket, "create_connection", safe_create)
