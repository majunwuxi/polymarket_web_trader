from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

# Polymarket cut over clob.polymarket.com to CLOB V2 on 2026-04-28. The legacy
# py-clob-client signs V1 orders and now fails with order_version_mismatch.
try:
    from py_clob_client_v2 import ApiCreds, ClobClient, OrderArgs, OrderType
    from py_clob_client_v2.exceptions import PolyApiException, PolyException
    from py_clob_client_v2.order_builder.constants import BUY, SELL

    HAS_ORDER_ARGS = True
    IS_CLOB_V2 = True
    SignatureType = None
except ImportError:
    from py_clob_client.client import ClobClient

    IS_CLOB_V2 = False

    # Try to import SignatureType, fallback to using int directly
    try:
        from py_clob_client.signature import SignatureType
    except ImportError:
        try:
            from py_clob_client.types import SignatureType
        except ImportError:
            # If SignatureType is not available, we'll use the int directly
            SignatureType = None

    # Try to import OrderArgs and related types
    try:
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
        HAS_ORDER_ARGS = True
    except ImportError:
        try:
            from py_clob_client.types import OrderArgs, OrderType
            from py_clob_client.clob_types import ApiCreds
            from py_clob_client.constants import BUY, SELL
            HAS_ORDER_ARGS = True
        except ImportError:
            HAS_ORDER_ARGS = False
            OrderArgs = None
            OrderType = None
            ApiCreds = None
            BUY = "BUY"
            SELL = "SELL"

    # Try to import exceptions
    try:
        from py_clob_client.exceptions import PolyApiException, PolyException
    except ImportError:
        PolyApiException = Exception
        PolyException = Exception

from .config import EnvConfig

logger = logging.getLogger(__name__)


def normalize_size_for_api(size: float) -> float:
    """
    规范化数量以符合 API 要求
    - 先四舍五入到2位小数
    - 然后格式化为4位小数发送给API
    
    例如: 11.2655 -> 11.27 -> 11.2700
    
    Args:
        size: 原始数量
        
    Returns:
        规范化后的数量（内部保留2位小数，发送时格式化为4位小数）
    """
    if size <= 0:
        return 0.0
    
    # 先四舍五入到2位小数
    size_rounded_2 = round(size, 2)
    
    # 格式化为4位小数（符合API要求）
    # 例如: 11.27 -> 11.2700
    size_str = f"{size_rounded_2:.4f}"
    size_normalized = float(size_str)
    
    return max(0.0, size_normalized)


