from __future__ import annotations

import logging
import math
import threading
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests
from polymarket import RequestRejectedError
from eth_account import Account
from web3 import Web3

from .config import EnvConfig

logger = logging.getLogger(__name__)
WALLET_TYPES = {0: "EOA", 1: "POLY_PROXY", 2: "GNOSIS_SAFE", 3: "DEPOSIT_WALLET"}


def _restriction_result(code: str, retry_after: Any = None) -> dict[str, Any]:
    if code == "engine_restarting":
        message = "Polymarket 撮合引擎正在重启，暂时无法处理交易请求。请稍后重试；恢复后新订单可能暂时仅接受 post-only 订单。"
    else:
        message = "Polymarket 当前处于 post-only 模式，普通订单暂不可提交，请稍后重试。"
    result: dict[str, Any] = {"orderId": "", "status": "failed", "code": code, "error": message}
    try:
        delay = float(retry_after)
        if math.isfinite(delay) and delay >= 0:
            result["retry_after"] = delay
            result["error"] += f" 服务端建议等待至少 {delay:g} 秒。"
    except (TypeError, ValueError, OverflowError):
        pass
    return result


def _classify_restriction(exc: Exception) -> Optional[dict[str, Any]]:
    if isinstance(exc, RequestRejectedError):
        if exc.status == 425 or exc.restriction == "restarting":
            return _restriction_result("engine_restarting", exc.retry_after)
        if exc.restriction == "post_only" or exc.code == "post_only_mode":
            return _restriction_result("post_only_mode", exc.retry_after)
    if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 425:
        return _restriction_result("engine_restarting", exc.response.headers.get("Retry-After"))
    return None


