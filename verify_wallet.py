# -*- coding: utf-8 -*-
"""
验证私钥并显示对应的 EOA 地址。
也支持检查一个地址在 Polygon 上是否是合约地址（用于判断“头像地址/充值地址”和导出私钥地址为何不同）。
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from web3 import Web3


def normalize_private_key(private_key: str) -> str:
    private_key = private_key.strip()
    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"
    return private_key


def validate_private_key_hex(private_key: str) -> Optional[str]:
    private_key = normalize_private_key(private_key)
    if len(private_key) != 66:
        return f"私钥长度不正确：期望 66 个字符，实际 {len(private_key)} 个字符"
    try:
        int(private_key, 16)
    except ValueError:
        return "私钥包含无效的十六进制字符"
    return None


def verify_wallet(private_key: str) -> bool:
    error = validate_private_key_hex(private_key)
    if error:
        print(f"错误：{error}")
        return False

    private_key = normalize_private_key(private_key)
    try:
        account = Web3().eth.account.from_key(private_key)
    except Exception as exc:
        print(f"错误：无法从私钥创建账户：{exc}")
        return False

    address = account.address
    print("=" * 60)
    print("私钥验证成功")
    print("=" * 60)
    print(f"EOA 地址: {address}")
    print(f"私钥格式: 正确 (66 字符)")
    print("=" * 60)
    print("请将以下信息添加到 `.env` 文件：")
    print(f"POLY_PRIVATE_KEY={private_key}")
    print(f"POLY_FUNDER_ADDRESS={address}")
    print("POLY_SIGNATURE_TYPE=1")
    print("=" * 60)
    return True


def json_rpc(rpc_url: str, method: str, params: list[Any]) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(rpc_url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


@dataclass(frozen=True)
class AddressCheck:
    address: str
    is_contract: bool
    code_bytes: int
    code_hex: str


def check_address_on_chain(address: str, rpc_url: str) -> AddressCheck:
    checksum = Web3.to_checksum_address(address)
    code_hex = json_rpc(rpc_url, "eth_getCode", [checksum, "latest"])
    code_bytes = max(0, (len(code_hex) - 2) // 2)
    is_contract = code_hex != "0x"
    return AddressCheck(address=checksum, is_contract=is_contract, code_bytes=code_bytes, code_hex=code_hex)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-key", dest="private_key", help="要验证的私钥 (可带/不带 0x)")
    parser.add_argument("--check-address", dest="check_address", help="检查该地址在链上是否为合约")
    parser.add_argument("--rpc", dest="rpc_url", default="https://polygon-rpc.com", help="JSON-RPC URL (默认 Polygon)")
    args = parser.parse_args()

    if args.check_address:
        try:
            info = check_address_on_chain(args.check_address, args.rpc_url)
        except Exception as exc:
            print(f"错误：地址检查失败：{exc}")
            return 1

        print("=" * 60)
        print("地址链上类型检查")
        print("=" * 60)
        print(f"地址: {info.address}")
        print(f"RPC:  {args.rpc_url}")
        print(f"合约: {'是' if info.is_contract else '否'}")
        print(f"代码: {info.code_bytes} bytes")
        if info.is_contract and info.code_bytes <= 96:
            print(f"code: {info.code_hex}")
        print("=" * 60)
        return 0

    if args.private_key:
        return 0 if verify_wallet(args.private_key) else 1

    print("=" * 60)
    print("Polymarket 私钥验证工具")
    print("=" * 60)
    print("输入从 Magic/Polymarket 导出的私钥 (输入 q 退出)")
    print("-" * 60)

    while True:
        try:
            private_key = input("\n私钥: ").strip()
            if private_key.lower() == "q":
                print("退出")
                return 0
            if not private_key:
                print("错误：私钥不能为空")
                continue
            if verify_wallet(private_key):
                return 0
            print("请重新输入私钥，或输入 q 退出")
        except KeyboardInterrupt:
            print("\n退出")
            return 0
        except Exception as exc:
            print(f"错误：{exc}")


if __name__ == "__main__":
    raise SystemExit(main())