class CLOBOrderExecutor:
    def __init__(
        self,
        clob_host: str,
        env: EnvConfig,
        price_precision: int,
        size_precision: int,
        dry_run: bool = False,
    ) -> None:
        self.dry_run = dry_run or env.dry_run
        self.clob_host = clob_host
        self._env = env
        self._protocol_v2_client = None
        self.price_precision = price_precision
        self.size_precision = size_precision
        
        # Handle signature_type - use SignatureType if available, otherwise use int directly
        if SignatureType is not None:
            sig_type = SignatureType(env.signature_type)
        else:
            sig_type = env.signature_type
        
        # Dynamically check ClobClient's __init__ signature to determine correct parameters
        sig = inspect.signature(ClobClient.__init__)
        params = list(sig.parameters.keys())
        # Remove 'self' parameter
        if 'self' in params:
            params.remove('self')
        
        # Build kwargs based on available parameters
        init_kwargs = {}
        
        # Check which parameters are available for private key
        if 'private_key' in params:
            init_kwargs['private_key'] = env.private_key
        elif 'key' in params:
            init_kwargs['key'] = env.private_key
        elif 'api_key' in params:
            init_kwargs['api_key'] = env.private_key
        
        # Add chain_id (Polygon mainnet = 137, testnet = 80001)
        # For Polymarket, use Polygon mainnet
        if 'chain_id' in params:
            init_kwargs['chain_id'] = 137  # Polygon mainnet
        
        if 'signature_type' in params:
            init_kwargs['signature_type'] = sig_type
        
        if 'funder' in params:
            init_kwargs['funder'] = env.funder_address
        elif 'funder_address' in params:
            init_kwargs['funder_address'] = env.funder_address

        env_api_creds = None
        api_cred_values = [
            env.clob_api_key,
            env.clob_api_secret,
            env.clob_api_passphrase,
        ]
        if all(api_cred_values):
            if ApiCreds is None:
                logger.warning("POLY_CLOB_API_* values are set, but ApiCreds is not available in py-clob-client")
            else:
                env_api_creds = ApiCreds(
                    api_key=env.clob_api_key,
                    api_secret=env.clob_api_secret,
                    api_passphrase=env.clob_api_passphrase,
                )
                if 'creds' in params:
                    init_kwargs['creds'] = env_api_creds
        elif any(api_cred_values):
            logger.warning("Incomplete POLY_CLOB_API_* credentials; falling back to API key derivation")
        
        # Initialize ClobClient
        # host is typically the first positional parameter
        # Check if host is in params (keyword) or positional
        if 'host' in params:
            init_kwargs['host'] = clob_host
            self.client = ClobClient(**init_kwargs)
        else:
            # host is positional, pass it as first argument
            self.client = ClobClient(clob_host, **init_kwargs)
        
        # Try to set API credentials if needed (for post_order endpoint)
        try:
            if env_api_creds is not None:
                if hasattr(self.client, 'set_api_creds'):
                    self.client.set_api_creds(env_api_creds)
                logger.info("CLOB API credentials loaded from environment")
            elif hasattr(self.client, 'create_or_derive_api_key'):
                creds = self.client.create_or_derive_api_key()
                if hasattr(self.client, 'set_api_creds'):
                    self.client.set_api_creds(creds)
                    logger.debug("CLOB V2 API credentials set successfully")
            elif hasattr(self.client, 'create_or_derive_api_creds'):
                creds = self.client.create_or_derive_api_creds()
                if hasattr(self.client, 'set_api_creds'):
                    self.client.set_api_creds(creds)
                    logger.debug("API credentials set successfully")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to set API credentials (may not be needed): %s", exc, exc_info=True)

        # Emit a one-line auth status to make diagnosing env/auth issues easier (never logs secrets).
        try:
            has_signer = getattr(self.client, "signer", None) is not None
            has_creds = getattr(self.client, "creds", None) is not None
            mode = getattr(self.client, "mode", None)
            logger.info("CLOB client auth: has_signer=%s has_creds=%s mode=%s", has_signer, has_creds, mode)
        except Exception:  # noqa: BLE001
            pass

        try:
            self._creds_derived = getattr(self.client, "creds", None) is not None
        except Exception:  # noqa: BLE001
            self._creds_derived = False

    def _ensure_api_creds(self) -> bool:
        has_creds = getattr(self.client, "creds", None) is not None
        if has_creds:
            return True

        logger.info("API credentials missing, attempting to derive...")
        try:
            if hasattr(self.client, 'create_or_derive_api_key'):
                creds = self.client.create_or_derive_api_key()
                if hasattr(self.client, 'set_api_creds'):
                    self.client.set_api_creds(creds)
                    logger.info("CLOB API credentials derived and set successfully")
                    self._creds_derived = True
                    return True
            elif hasattr(self.client, 'create_or_derive_api_creds'):
                creds = self.client.create_or_derive_api_creds()
                if hasattr(self.client, 'set_api_creds'):
                    self.client.set_api_creds(creds)
                    logger.info("CLOB API credentials derived and set successfully")
                    self._creds_derived = True
                    return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to derive API credentials: %s", exc, exc_info=True)
        return False

    def _validate_order_response(self, resp: Any, order_size: float) -> tuple[bool, Optional[float], Optional[str]]:
        if not isinstance(resp, dict):
            return False, None, "无法确认订单响应"
        error = resp.get("error") or resp.get("errorMessage")
        status = str(resp.get("status") or "").lower()
        if error or resp.get("success") is False or status in ("error", "failed", "rejected", "cancelled", "canceled"):
            return False, None, str(error or status or "订单被拒绝")
        order_id = resp.get("orderID") or resp.get("orderId") or resp.get("order_id") or resp.get("id")
        if not order_id:
            return False, None, "订单响应缺少订单编号，无法确认提交"
        filled = None
        for key in ("filledSize", "filled_size", "filled", "size_matched", "sizeMatched"):
            if resp.get(key) is not None:
                try:
                    value = float(resp[key])
                    if math.isfinite(value) and 0 <= value <= order_size:
                        filled = value
                except (ValueError, TypeError):
                    pass
                break
        return True, filled, None

    def _get_book_version(self, asset_id: str) -> str:
        response = requests.get(f"{self.clob_host.rstrip('/')}/book", params={"token_id": asset_id}, timeout=10)
        response.raise_for_status()
        book = response.json()
        if not isinstance(book, dict):
            raise ValueError("订单簿响应无效")
        if str(book.get("asset_id") or "") != asset_id:
            raise ValueError("订单簿资产标识不匹配")
        version = book.get("version")
        if version is None or version == "v1":
            return "v1"
        if version == "v2":
            return "v2"
        raise ValueError(f"不支持的订单簿版本: {version}")

    def _place_protocol_v2_order(self, asset_id: str, side: str, price: float, size: float) -> dict[str, Any]:
        try:
            if self.clob_host.rstrip("/") != "https://clob.polymarket.com":
                raise ValueError("统一 SDK 仅支持已配置的生产 CLOB 地址")
            if not self._env.funder_address:
                raise ValueError("V2 交易需要明确配置资金钱包地址")
            if self._protocol_v2_client is None:
                from polymarket import SecureClient
                from polymarket.models.clob.api_key import ApiKeyCreds
                values = (self._env.clob_api_key, self._env.clob_api_secret, self._env.clob_api_passphrase)
                if any(values) and not all(values):
                    raise ValueError("CLOB API 凭证不完整")
                creds = ApiKeyCreds(apiKey=values[0], secret=values[1], passphrase=values[2]) if all(values) else None
                self._protocol_v2_client = SecureClient.create(private_key=self._env.private_key, wallet=self._env.funder_address, credentials=creds)
            client = self._protocol_v2_client
            signed = client.create_limit_order(asset_id=asset_id, side=side.upper(), price=str(price), size=str(size))
            response = client.post_order(signed)
            if not response.ok:
                return {"orderId": "", "status": "failed", "error": response.message}
            return {"orderId": str(response.order_id), "status": "pending", "filled_size": None}
        except Exception as exc:
            logger.warning("Protocol V2 下单失败: %s", exc)
            return {"orderId": "", "status": "failed", "error": str(exc)}

    def place_order(
        self, side: str, price: float, size: float, market_id: str, token_id: str
    ) -> dict[str, Any]:
        # 使用绿色字体显示准备下单信息
        logger.info(
            "\033[1;32m【准备下单】方向: %s, 原始价格: %.6f, 原始数量: %.6f, token_id: %s, market_id: %s\033[0m",
            side, price, size, token_id, market_id
        )
        
        # 验证和清理 token_id
        original_token_id = token_id
        cleaned_token_id = token_id.strip().strip('"').strip("'").strip("[").strip("]").strip()
        if cleaned_token_id != original_token_id:
            logger.warning(f"token_id 包含额外字符，已清理: '{original_token_id}' -> '{cleaned_token_id}'")
            token_id = cleaned_token_id
        
        if not token_id or not token_id.isdigit():
            error_msg = f"token_id 格式无效: '{token_id}' (原始: '{original_token_id}')"
            logger.error(error_msg)
            return {"orderId": "", "status": "failed", "error": error_msg}
        
        # 简单的价格和数量验证（只做必要的范围检查，不做过度精度处理）
        import math
        if size <= 0:
            error_msg = f"size <= 0: {size}"
            logger.error(error_msg)
            return {"orderId": "", "status": "failed", "error": error_msg}
        
        # 使用配置的精度进行四舍五入（优先根据聚合/环境配置）
        try:
            price = round(price, int(self.price_precision))
        except Exception:
            price = round(price, 3)
        
        try:
            size = round(size, int(self.size_precision))
        except Exception:
            size = round(size, 4)
        
        # 再进行边界钳制，避免 0.998 -> 1.00 的问题导致无效价格
        if price >= 1.0:
            logger.warning("Price rounded to >= 1.0, clamping to 0.999 (was %s)", price)
            price = 0.999
        elif price < 0.001:
            logger.warning("Price rounded to < 0.001, clamping to 0.001 (was %s)", price)
            price = 0.001
        
        # 根据最终钳制结果，再次按精度规范化一次，确保传参稳定
        try:
            price = round(price, int(self.price_precision))
        except Exception:
            price = round(price, 3)
        
        if self.dry_run:
            order_id = f"dryrun-{uuid.uuid4()}"
            logger.info(
                "[DRY RUN] Place order %s %s %s size=%s price=%s",
                market_id,
                token_id,
                side,
                size,
                price,
            )
            return {"orderId": order_id, "status": "simulated"}

        try:
            version = self._get_book_version(token_id)
        except (requests.RequestException, ValueError) as exc:
            return {"orderId": "", "status": "failed", "error": f"无法确认订单簿协议版本: {exc}"}
        if version == "v2":
            return self._place_protocol_v2_order(token_id, side, price, size)

        # 验证 token_id 是否有效（通过 get_tick_size）
        try:
            if hasattr(self.client, 'get_tick_size'):
                tick_size = self.client.get_tick_size(token_id)
                logger.debug(f"token_id 验证成功，tick_size={tick_size}")
        except Exception as tick_error:
            error_str = str(tick_error)
            if "404" in error_str or "market not found" in error_str.lower():
                logger.warning(f"CLOB API 返回 404，可能是市场不存在或已关闭: {error_str}")
                return {
                    "orderId": "",
                    "status": "failed",
                    "error": f"市场不存在或已关闭: {error_str}",
                    "market_not_found": True,
                }
            else:
                logger.warning(f"token_id 验证时出现其他错误: {error_str}，继续尝试下单")
        
        self._ensure_api_creds()

        if IS_CLOB_V2:
            side_const = BUY if side.upper() == "BUY" else SELL
            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                try:
                    order_args = OrderArgs(
                        token_id=token_id,
                        price=price,
                        size=size,
                        side=side_const,
                    )
                    order = self.client.create_and_post_order(
                        order_args=order_args,
                        order_type=OrderType.GTC,
                    )
                    break
                except (PolyApiException, PolyException) as exc:
                    error_msg = str(exc)
                    if "425" in error_msg and attempt < max_attempts:
                        logger.warning("CLOB V2 returned 425 (service not ready), re-deriving API credentials and retrying (attempt %d/%d)", attempt, max_attempts)
                        try:
                            if hasattr(self.client, 'set_api_creds'):
                                self.client.set_api_creds(None)
                        except Exception:  # noqa: BLE001
                            pass
                        if self._ensure_api_creds():
                            time.sleep(1)
                            continue
                    logger.warning("CLOB V2 API error when placing order: %s", error_msg)
                    return {"orderId": "", "status": "failed", "error": error_msg}
                except Exception as exc:  # noqa: BLE001
                    error_msg = str(exc)
                    logger.warning("Unexpected CLOB V2 order error: %s", error_msg)
                    return {"orderId": "", "status": "failed", "error": error_msg}

            success, filled_size, error_msg = self._validate_order_response(order, size)
            order_id = None
            if isinstance(order, dict):
                order_id = order.get("orderID") or order.get("orderId") or order.get("order_id") or order.get("id")

            if success:
                logger.info(
                    "CLOB V2 order posted: %s %.4f @ %.4f | token_id=%s | filled=%s | order_id=%s",
                    side, size, price, token_id[:16], filled_size, order_id
                )
                return {
                    "orderId": order_id or "",
                    "status": "filled" if filled_size is not None and filled_size >= size else "pending",
                    "filled_size": filled_size,
                    "response": order,
                }
            logger.warning("CLOB V2 order response abnormal: %s | resp=%s", error_msg, order)
            return {"orderId": order_id or "", "status": "failed", "error": error_msg}

        # Check if we need to use OrderArgs or keyword arguments
        sig = inspect.signature(self.client.create_order)
        params = list(sig.parameters.keys())
        
        # Check if first parameter is order_args or similar
        first_param = params[0] if params else None
        
        if first_param and ('order_args' in first_param.lower() or 'order' in first_param.lower()):
            # New API style: use OrderArgs object
            if HAS_ORDER_ARGS and OrderArgs is not None:
                # Map side string to constant
                side_const = BUY if side.upper() == "BUY" else SELL
                
                logger.debug(
                    "创建订单参数 - 价格: %.2f, 数量: %.4f, 方向: %s, token_id: %s",
                    price, size, side, token_id
                )
                
                # 直接使用处理后的值创建 OrderArgs（不再进行多次格式化）
                order_args = OrderArgs(
                    price=price,
                    size=size,
                    side=side_const,
                    token_id=token_id,
                )
                
                # Create and sign order
                try:
                    signed_order = self.client.create_order(order_args)
                except Exception as exc:  # noqa: BLE001
                    # Handle errors during order creation (e.g., price validation)
                    error_msg = str(exc)
                    if 'price' in error_msg.lower() and ('min' in error_msg.lower() or 'max' in error_msg.lower()):
                        logger.warning("Price validation error: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": f"Invalid price: {error_msg}"}
                    elif 'not enough balance' in error_msg.lower() or 'allowance' in error_msg.lower():
                        logger.warning("Insufficient balance/allowance: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": error_msg}
                    elif 'invalid amounts' in error_msg.lower():
                        logger.warning("精度错误: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": error_msg}
                    else:
                        logger.warning("Error creating order: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": error_msg}
                
                # Check if we need to post the order separately
                # Try to post order, but handle cases where API credentials are not available or balance is insufficient
                if hasattr(self.client, 'post_order'):
                    try:
                        # 使用 GTC 订单类型（Good Till Cancel），提高成交率
                        order = self.client.post_order(signed_order, OrderType.GTC)
                        
                        # 验证订单响应
                        success, filled_size, error_msg = self._validate_order_response(order, size)
                        order_id = None
                        if isinstance(order, dict):
                            order_id = order.get("orderID") or order.get("orderId") or order.get("order_id") or order.get("id")
                        
                        if success:
                            logger.info(
                                "\033[1;32m【订单成功】已下单: %s %.4f @ %.2f | token_id=%s | 成交: %s | order_id=%s\033[0m",
                                side, size, price, token_id[:16], filled_size, order_id
                            )
                            return {
                                "orderId": order_id or "",
                                "status": "filled" if filled_size is not None and filled_size >= size else "pending",
                                "filled_size": filled_size,
                            }
                        else:
                            logger.warning(
                                "订单响应异常: %s | token_id=%s | resp=%s",
                                error_msg, token_id[:16], order
                            )
                            return {"orderId": order_id or "", "status": "failed", "error": error_msg}
                    except (PolyApiException, PolyException) as exc:
                        # Handle API exceptions (balance issues, etc.)
                        error_msg = str(exc)
                        if 'not enough balance' in error_msg.lower() or 'allowance' in error_msg.lower():
                            logger.warning(
                                "余额/授权不足 - 订单: %s %s @ %.2f, 数量: %.4f, 金额: %.2f USDC, 错误: %s",
                                side, token_id[:16], price, size, price * size, error_msg
                            )
                            return {"orderId": "", "status": "failed", "error": error_msg}
                        elif 'API Credentials' in error_msg or 'L2_AUTH' in error_msg:
                            logger.warning("API credentials not available, using signed order directly: %s", error_msg)
                            order = signed_order
                            return {"orderId": "", "status": "pending", "response": order}
                        elif 'invalid amounts' in error_msg.lower():
                            logger.warning("精度错误: %s", error_msg)
                            return {"orderId": "", "status": "failed", "error": error_msg}
                        else:
                            logger.warning("API error when posting order: %s", error_msg)
                            return {"orderId": "", "status": "failed", "error": error_msg}
                    except Exception as exc:  # noqa: BLE001
                        # Handle other exceptions
                        error_msg = str(exc)
                        logger.warning("Unexpected error when posting order: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": error_msg}
                else:
                    # Order might already be posted
                    order = signed_order
                    return {"orderId": "", "status": "pending", "response": order}
            else:
                # Fallback: try to create order_args dict
                logger.debug(
                    "创建订单参数（fallback） - 价格: %.2f, 数量: %.4f",
                    price, size
                )
                
                order_args = {
                    "price": price,
                    "size": size,
                    "side": side.lower(),
                    "token_id": token_id,
                }
                try:
                    signed_order = self.client.create_order(order_args)
                except Exception as exc:  # noqa: BLE001
                    error_msg = str(exc)
                    if 'price' in error_msg.lower() and ('min' in error_msg.lower() or 'max' in error_msg.lower()):
                        logger.warning("Price validation error: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": f"Invalid price: {error_msg}"}
                    elif 'not enough balance' in error_msg.lower() or 'allowance' in error_msg.lower():
                        logger.warning("Insufficient balance/allowance: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": error_msg}
                    elif 'invalid amounts' in error_msg.lower():
                        logger.warning("精度错误: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": error_msg}
                    else:
                        logger.warning("Error creating order: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": error_msg}
                
                if hasattr(self.client, 'post_order'):
                    try:
                        # 使用 GTC 订单类型
                        order = self.client.post_order(signed_order, OrderType.GTC)
                        # 验证订单响应
                        success, filled_size, error_msg = self._validate_order_response(order, size)
                        order_id = None
                        if isinstance(order, dict):
                            order_id = order.get("orderID") or order.get("orderId") or order.get("order_id") or order.get("id")
                        
                        if success:
                            logger.info(
                                "\033[1;32m【订单成功】已下单: %s %.4f @ %.2f | token_id=%s | 成交: %s | order_id=%s\033[0m",
                                side, size, price, token_id[:16], filled_size, order_id
                            )
                            return {
                                "orderId": order_id or "",
                                "status": "filled" if filled_size is not None and filled_size >= size else "pending",
                                "filled_size": filled_size,
                            }
                        else:
                            return {"orderId": order_id or "", "status": "failed", "error": error_msg}
                    except (PolyApiException, PolyException) as exc:
                        error_msg = str(exc)
                        if 'not enough balance' in error_msg.lower() or 'allowance' in error_msg.lower():
                            logger.warning("Insufficient balance/allowance for order: %s", error_msg)
                            return {"orderId": "", "status": "failed", "error": error_msg}
                        elif 'API Credentials' in error_msg or 'L2_AUTH' in error_msg:
                            logger.warning("API credentials not available, using signed order directly: %s", error_msg)
                            order = signed_order
                            return {"orderId": "", "status": "pending", "response": order}
                        elif 'invalid amounts' in error_msg.lower():
                            logger.warning("精度错误: %s", error_msg)
                            return {"orderId": "", "status": "failed", "error": error_msg}
                        else:
                            logger.warning("API error when posting order: %s", error_msg)
                            return {"orderId": "", "status": "failed", "error": error_msg}
                    except Exception as exc:  # noqa: BLE001
                        error_msg = str(exc)
                        logger.warning("Unexpected error when posting order: %s", error_msg)
                        return {"orderId": "", "status": "failed", "error": error_msg}
                else:
                    order = signed_order
                    return {"orderId": "", "status": "pending", "response": order}
        else:
            # Old API style: use keyword arguments
            order_kwargs = {}
            
            # Check which parameter name is used for market
            if 'market' in params:
                order_kwargs['market'] = market_id
            elif 'market_id' in params:
                order_kwargs['market_id'] = market_id
            elif 'token_id' in params:
                order_kwargs['token_id'] = token_id
            
            # Add other common parameters
            if 'side' in params:
                order_kwargs['side'] = side.lower()
            if 'size' in params:
                order_kwargs['size'] = size
            if 'price' in params:
                order_kwargs['price'] = price
            if 'time_in_force' in params:
                order_kwargs['time_in_force'] = "GTC"  # 改用 GTC
            elif 'tif' in params:
                order_kwargs['tif'] = "GTC"  # 改用 GTC
            
            try:
                order = self.client.create_order(**order_kwargs)
                logger.info("Placed order %s -> %s", token_id, order)
                return order
            except (PolyApiException, PolyException) as exc:
                error_msg = str(exc)
                if 'not enough balance' in error_msg.lower() or 'allowance' in error_msg.lower():
                    logger.warning("Insufficient balance/allowance for order: %s", error_msg)
                    return {"orderId": "", "status": "failed", "error": error_msg}
                else:
                    logger.warning("API error when creating order: %s", error_msg)
                    return {"orderId": "", "status": "failed", "error": error_msg}
            except Exception as exc:  # noqa: BLE001
                error_msg = str(exc)
                logger.warning("Unexpected error when creating order: %s", error_msg)
                return {"orderId": "", "status": "failed", "error": error_msg}

    def create_and_post_order(self, price: float, size: float, side: str, token_id: str) -> str:
        """
        Helper method for Dashboard API to place an order.
        Wraps place_order method.
        
        Args:
            price: Price to place order at
            size: Size to place order for
            side: 'BUY' or 'SELL'
            token_id: Token ID to trade
            
        Returns:
            Order ID as string, or empty string on failure
        """
        # We pass token_id as market_id since we might not have the market_id handy.
        # Most modern CLOB calls use token_id. market_id is mainly for logging here.
        result = self.place_order(
            side=side,
            price=price,
            size=size,
            market_id=token_id, # Use token_id as fallback for market_id
            token_id=token_id
        )
        return str(result.get("orderId") or result.get("order_id") or "")

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        """
        查询订单状态
        
        Args:
            order_id: 订单ID
        
        Returns:
            订单信息字典，包含status字段，如果查询失败返回None
        """
        if self.dry_run:
            logger.debug("[DRY RUN] get order %s", order_id)
            return None
        
        try:
            # 方法1：使用CLOB API查询单个订单
            if hasattr(self.client, 'get_order'):
                return self.client.get_order(order_id)
            
            # 方法2：查询所有订单，然后过滤
            if hasattr(self.client, 'get_orders'):
                orders = self.client.get_orders()
                if orders:
                    for order in orders:
                        # 检查多种可能的订单ID字段名
                        order_id_value = (
                            order.get("orderID") or 
                            order.get("order_id") or 
                            order.get("id") or
                            order.get("orderHash")
                        )
                        if order_id_value and str(order_id_value).lower() == order_id.lower():
                            return order
            
            # 方法3：尝试使用get_open_orders（如果存在）
            if hasattr(self.client, 'get_open_orders'):
                open_orders = self.client.get_open_orders()
                if open_orders:
                    for order in open_orders:
                        order_id_value = (
                            order.get("orderID") or 
                            order.get("order_id") or 
                            order.get("id") or
                            order.get("orderHash")
                        )
                        if order_id_value and str(order_id_value).lower() == order_id.lower():
                            return order
            
            # 如果找不到，可能订单已成交或不存在
            logger.debug("未找到订单 %s（可能已成交或不存在）", order_id[:16])
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询订单状态失败 (order_id: %s): %s", order_id[:16], exc)
            return None

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if self.dry_run:
            return {"success": True, "status": "simulated"}
        try:
            if hasattr(self.client, "cancel_orders"):
                result = self.client.cancel_orders([order_id])
            elif hasattr(self.client, "cancel_order"):
                result = self.client.cancel_order(order_id)
            else:
                return {"success": False, "error": "客户端不支持撤单"}
            if not isinstance(result, dict):
                return {"success": False, "error": "无法确认撤单结果"}
            if order_id in (result.get("canceled") or result.get("cancelled") or []):
                return {"success": True, "status": "canceled"}
            failures = result.get("not_canceled") or result.get("notCanceled") or {}
            return {"success": False, "error": str(failures.get(order_id) or "交易所未确认撤单")}
        except Exception as exc:
            logger.warning("Failed to cancel order %s: %s", order_id, exc)
            return {"success": False, "error": str(exc)}