class CLOBOrderExecutor:
    def __init__(self, clob_host: str, env: EnvConfig, price_precision: int, size_precision: int, dry_run: bool = False) -> None:
        self.dry_run = dry_run or env.dry_run
        self.clob_host = clob_host
        self._env = env
        self._client = None
        self._client_lock = threading.Lock()

    @property
    def has_api_creds(self) -> bool:
        return self._client is not None

    def _get_client(self):
        with self._client_lock:
            if self._client is not None:
                return self._client
            env = self._env
            if self.clob_host.rstrip("/") != "https://clob.polymarket.com":
                raise ValueError("统一 SDK 仅支持生产 CLOB 地址")
            if env.signature_type not in WALLET_TYPES:
                raise ValueError("签名类型必须为 0、1、2 或 3")
            values = (env.clob_api_key, env.clob_api_secret, env.clob_api_passphrase)
            if any(values) and not all(values):
                raise ValueError("CLOB API 凭证不完整，请填写全部三项或全部留空")
            if not env.private_key or not Web3.is_address(env.funder_address):
                raise ValueError("请配置有效私钥及账户钱包地址")
            try:
                signer = Account.from_key(env.private_key).address
            except (ValueError, TypeError) as exc:
                raise ValueError("私钥无效") from exc
            wallet = Web3.to_checksum_address(env.funder_address)
            if env.signature_type == 0:
                if signer.lower() != wallet.lower():
                    raise ValueError("EOA 钱包地址必须与私钥对应地址一致")
            else:
                if signer.lower() == wallet.lower():
                    raise ValueError("智能钱包地址不能与签名者地址相同")
                rpc = env.polygon_rpc_url or "https://polygon-bor-rpc.publicnode.com"
                try:
                    web3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
                    if web3.eth.chain_id != 137:
                        raise ValueError("RPC 不是 Polygon 主网")
                    code = web3.eth.get_code(wallet)
                except Exception as exc:
                    raise ValueError("无法通过 Polygon RPC 检查钱包部署状态") from exc
                if not code:
                    raise ValueError("账户钱包未部署或地址错误；请先在 Polymarket 完成钱包设置")
            try:
                from polymarket import SecureClient
                from polymarket.models.clob.api_key import ApiKeyCreds
            except ImportError as exc:
                raise ValueError("缺少官方 polymarket-client SDK，请安装项目依赖") from exc
            credentials = ApiKeyCreds(apiKey=values[0], secret=values[1], passphrase=values[2]) if all(values) else None
            try:
                client = SecureClient.create(private_key=env.private_key, wallet=wallet, credentials=credentials)
            except Exception as exc:
                if _classify_restriction(exc):
                    raise
                logger.warning("无法初始化统一交易客户端 (%s)", type(exc).__name__)
                raise ValueError("交易客户端初始化失败，请检查钱包、CLOB 凭证和网络") from exc
            if str(client.wallet).lower() != wallet.lower() or client.wallet_type != WALLET_TYPES[env.signature_type]:
                client.close()
                raise ValueError(f"钱包类型或地址不匹配，请核对配置（期望 {WALLET_TYPES[env.signature_type]}）")
            self._client = client
            return client

    def _get_book_context(self, asset_id: str) -> tuple[str, Decimal, Decimal]:
        response = requests.get(f"{self.clob_host.rstrip('/')}/book", params={"token_id": asset_id}, timeout=10)
        response.raise_for_status()
        book = response.json()
        if not isinstance(book, dict):
            raise ValueError("订单簿响应无效")
        if str(book.get("asset_id") or "") != asset_id:
            raise ValueError("订单簿资产标识不匹配")
        version = book.get("version")
        if version is None:
            version = "v1"
        if version not in ("v1", "v2"):
            raise ValueError(f"不支持的订单簿版本: {version}")
        try:
            tick = Decimal(str(book["tick_size"]))
            minimum = Decimal(str(book["min_order_size"]))
            if not tick.is_finite() or not minimum.is_finite() or not 0 < tick < Decimal("0.5") or minimum <= 0:
                raise ValueError("订单簿交易规则无效")
        except (KeyError, InvalidOperation, TypeError) as exc:
            raise ValueError("订单簿缺少有效的价格增量或最小份额") from exc
        return version, tick, minimum

    @staticmethod
    def _validate_order_constraints(price: Any, size: Any, tick: Decimal, minimum: Decimal) -> None:
        try:
            price_value = Decimal(str(price))
            size_value = Decimal(str(size))
            if not price_value.is_finite() or not size_value.is_finite():
                raise ValueError("价格和份额必须是有限数字")
            if price_value < tick or price_value > Decimal(1) - tick or price_value % tick != 0:
                raise ValueError(f"价格必须为 {tick} 的整数倍，范围为 {tick} 至 {Decimal(1) - tick}")
            if size_value <= 0 or size_value < minimum:
                raise ValueError(f"订单份额不能小于 {minimum}")
            if size_value % Decimal("0.01") != 0:
                raise ValueError("订单份额最多支持两位小数")
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("价格或份额格式无效") from exc

    def place_order(self, side: str, price: float, size: float, market_id: str, token_id: str) -> dict[str, Any]:
        if not isinstance(token_id, str) or not token_id.isdigit():
            return {"orderId": "", "status": "failed", "error": "资产标识无效"}
        if side not in ("BUY", "SELL"):
            return {"orderId": "", "status": "failed", "error": "订单方向无效"}
        try:
            _, tick, minimum = self._get_book_context(token_id)
            self._validate_order_constraints(price, size, tick, minimum)
        except (requests.RequestException, ValueError) as exc:
            restriction = _classify_restriction(exc)
            if restriction:
                return restriction
            return {"orderId": "", "status": "failed", "error": f"订单规则校验失败: {exc}"}
        if self.dry_run:
            return {"orderId": f"dryrun-{uuid.uuid4()}", "status": "simulated"}
        try:
            client = self._get_client()
            signed = client.create_limit_order(asset_id=token_id, side=side, price=str(price), size=str(size))
            response = client.post_order(signed)
            if not response.ok:
                if getattr(response, "code", None) == "post_only_mode":
                    return _restriction_result("post_only_mode")
                return {"orderId": "", "status": "failed", "error": str(response.message)}
            order_id = str(response.order_id or "")
            if not order_id:
                return {"orderId": "", "status": "failed", "error": "订单响应缺少订单编号，无法确认提交"}
            return {"orderId": order_id, "status": "pending", "filled_size": None}
        except ValueError as exc:
            return {"orderId": "", "status": "failed", "error": str(exc)}
        except Exception as exc:
            restriction = _classify_restriction(exc)
            if restriction:
                return restriction
            logger.warning("提交订单失败 (%s)", type(exc).__name__)
            return {"orderId": "", "status": "failed", "error": "提交订单失败，请检查日志、钱包余额和授权状态"}

    def create_and_post_order(self, price: float, size: float, side: str, token_id: str) -> str:
        result = self.place_order(side=side, price=price, size=size, market_id=token_id, token_id=token_id)
        return str(result.get("orderId") or "")

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        if self.dry_run:
            return None
        try:
            result = self._get_client().get_order(order_id=order_id)
            return {"id": str(result.id), "status": result.status, "size_matched": str(result.size_matched)}
        except Exception as exc:
            logger.warning("订单查询失败 (%s)", type(exc).__name__)
            return None

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if self.dry_run:
            return {"success": True, "status": "simulated"}
        try:
            result = self._get_client().cancel_order(order_id=order_id)
            if order_id in result.canceled:
                return {"success": True, "status": "canceled"}
            return {"success": False, "error": str(result.not_canceled.get(order_id) or "交易所未确认撤单")}
        except Exception as exc:
            restriction = _classify_restriction(exc)
            if restriction:
                return {"success": False, **{k: v for k, v in restriction.items() if k not in ("orderId", "status")}}
            logger.warning("撤单失败 (%s)", type(exc).__name__)
            return {"success": False, "error": "撤单失败，请检查钱包和网络"}
