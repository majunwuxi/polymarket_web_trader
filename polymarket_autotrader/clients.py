from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from requests import Response, Session
from web3 import Web3

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    websocket = None  # type: ignore
def _poa_middleware():
    """
    Return a middleware for Polygon POA chains, or None if not available
    in the installed web3 version.
    """
    try:
        # Web3 v5/v6
        from web3.middleware import geth_poa_middleware

        return geth_poa_middleware
    except Exception:
        pass
    try:
        # Web3 v7+
        from web3.middleware.proof_of_authority import (  # type: ignore
            construct_poa_middleware,
        )

        return construct_poa_middleware()
    except Exception:
        return None

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    max_attempts: int = 3
    timeout: tuple[float, float] = (5.0, 15.0)  # connect, read (减少读取超时从30秒到15秒)
    backoff_base: float = 2.0
    linear_backoff: float = 1.0
    max_rate_limit_wait: float = 60.0  # 429限流最大等待时间（秒）


@dataclass
class OrderBook:
    bids: list[Any]
    asks: list[Any]


def retry_request(
    session: Session,
    method: str,
    url: str,
    retry_config: RetryConfig,
    heartbeat_callback: Optional[Any] = None,
    **kwargs: Any,
) -> Response:
    attempts = 0
    total_wait_time = 0.0
    max_total_wait = 90.0  # 总等待时间不超过90秒，留出余量避免超过120秒的心跳超时
    request_start_time = time.time()
    last_heartbeat_update = request_start_time
    heartbeat_update_interval = 10.0  # 每10秒更新一次心跳
    
    while True:
        attempts += 1
        try:
            resp = session.request(method, url, timeout=retry_config.timeout, **kwargs)
        except requests.RequestException as exc:
            logger.warning("Request error %s (attempt %s): %s", url, attempts, exc)
            if attempts >= retry_config.max_attempts:
                raise
            
            # 检查总等待时间
            wait_time = retry_config.linear_backoff * attempts
            if total_wait_time + wait_time > max_total_wait:
                wait_time = max_total_wait - total_wait_time
                if wait_time <= 0:
                    logger.warning(
                        "请求 %s 总等待时间已超过限制 %.1f 秒，停止重试",
                        url,
                        max_total_wait,
                    )
                    raise requests.RequestException(
                        f"Request failed after {attempts} attempts, total wait time exceeded {max_total_wait}s"
                    ) from exc
            
            total_wait_time += wait_time
            
            # 在等待期间定期更新心跳
            if heartbeat_callback and wait_time > 5.0:  # 如果等待时间超过5秒，在等待期间更新心跳
                elapsed_since_last_heartbeat = time.time() - last_heartbeat_update
                if elapsed_since_last_heartbeat >= heartbeat_update_interval:
                    try:
                        heartbeat_callback()
                        last_heartbeat_update = time.time()
                    except Exception:  # noqa: BLE001
                        pass  # 心跳更新失败不影响主流程
                
                # 如果等待时间很长，在等待过程中也更新心跳
                if wait_time > heartbeat_update_interval:
                    sleep_chunks = int(wait_time / heartbeat_update_interval)
                    chunk_time = heartbeat_update_interval
                    for _ in range(sleep_chunks):
                        time.sleep(chunk_time)
                        try:
                            heartbeat_callback()
                            last_heartbeat_update = time.time()
                        except Exception:  # noqa: BLE001
                            pass
                    # 剩余时间
                    remaining = wait_time - (sleep_chunks * chunk_time)
                    if remaining > 0:
                        time.sleep(remaining)
                else:
                    time.sleep(wait_time)
            else:
                time.sleep(wait_time)
            continue

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                wait = float(retry_after)
            else:
                wait = retry_config.backoff_base ** attempts
            
            # 限制最大等待时间，避免无限等待
            max_wait = getattr(retry_config, 'max_rate_limit_wait', 60.0)
            if wait > max_wait:
                logger.warning(
                    "Rate limited on %s (429). 请求的等待时间 %.2fs 超过最大限制 %.2fs，使用最大限制",
                    url, wait, max_wait
                )
                wait = max_wait
            
            # 检查总等待时间，如果超过限制则提前返回错误
            if total_wait_time + wait > max_total_wait:
                wait = max_total_wait - total_wait_time
                if wait <= 0:
                    logger.warning(
                        "Rate limited on %s (429). 总等待时间已超过限制 %.1f 秒，停止重试",
                        url,
                        max_total_wait,
                    )
                    resp.raise_for_status()
            
            total_wait_time += wait
            logger.warning(
                "Rate limited on %s (429). Waiting %.2fs before retry (attempt %d/%d, total wait %.1fs/%.1fs)",
                url, wait, attempts, retry_config.max_attempts, total_wait_time, max_total_wait
            )
            if attempts >= retry_config.max_attempts:
                resp.raise_for_status()
            
            # 在等待期间定期更新心跳
            if heartbeat_callback and wait > 5.0:  # 如果等待时间超过5秒，在等待期间更新心跳
                elapsed_since_last_heartbeat = time.time() - last_heartbeat_update
                if elapsed_since_last_heartbeat >= heartbeat_update_interval:
                    try:
                        heartbeat_callback()
                        last_heartbeat_update = time.time()
                    except Exception:  # noqa: BLE001
                        pass
                
                # 如果等待时间很长，在等待过程中也更新心跳
                if wait > heartbeat_update_interval:
                    sleep_chunks = int(wait / heartbeat_update_interval)
                    chunk_time = heartbeat_update_interval
                    for _ in range(sleep_chunks):
                        time.sleep(chunk_time)
                        try:
                            heartbeat_callback()
                            last_heartbeat_update = time.time()
                        except Exception:  # noqa: BLE001
                            pass
                    # 剩余时间
                    remaining = wait - (sleep_chunks * chunk_time)
                    if remaining > 0:
                        time.sleep(remaining)
                else:
                    time.sleep(wait)
            else:
                time.sleep(wait)
            continue

        if 400 <= resp.status_code < 500:
            # 404 Not Found is acceptable for query operations (resource doesn't exist)
            # 400 Bad Request for positions endpoint is also acceptable (API might not support it)
            # Other 4xx errors should raise
            if resp.status_code not in (400, 404):
                resp.raise_for_status()

        if 500 <= resp.status_code < 600:
            logger.warning(
                "Server error %s (status=%s). attempt=%s", url, resp.status_code, attempts
            )
            if attempts >= retry_config.max_attempts:
                # 对于 500 错误，增加更详细的错误信息，但不改变行为
                logger.error(
                    "API 服务器持续返回错误 (status=%d)，已重试 %d 次。这可能是 Polymarket API 的临时问题。",
                    resp.status_code, retry_config.max_attempts
                )
                resp.raise_for_status()
            # 对于 500 错误，使用更长的退避时间
            backoff_time = retry_config.linear_backoff * attempts * 2  # 加倍退避时间
            
            # 检查总等待时间
            if total_wait_time + backoff_time > max_total_wait:
                backoff_time = max_total_wait - total_wait_time
                if backoff_time <= 0:
                    logger.warning(
                        "Server error %s (status=%d). 总等待时间已超过限制 %.1f 秒，停止重试",
                        url,
                        resp.status_code,
                        max_total_wait,
                    )
                    resp.raise_for_status()
            
            total_wait_time += backoff_time
            
            # 在等待期间定期更新心跳
            if heartbeat_callback and backoff_time > 5.0:  # 如果等待时间超过5秒，在等待期间更新心跳
                elapsed_since_last_heartbeat = time.time() - last_heartbeat_update
                if elapsed_since_last_heartbeat >= heartbeat_update_interval:
                    try:
                        heartbeat_callback()
                        last_heartbeat_update = time.time()
                    except Exception:  # noqa: BLE001
                        pass
                
                # 如果等待时间很长，在等待过程中也更新心跳
                if backoff_time > heartbeat_update_interval:
                    sleep_chunks = int(backoff_time / heartbeat_update_interval)
                    chunk_time = heartbeat_update_interval
                    for _ in range(sleep_chunks):
                        time.sleep(chunk_time)
                        try:
                            heartbeat_callback()
                            last_heartbeat_update = time.time()
                        except Exception:  # noqa: BLE001
                            pass
                    # 剩余时间
                    remaining = backoff_time - (sleep_chunks * chunk_time)
                    if remaining > 0:
                        time.sleep(remaining)
                else:
                    time.sleep(backoff_time)
            else:
                time.sleep(backoff_time)
            continue

        return resp


class WebSocketPriceService:
    """
    WebSocket 价格服务，用于获取实时价格
    
    在单独线程中运行，维护 token_id -> price 的缓存
    """
    
    WSS_CLOB_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    def __init__(self) -> None:
        if not WEBSOCKET_AVAILABLE:
            raise RuntimeError("websocket-client 未安装。请执行: pip install websocket-client")
        
        self._price_cache: Dict[str, Dict[str, float]] = {}  # token_id -> {best_bid, best_ask, midpoint}
        self._cache_lock = threading.Lock()
        self._subscribed_tokens: set[str] = set()
        self._subscription_lock = threading.RLock()  # 使用 RLock 避免死锁（可重入锁）
        # 价格极端值保护：当某 token 的 midpoint 连续多次落在极端区间，禁用其 WS 订阅，避免日志刷屏与无效订阅
        self._disabled_tokens: set[str] = set()
        self._extreme_price_streak: Dict[str, int] = {}
        self._extreme_low_threshold: float = 0.10
        self._extreme_high_threshold: float = 0.95
        self._extreme_streak_limit: int = 5
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_closed = True
        self._stop_event = threading.Event()
        self._last_ping_time = 0.0
        self._ping_interval = 10.0  # 每10秒发送一次 PING

    def is_token_disabled(self, token_id: str) -> bool:
        with self._subscription_lock:
            return token_id in self._disabled_tokens

    def _disable_token_due_to_extreme_price(self, token_id: str, midpoint: float, streak: int) -> None:
        # Ensure we only disable once.
        with self._subscription_lock:
            if token_id in self._disabled_tokens:
                return
            self._disabled_tokens.add(token_id)
            self._subscribed_tokens.discard(token_id)

        with self._cache_lock:
            self._price_cache.pop(token_id, None)
            self._extreme_price_streak.pop(token_id, None)

            is_connected = not getattr(self, "_ws_closed", True)
            ws_obj = self._ws

        logger.warning(
            "WS 价格极端值连续命中，禁用订阅 (token_id: %s..., midpoint=%.4f, streak=%d, range=(<%.2f or >%.2f))",
            token_id[:20],
            midpoint,
            streak,
            self._extreme_low_threshold,
            self._extreme_high_threshold,
        )

        # Re-send subscription list to shrink server-side stream (best effort).
        if is_connected and ws_obj:
            self._send_subscription()
        
    def get_price(self, token_id: str, side: str = "midpoint", wait_time: float = 0.0) -> Optional[float]:
        """
        从缓存中获取价格，可选的等待时间
        
        Args:
            token_id: Token ID
            side: 价格类型 ("midpoint", "ask", "bid")
            wait_time: 如果缓存中没有价格，等待的时间（秒），用于等待 WebSocket 接收价格更新
            
        Returns:
            价格或 None
        """
        # 先检查一次缓存
        with self._cache_lock:
            token_data = self._price_cache.get(token_id)
            if token_data:
                side_lower = side.lower()
                if side_lower in ("midpoint", "mid"):
                    price = token_data.get("midpoint")
                elif side_lower in ("ask", "buy"):
                    price = token_data.get("best_ask")
                elif side_lower in ("bid", "sell"):
                    price = token_data.get("best_bid")
                else:
                    price = token_data.get("midpoint")
                
                if price is not None and price > 0:
                    return price
        
        # 如果缓存中没有价格且需要等待，等待一段时间后再次检查
        if wait_time > 0:
            import time
            time.sleep(wait_time)
            with self._cache_lock:
                token_data = self._price_cache.get(token_id)
                if token_data:
                    side_lower = side.lower()
                    if side_lower in ("midpoint", "mid"):
                        price = token_data.get("midpoint")
                    elif side_lower in ("ask", "buy"):
                        price = token_data.get("best_ask")
                    elif side_lower in ("bid", "sell"):
                        price = token_data.get("best_bid")
                    else:
                        price = token_data.get("midpoint")
                    
                    if price is not None and price > 0:
                        return price
        
        return None
    
    def subscribe_token(self, token_id: str) -> None:
        """订阅 token 的价格更新"""
        logger.debug("subscribe_token 收到完整 token_id (长度: %d): %s...%s", 
                    len(token_id), token_id[:20], token_id[-20:] if len(token_id) > 40 else "")
        if self.is_token_disabled(token_id):
            logger.debug("token 已被禁用（极端值保护），跳过订阅: %s", token_id[:20])
            return
        with self._subscription_lock:
            if token_id not in self._subscribed_tokens:
                logger.info("订阅 token: %s (完整长度: %d)", token_id[:20], len(token_id))
                self._subscribed_tokens.add(token_id)
                # 验证 token_id 没有被截断
                added_token = next((t for t in self._subscribed_tokens if t == token_id), None)
                if added_token:
                    logger.debug("token_id 已正确添加到订阅列表 (长度: %d)", len(added_token))
                # 如果 WebSocket 已连接，发送新的订阅
                is_connected = False
                with self._cache_lock:
                    is_connected = not self._ws_closed
                logger.debug("订阅 token 时 WebSocket 状态: is_connected=%s, _ws=%s", is_connected, self._ws is not None)
                if is_connected and self._ws:
                    logger.info("WebSocket 已连接，立即发送订阅消息")
                    self._send_subscription()
                else:
                    logger.info("WebSocket 未连接或未初始化，订阅将在连接建立时发送")
            else:
                logger.debug("token 已订阅，跳过重复订阅: %s", token_id[:20])
    
    def subscribe_tokens(self, token_ids: list[str]) -> None:
        """批量订阅 token 的价格更新"""
        with self._subscription_lock:
            need_resubscribe = False
            for token_id in token_ids:
                if token_id in self._disabled_tokens:
                    continue
                if token_id not in self._subscribed_tokens:
                    self._subscribed_tokens.add(token_id)
                    need_resubscribe = True
            
            # 如果 WebSocket 已连接且有新订阅，发送订阅消息
            if need_resubscribe:
                is_connected = False
                with self._cache_lock:
                    is_connected = not self._ws_closed
                if is_connected and self._ws:
                    self._send_subscription()
    
    def _send_subscription(self) -> None:
        """发送订阅消息"""
        logger.info("_send_subscription 被调用")
        with self._subscription_lock:
            if not self._subscribed_tokens:
                logger.warning("没有需要订阅的 token，跳过发送订阅消息")
                return
            
            token_list = list(self._subscribed_tokens)
            logger.info("准备发送订阅消息，共 %d 个 token", len(token_list))
            # 验证 token_id 长度（检查是否被截断）
            for idx, token in enumerate(token_list[:3]):
                logger.info("订阅消息中的 token #%d 长度: %d, 前20字符: %s", idx + 1, len(token), token[:20])
            
            payload = {"assets_ids": token_list, "type": "market"}
            try:
                logger.info("检查 WebSocket 对象: _ws=%s", self._ws is not None)
                if not self._ws:
                    logger.error("WebSocket 未初始化，无法发送订阅消息")
                    return
                payload_str = json.dumps(payload)
                logger.info("发送订阅消息 payload (长度: %d): %s", len(payload_str), payload_str[:200])
                self._ws.send(payload_str)
                logger.info("WebSocket 发送订阅消息成功: %d 个 token (前3个长度: %s)", 
                          len(token_list), [len(str(t)) for t in token_list[:3]])
            except Exception as exc:  # noqa: BLE001
                logger.error("WebSocket 发送订阅消息失败: %s", exc, exc_info=True)
    
    def _process_ws_payload(self, payload: dict[str, Any]) -> None:
        """处理一条已解析的 WebSocket payload（dict）"""
        if not isinstance(payload, dict) or not payload:
            return

        event_type = (
            (payload.get("event_type") or payload.get("type") or payload.get("event") or "")
        ).lower()
        if not event_type:
            if "price_changes" in payload:
                event_type = "price_change"
            elif "bids" in payload or "asks" in payload:
                event_type = "book"

        # 记录前几条消息的详细信息，帮助调试
        if getattr(self, "_message_count", 0) <= 5:
            logger.info(
                "收到 WebSocket payload: event_type=%s, keys=%s",
                event_type,
                list(payload.keys())[:10],
            )
        elif event_type in ("book", "price_change"):
            logger.debug("收到 WebSocket %s payload", event_type)

        # 处理 book 事件（完整订单簿）
        if event_type == "book":
            asset_id = str(payload.get("asset_id") or "")
            if not asset_id:
                return
            
            # 验证 asset_id 是否在订阅列表中
            with self._subscription_lock:
                is_subscribed = asset_id in self._subscribed_tokens
                if self._message_count <= 5:
                    logger.info(
                        "收到 book 事件 - asset_id 长度: %d, 前20字符: %s, 已订阅: %s",
                        len(asset_id),
                        asset_id[:20],
                        is_subscribed,
                    )
            if not is_subscribed:
                return
            
            bids = payload.get("bids") or []
            asks = payload.get("asks") or []

            def _px(item: Any) -> Optional[float]:
                try:
                    if isinstance(item, dict):
                        v = item.get("price") or item.get("px") or item.get("p")
                        if v is None:
                            return None
                        f = float(v)
                        return f if f > 0 else None
                    if isinstance(item, (list, tuple)) and item:
                        f = float(item[0])
                        return f if f > 0 else None
                    f = float(item)
                    return f if f > 0 else None
                except Exception:  # noqa: BLE001
                    return None

            best_bid = None
            if isinstance(bids, list) and bids:
                for it in bids:
                    p = _px(it)
                    if p is None:
                        continue
                    best_bid = p if best_bid is None else max(best_bid, p)

            best_ask = None
            if isinstance(asks, list) and asks:
                for it in asks:
                    p = _px(it)
                    if p is None:
                        continue
                    best_ask = p if best_ask is None else min(best_ask, p)
            
            # 更新价格缓存
            if best_bid is not None or best_ask is not None:
                if self._message_count <= 5:
                    logger.info("更新价格缓存 - asset_id: %s (长度: %d), best_bid: %s, best_ask: %s", 
                              asset_id[:20], len(asset_id), best_bid, best_ask)
                self._update_price_cache(asset_id, best_bid, best_ask)
        
        # 处理 price_change 事件（价格变化）
        elif event_type == "price_change":
            price_changes = payload.get("price_changes") or []
            if isinstance(price_changes, list) and len(price_changes) > 0:
                # price_changes 是一个数组，每个元素包含 asset_id 和 best_bid/best_ask
                for pc in price_changes:
                    if not isinstance(pc, dict):
                        continue
                    
                    asset_id = str(pc.get("asset_id") or "")
                    if not asset_id:
                        continue
                    
                    # 验证 asset_id
                    with self._subscription_lock:
                        is_subscribed = asset_id in self._subscribed_tokens
                        if self._message_count <= 5:
                            logger.info(
                                "收到 price_change 事件 - asset_id 长度: %d, 前20字符: %s, 已订阅: %s",
                                len(asset_id),
                                asset_id[:20],
                                is_subscribed,
                            )
                    if not is_subscribed:
                        continue
                    
                    # 从 price_change 事件中提取 best_bid 和 best_ask
                    pc_bid = pc.get("best_bid")
                    pc_ask = pc.get("best_ask")
                    
                    best_bid = None
                    best_ask = None
                    
                    if pc_bid is not None:
                        try:
                            best_bid = float(pc_bid)
                            if best_bid <= 0:
                                best_bid = None
                        except (ValueError, TypeError):
                            pass
                    
                    if pc_ask is not None:
                        try:
                            best_ask = float(pc_ask)
                            if best_ask <= 0:
                                best_ask = None
                        except (ValueError, TypeError):
                            pass
                    
                    # 更新价格缓存
                    if best_bid is not None or best_ask is not None:
                        self._update_price_cache(asset_id, best_bid, best_ask)

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        """处理 WebSocket 消息"""
        # 过滤无效消息：空消息、PONG、非 JSON 消息
        if not message or message == "PONG":
            return

        if not isinstance(message, str):
            return

        # 检查是否是 JSON 格式（以 { 或 [ 开头）
        if len(message) > 0 and message[0] not in ("{", "["):
            logger.debug("收到非 JSON WebSocket 消息: %s", message[:50])
            return

        try:
            data = json.loads(message)
        except json.JSONDecodeError as exc:
            logger.debug("WebSocket 消息 JSON 解析失败: %s, 消息: %s", exc, message[:100])
            return

        # 初始化消息计数器（如果不存在）
        if not hasattr(self, "_message_count"):
            self._message_count = 0
        self._message_count += 1

        # 顶层可能是 list（常见：订阅后返回 book 快照数组）或 dict（带 event_type 的增量事件）
        if isinstance(data, list):
            if self._message_count <= 5:
                logger.info("收到 WebSocket 列表消息 #%d: len=%d", self._message_count, len(data))
            for item in data:
                if isinstance(item, dict):
                    self._process_ws_payload(item)
            return

        if isinstance(data, dict):
            if self._message_count <= 5:
                event_type = (
                    (data.get("event_type") or data.get("type") or data.get("event") or "")
                ).lower()
                logger.info(
                    "收到 WebSocket 消息 #%d: event_type=%s, keys=%s",
                    self._message_count,
                    event_type,
                    list(data.keys())[:10],
                )
            self._process_ws_payload(data)
            return
    
    def _update_price_cache(self, asset_id: str, best_bid: Optional[float], best_ask: Optional[float]) -> None:
        """更新价格缓存"""
        if not asset_id:
            return

        midpoint: Optional[float] = None
        current_bid: Optional[float] = None
        current_ask: Optional[float] = None
        streak: int = 0
        should_disable: bool = False

        with self._cache_lock:
            token_data = self._price_cache.get(asset_id, {})

            # 更新 bid 和 ask（如果提供了新值）
            if best_bid is not None:
                token_data["best_bid"] = best_bid
            if best_ask is not None:
                token_data["best_ask"] = best_ask

            # 计算 midpoint
            current_bid = token_data.get("best_bid")
            current_ask = token_data.get("best_ask")

            if current_bid is not None and current_ask is not None:
                midpoint = (current_bid + current_ask) / 2.0
            elif current_ask is not None:
                midpoint = current_ask
            elif current_bid is not None:
                midpoint = current_bid

            if midpoint is not None:
                token_data["midpoint"] = midpoint

            self._price_cache[asset_id] = token_data

            # 极端值计数：连续命中才触发禁用
            if midpoint is not None and (
                midpoint < self._extreme_low_threshold or midpoint > self._extreme_high_threshold
            ):
                streak = int(self._extreme_price_streak.get(asset_id, 0) or 0) + 1
                self._extreme_price_streak[asset_id] = streak
                if streak >= self._extreme_streak_limit:
                    should_disable = True
            else:
                self._extreme_price_streak.pop(asset_id, None)

        if should_disable and midpoint is not None:
            self._disable_token_due_to_extreme_price(asset_id, midpoint, streak)
            return

        logger.info(
            "WebSocket 更新价格 (token_id: %s, bid: %s, ask: %s, midpoint: %s)",
            asset_id[:20],
            current_bid,
            current_ask,
            midpoint,
        )
    
    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        """WebSocket 错误处理"""
        logger.warning("WebSocket 错误: %s", error)
    
    def _on_close(self, ws: websocket.WebSocketApp, close_status_code: int, close_msg: str) -> None:
        """WebSocket 关闭处理"""
        logger.info("WebSocket 连接已关闭 (status: %s, msg: %s)", close_status_code, close_msg)
        # 标记连接已关闭，由 run_ws 循环负责重连
        with self._cache_lock:
            self._ws_closed = True
    
    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        """WebSocket 打开处理"""
        try:
            logger.info("WebSocket 连接已建立")
            self._last_ping_time = time.time()
            # 标记连接已打开
            with self._cache_lock:
                self._ws_closed = False
            
            # 参考参考代码：连接建立后立即发送订阅消息，不等待
            # 参考代码中：async with websockets.connect(...) as ws: await ws.send(json.dumps(payload))
            # 这说明连接建立后应该立即发送订阅消息，不需要等待
            with self._subscription_lock:
                token_count = len(self._subscribed_tokens)
                logger.info("WebSocket 连接建立，当前已订阅 %d 个 token", token_count)
            
            if token_count > 0:
                logger.info("立即发送初始订阅消息（参考参考代码的做法）...")
                # 立即发送，不等待（参考参考代码：连接建立后立即发送）
                self._send_subscription()
            else:
                logger.info("WebSocket 连接建立时还没有订阅任何 token，将在首次请求价格时订阅")
        except Exception as exc:  # noqa: BLE001
            logger.error("WebSocket _on_open 处理出错: %s", exc, exc_info=True)
    
    def _start_websocket(self) -> None:
        """启动 WebSocket 连接（在单独线程中运行）"""
        if self._stop_event.is_set():
            return
        
        # 如果 WebSocket 线程正在运行，不要重复启动
        if self._ws_thread and self._ws_thread.is_alive():
            logger.debug("WebSocket 线程已在运行，跳过启动")
            return
        
        try:
            self._ws = websocket.WebSocketApp(
                self.WSS_CLOB_MARKET,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open,
            )
            
            # 初始化连接状态
            with self._cache_lock:
                self._ws_closed = True
            
            # 在后台线程中运行 WebSocket
            def run_ws():
                reconnect_count = 0
                while not self._stop_event.is_set():
                    try:
                        reconnect_count += 1
                        logger.info("WebSocket 连接/重连尝试 #%d", reconnect_count)
                        
                        # 确保之前的连接已关闭
                        if self._ws:
                            try:
                                logger.debug("关闭旧的 WebSocket 连接...")
                                self._ws.close()
                            except Exception:  # noqa: BLE001
                                pass
                        
                        # 创建新的 WebSocket 连接
                        logger.info("创建新的 WebSocket 连接: %s", self.WSS_CLOB_MARKET)
                        self._ws = websocket.WebSocketApp(
                            self.WSS_CLOB_MARKET,
                            on_message=self._on_message,
                            on_error=self._on_error,
                            on_close=self._on_close,
                            on_open=self._on_open,
                        )
                        
                        # 运行 WebSocket（阻塞直到连接关闭）
                        logger.debug("开始运行 WebSocket run_forever...")
                        self._ws.run_forever(ping_interval=None)
                        logger.info("WebSocket run_forever 退出（连接已断开）")
                        
                        # 如果连接断开且未停止，等待后重连
                        if not self._stop_event.is_set():
                            logger.info("WebSocket 断开，等待 5 秒后重连...")
                            time.sleep(5)
                    except Exception as exc:  # noqa: BLE001
                        if not self._stop_event.is_set():
                            logger.warning("WebSocket 运行错误: %s，等待 5 秒后重连...", exc, exc_info=True)
                            time.sleep(5)
            
            self._ws_thread = threading.Thread(target=run_ws, daemon=True, name="WebSocketPriceService")
            self._ws_thread.start()
            
            # 启动 PING 线程
            def ping_loop():
                while not self._stop_event.is_set():
                    time.sleep(1)  # 每秒检查一次
                    # 检查连接状态
                    is_connected = False
                    with self._cache_lock:
                        is_connected = not self._ws_closed
                    
                    if is_connected and self._ws:
                        now = time.time()
                        if now - self._last_ping_time >= self._ping_interval:
                            try:
                                self._ws.send("PING")
                                self._last_ping_time = now
                            except Exception:  # noqa: BLE001
                                # 如果发送失败，标记连接已关闭
                                with self._cache_lock:
                                    self._ws_closed = True
                                pass
            
            ping_thread = threading.Thread(target=ping_loop, daemon=True, name="WebSocketPing")
            ping_thread.start()
            
        except Exception as exc:  # noqa: BLE001
            logger.error("启动 WebSocket 失败: %s", exc)
    
    def start(self) -> None:
        """启动 WebSocket 服务"""
        if not WEBSOCKET_AVAILABLE:
            logger.warning("websocket-client 未安装，WebSocket 价格服务不可用")
            return
        
        if self._ws_thread and self._ws_thread.is_alive():
            logger.debug("WebSocket 服务已在运行")
            return
        
        self._stop_event.clear()
        logger.info("启动 WebSocket 价格服务...")
        self._start_websocket()
    
    def stop(self) -> None:
        """停止 WebSocket 服务"""
        logger.info("停止 WebSocket 价格服务...")
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
    
    def is_running(self) -> bool:
        """检查 WebSocket 服务是否运行中"""
        is_connected = False
        with self._cache_lock:
            is_connected = not self._ws_closed
        return (self._ws_thread is not None and 
                self._ws_thread.is_alive() and 
                is_connected)


class PolymarketDataClient:
    @staticmethod
    def _extract_game_start_time_from_market_payload(payload: Any) -> str:
        """
        Extract a game start time string from a Gamma market payload.

        The field may live on the market itself, its nested `event`, or within
        nested structures depending on market type and API version.
        """
        if not isinstance(payload, dict):
            return ""

        def _extract(obj: Any) -> str:
            if not isinstance(obj, dict):
                return ""
            for key in (
                "gameStartTime",
                "game_start_time",
                "startTime",
                "start_time",
                "startDateTime",
                "start_date_time",
                "scheduledStartTime",
                "scheduled_start_time",
                "startDate",
                "start_date",
                "startsAt",
                "starts_at",
            ):
                val = obj.get(key)
                if val:
                    return str(val)
            nested = obj.get("event")
            if isinstance(nested, dict):
                hit = _extract(nested)
                if hit:
                    return hit
            return ""

        start = _extract(payload)
        if start:
            return start

        # Some payloads include multiple markets or nested event objects.
        event_obj = payload.get("event")
        if isinstance(event_obj, dict):
            start = _extract(event_obj)
            if start:
                return start

        markets = payload.get("markets") or []
        if isinstance(markets, list):
            for m in markets:
                start = _extract(m)
                if start:
                    return start
        return ""

    def __init__(
        self,
        gamma_base_url: str,
        data_api_base_url: str,
        clob_host: str,
        retry_config: Optional[RetryConfig] = None,
        session: Optional[Session] = None,
        enable_websocket: bool = True,
    ) -> None:
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.data_api_base_url = data_api_base_url.rstrip("/")
        self.clob_host = clob_host.rstrip("/")
        self.retry_config = retry_config or RetryConfig()
        self.session = session or requests.Session()
        
        # WebSocket 价格服务
        self.ws_price_service: Optional[WebSocketPriceService] = None
        if enable_websocket and WEBSOCKET_AVAILABLE:
            try:
                self.ws_price_service = WebSocketPriceService()
                self.ws_price_service.start()
                logger.info("WebSocket 价格服务已启动")
            except Exception as exc:  # noqa: BLE001
                logger.warning("启动 WebSocket 价格服务失败: %s，将只使用 REST API", exc)
                self.ws_price_service = None
        elif enable_websocket and not WEBSOCKET_AVAILABLE:
            logger.warning("websocket-client 未安装，WebSocket 价格服务不可用")

    def fetch_positions_for_user(self, address: str, heartbeat_callback: Optional[Any] = None) -> list[dict[str, Any]]:
        """Read a complete Data API v2 position snapshot before returning it."""
        url = f"{self.data_api_base_url}/v2/positions"
        positions: list[dict[str, Any]] = []
        cursor = None
        seen: set[str] = set()
        while True:
            params = {"user": address.lower(), "limit": 500, "filter_amount": 0}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = retry_request(self.session, "GET", url, self.retry_config, heartbeat_callback=heartbeat_callback, params=params)
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not isinstance(payload.get("pagination"), dict):
                    raise ValueError("持仓响应格式无效")
                page = payload["data"]
                for row in page:
                    if not isinstance(row, dict) or not row.get("token_id") or row.get("current_size") is None:
                        raise ValueError("持仓记录缺少资产或数量")
                    # Preserve v2 fields and map values used by the existing dashboard.
                    mapped = dict(row)
                    mapped.update({"asset": str(row["token_id"]), "size": row["current_size"],
                                   "avgPrice": row.get("avg_price"), "curPrice": row.get("current_price"),
                                   "currentValue": row.get("current_value"), "initialValue": row.get("entry_cost_usdc"),
                                   "cashPnl": row.get("unrealized_pnl"), "percentPnl": row.get("percent_pnl")})
                    positions.append(mapped)
                pagination = payload["pagination"]
                next_cursor = pagination.get("next_cursor")
                has_more = pagination.get("has_more")
                if not isinstance(has_more, bool) or has_more != bool(next_cursor):
                    raise ValueError("持仓分页信息无效")
                if not next_cursor:
                    return positions
                if not isinstance(next_cursor, str) or next_cursor in seen or len(seen) >= 10000:
                    raise ValueError("持仓分页游标无效或重复")
                seen.add(next_cursor)
                cursor = next_cursor
            except (requests.RequestException, ValueError, TypeError) as exc:
                raise RuntimeError("无法完整读取 Data API v2 持仓") from exc

    def _find_market_by_filter(self, field: str, value: str, asset_id: str | None = None) -> dict[str, Any]:
        """Query Gamma and verify the identifier rather than trusting an arbitrary result."""
        if field not in ("condition_ids", "clob_token_ids", "position_ids") or not value:
            return {}
        try:
            params = {field: value, "closed": "any", "limit": 100}
            if field == "position_ids":
                params["version"] = "v2"
            response = retry_request(self.session, "GET", f"{self.gamma_base_url}/markets", self.retry_config, params=params)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                return {}
            matches = []
            for market in rows:
                if not isinstance(market, dict):
                    continue
                if field == "condition_ids" and str(market.get("conditionId") or market.get("condition_id") or "").lower() != value.lower():
                    continue
                expected_asset = asset_id or (value if field != "condition_ids" else None)
                if expected_asset and expected_asset not in self._market_asset_ids(market).values():
                    continue
                if market.get("id"):
                    matches.append(market)
            return matches[0] if len(matches) == 1 else {}
        except (requests.RequestException, ValueError, TypeError):
            return {}

    def get_market_for_trade(self, trade: dict[str, Any]) -> dict[str, Any]:
        token_id = next((str(trade[k]) for k in ("asset", "assetId", "asset_id", "tokenId", "token_id", "token") if trade.get(k)), None)
        slug = str(trade.get("slug") or "").strip()
        market_key = str(trade.get("market") or trade.get("market_id") or "").strip()
        if market_key.startswith("0x"):
            market = self._find_market_by_filter("condition_ids", market_key, token_id)
            if market:
                return market
        elif market_key.isdigit():
            try:
                resp = retry_request(self.session, "GET", f"{self.gamma_base_url}/markets/{market_key}", self.retry_config)
                resp.raise_for_status()
                market = resp.json()
                if isinstance(market, dict) and str(market.get("id")) == market_key and (not token_id or token_id in self._market_asset_ids(market).values()):
                    return market
            except (requests.RequestException, ValueError):
                pass
        if market_key:
            return {}
        if slug:
            market_id = self.get_market_id_for_slug(slug)
            if market_id and market_id != market_key:
                market = self.get_market_for_trade({"market_id": market_id, "asset": token_id})
                if market:
                    return market
        if token_id:
            for field in ("clob_token_ids", "position_ids"):
                market = self._find_market_by_filter(field, token_id)
                if market:
                    return market
        return {}

    def get_token_id_for_trade(self, trade: dict[str, Any]) -> Optional[str]:
        for key in ["asset", "assetId", "asset_id", "tokenId", "token_id", "token"]:
            if key in trade and trade[key]:
                return str(trade[key])
        # attempt from clobTokenIds mapping
        market = trade.get("market") or trade.get("market_id")
        if not market:
            return None
        market_info = self.get_market_for_trade({"market": market})
        mapping = self._market_asset_ids(market_info)
        outcome = trade.get("outcome") or trade.get("side")
        if isinstance(mapping, dict) and outcome in mapping:
            return mapping[outcome]
        return None

    @staticmethod
    def _market_asset_ids(market: dict[str, Any]) -> dict[str, str]:
        """Return outcome IDs in Gamma order for the market's protocol."""
        version = str(market.get("version") or "v1").lower()
        if version not in ("v1", "v2"):
            return {}
        raw_ids = (market.get("positionIds") or market.get("position_ids")) if version == "v2" else (market.get("clobTokenIds") or market.get("clob_token_ids"))
        raw_outcomes = market.get("outcomes") or market.get("outcomeNames")

        def parse(raw: Any) -> list[Any]:
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    return []
            return raw if isinstance(raw, list) else []

        ids, outcomes = parse(raw_ids), parse(raw_outcomes)
        if not ids or len(ids) != len(outcomes):
            return {}
        if any(not isinstance(x, str) or not x.isdigit() for x in ids):
            return {}
        if any(not isinstance(x, str) or not x.strip() for x in outcomes):
            return {}
        if len(set(outcomes)) != len(outcomes) or len(set(ids)) != len(ids):
            return {}
        return dict(zip(outcomes, ids))

    def get_clob_token_ids(self, market_id: str) -> dict[str, str]:
        """Compatibility name: values are CTF token IDs or V2 position IDs."""
        try:
            response = retry_request(self.session, "GET", f"{self.gamma_base_url}/markets/{market_id}", self.retry_config)
            if response.status_code != 200:
                return {}
            market = response.json()
            return self._market_asset_ids(market) if isinstance(market, dict) else {}
        except (requests.RequestException, ValueError) as exc:
            logger.warning("无法读取市场资产 %s: %s", market_id, exc)
            return {}

    def get_market_id_for_token(self, token_id: str) -> Optional[str]:
        """Return the Gamma market ID for a verified outcome asset."""
        if not token_id:
            return None
        market = self.get_market_for_trade({"asset": str(token_id)})
        return str(market["id"]) if market.get("id") else None

    def get_order_book(self, token_id: str) -> Optional[OrderBook]:
        """
        Fetch order book from CLOB REST `/book?token_id=...`.
        """
        if not token_id:
            logger.warning("get_order_book called with empty token_id")
            return None
        url = f"{self.clob_host}/book"
        try:
            resp = retry_request(self.session, "GET", url, self.retry_config, params={"token_id": str(token_id)})
            if resp.status_code != 200:
                logger.warning(f"get_order_book failed: {resp.status_code} {resp.text} for token {token_id}")
                return None
            data = resp.json()
            if not isinstance(data, dict):
                logger.warning(f"get_order_book returned non-dict: {data}")
                return None
            
            bids = data.get("bids") or []
            asks = data.get("asks") or []
            return OrderBook(bids=bids, asks=asks)
        except Exception as e:  # noqa: BLE001
            logger.error(f"get_order_book exception: {e}")
            return None

    def get_midpoint_from_clob_book(self, token_id: str) -> Optional[float]:
        """
        Fetch midpoint price from CLOB REST `/book?token_id=...`.

        Returns midpoint if bids/asks exist; falls back to best bid/ask if only one side exists.
        """
        if not token_id:
            return None
        url = f"{self.clob_host}/book"
        try:
            resp = retry_request(self.session, "GET", url, self.retry_config, params={"token_id": str(token_id)})
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            bids = data.get("bids") or []
            asks = data.get("asks") or []

            def _px(item: Any) -> Optional[float]:
                try:
                    if isinstance(item, dict):
                        v = item.get("price") or item.get("px") or item.get("p")
                        if v is None:
                            return None
                        f = float(v)
                        return f if f > 0 else None
                    if isinstance(item, (list, tuple)) and item:
                        f = float(item[0])
                        return f if f > 0 else None
                    f = float(item)
                    return f if f > 0 else None
                except Exception:  # noqa: BLE001
                    return None

            best_bid = None
            if isinstance(bids, list) and bids:
                for it in bids:
                    p = _px(it)
                    if p is None:
                        continue
                    best_bid = p if best_bid is None else max(best_bid, p)

            best_ask = None
            if isinstance(asks, list) and asks:
                for it in asks:
                    p = _px(it)
                    if p is None:
                        continue
                    best_ask = p if best_ask is None else min(best_ask, p)
            if best_bid is not None and best_ask is not None:
                return (best_bid + best_ask) / 2.0
            if best_ask is not None:
                return best_ask
            if best_bid is not None:
                return best_bid
            return None
        except Exception:  # noqa: BLE001
            return None

    def get_market_id_for_slug(self, slug: str) -> Optional[str]:
        if not slug:
            return None
        from urllib.parse import quote
        try:
            resp = retry_request(self.session, "GET", f"{self.gamma_base_url}/markets/slug/{quote(slug, safe='')}", self.retry_config)
            resp.raise_for_status()
            market = resp.json()
            if isinstance(market, dict) and market.get("slug") == slug and market.get("id"):
                return str(market["id"])
        except (requests.RequestException, ValueError):
            pass
        return self.get_market_id_for_event_slug(slug)

    def get_market_id_for_event_slug(self, event_slug: str, token_id: Optional[str] = None) -> Optional[str]:
        event = self.get_event_for_slug(event_slug)
        markets = [m for m in (event.get("markets") or []) if isinstance(m, dict) and m.get("id")]
        if token_id:
            markets = [m for m in markets if str(token_id) in self._market_asset_ids(m).values()]
        return str(markets[0]["id"]) if len(markets) == 1 else None

    def get_market_ids_for_event_slug(self, slug: str) -> list[str]:
        event = self.get_event_for_slug(slug)
        return [str(m["id"]) for m in (event.get("markets") or []) if isinstance(m, dict) and m.get("id")]

    def get_event_for_slug(self, slug: str) -> dict[str, Any]:
        if not slug:
            return {}
        from urllib.parse import quote
        try:
            resp = retry_request(self.session, "GET", f"{self.gamma_base_url}/events/slug/{quote(slug, safe='')}", self.retry_config)
            resp.raise_for_status()
            event = resp.json()
            return event if isinstance(event, dict) and event.get("slug") == slug else {}
        except (requests.RequestException, ValueError):
            return {}

    def get_market_info_for_token(self, token_id: str) -> dict[str, Any]:
        """
        通过 token_id 获取市场信息和对应的 outcome 名称
        
        Returns:
            包含 market_title 和 outcome_name 的字典
        """
        try:
            # 先获取 market_id
            market_id = self.get_market_id_for_token(token_id)
            if not market_id:
                return {}
            
            # 获取市场信息
            market_info = self.get_market_for_trade({"market": market_id, "asset": token_id})
            if not market_info:
                return {}
            
            # 提取市场标题
            market_title = market_info.get("question") or market_info.get("title") or ""

            # 尝试提取 eventId / eventSlug（用于同结果聚合集中度）
            event_id = market_info.get("eventId") or market_info.get("event_id") or ""
            event_slug = market_info.get("eventSlug") or market_info.get("event_slug") or ""
            event_obj = market_info.get("event")
            if isinstance(event_obj, dict):
                event_id = event_id or event_obj.get("id") or event_obj.get("eventId") or ""
                event_slug = event_slug or event_obj.get("slug") or event_obj.get("eventSlug") or ""
            
            # 获取 clobTokenIds 映射，找到对应的 outcome
            clob_token_ids = self.get_clob_token_ids(market_id)
            outcome_name = ""
            
            if isinstance(clob_token_ids, dict):
                # 查找 token_id 对应的 outcome
                for outcome, tid in clob_token_ids.items():
                    if str(tid) == str(token_id):
                        outcome_name = outcome
                        break
            
            # 如果没找到，尝试从 outcomes 数组中查找
            if not outcome_name:
                outcomes = market_info.get("outcomes") or market_info.get("outcomeTokens") or []
                if isinstance(outcomes, list):
                    for outcome in outcomes:
                        if not isinstance(outcome, dict):
                            continue
                        outcome_token_id = outcome.get("tokenId") or outcome.get("token_id") or outcome.get("id")
                        if str(outcome_token_id) == str(token_id):
                            outcome_name = outcome.get("name") or outcome.get("title") or outcome.get("outcome") or ""
                            break
            
            # 提取比赛开始时间（优先从 Gamma market/event payload 获取，不依赖 data-api /events/{slug}）
            game_start_time = self._extract_game_start_time_from_market_payload(market_info)
            
            # 提取 slug (优先使用 market slug，如果没有则使用 event slug)
            market_slug = market_info.get("slug") or market_info.get("marketSlug") or market_info.get("market_slug") or ""
            if not market_slug and event_slug:
                market_slug = event_slug

            return {
                "market_title": market_title,
                "outcome_name": outcome_name,
                "market_id": market_id,
                "eventId": str(event_id) if event_id else "",
                "eventSlug": str(event_slug) if event_slug else "",
                "slug": str(market_slug) if market_slug else "",
                "market_slug": str(market_slug) if market_slug else "",
                "game_start_time": game_start_time,  # 新增：比赛开始时间
            }
        except Exception as exc:  # noqa: BLE001
            token_id_str = str(token_id)
            logger.debug("获取市场信息时出错 (token_id: %s): %s", token_id_str[:16] if len(token_id_str) > 16 else token_id_str, exc)
            return {}

    # NOTE: eventSlug -> data-api /events/{slug} is not reliable for all sports slugs (may 404).
    # We intentionally avoid relying on it for stop-loss logic; use Gamma market/event payload instead.

    def get_best_price_for_token(self, token_id: str, side: str = "midpoint") -> Optional[float]:
        """
        通过 token_id 使用 WebSocket 获取价格
        
        完全按照用户提供的示例代码逻辑实现：
        1. 订阅 token_id
        2. 从 WebSocket 缓存获取价格
        3. 如果没有，等待一段时间让 WebSocket 接收价格更新
        
        Args:
            token_id: Token ID
            side: 价格类型 ("midpoint", "ask", "bid")
            
        Returns:
            价格或 None
        """
        if not self.ws_price_service:
            logger.warning("WebSocket 价格服务不可用，无法获取价格 (token_id: %s)", token_id[:20])
            return None

        if self.ws_price_service.is_token_disabled(token_id):
            logger.debug(
                "token 已被禁用（极端值保护），跳过 WebSocket 获取价格 (token_id: %s..., side: %s)",
                token_id[:20],
                side,
            )
            return None

        logger.debug(
            "get_best_price_for_token 被调用 (token_id 长度: %d, 前20字符: %s..., side: %s)",
            len(token_id),
            token_id[:20],
            side,
        )
        
        # 确保已订阅该 token（按照示例代码的方式）
        logger.info("调用 subscribe_token (token_id 长度: %d, 前20字符: %s...)", len(token_id), token_id[:20])
        self.ws_price_service.subscribe_token(token_id)
        
        # 先尝试从缓存获取（不等待）
        logger.debug("尝试从 WebSocket 缓存获取价格 (token_id: %s)", token_id[:20])
        price = self.ws_price_service.get_price(token_id, side, wait_time=0.0)
        if price is not None and price > 0:
            # 将价格获取的info日志改为debug级别，减少日志量
            logger.debug("从 WebSocket 缓存获取价格: %.4f (token_id: %s, side: %s)", price, token_id[:20], side)
            return price
        
        # 如果缓存中没有，等待2秒让 WebSocket 接收价格更新（类似示例代码中的逻辑）
        logger.debug("WebSocket 缓存中没有价格，等待 2 秒接收价格更新 (token_id: %s)", token_id[:20])
        price = self.ws_price_service.get_price(token_id, side, wait_time=2.0)
        
        if price is not None and price > 0:
            # 将价格获取的info日志改为debug级别，减少日志量
            logger.debug("从 WebSocket 获取价格: %.4f (token_id: %s, side: %s)", price, token_id[:20], side)
            return price
        
        logger.warning("WebSocket 无法获取价格 (token_id: %s)", token_id[:20])
        return None

    def get_best_price(self, market_id: str, side: str = "midpoint") -> Optional[float]:
        """
        通过 market_id 使用 WebSocket 获取价格
        
        方法：
        1. 通过 market_id 获取所有相关的 token_id（使用 get_clob_token_ids）
        2. 对每个 token_id 使用 WebSocket 获取价格
        3. 根据 side 参数返回相应价格
        
        Args:
            market_id: 市场 ID
            side: 价格类型，可选值: "midpoint" (默认), "ask"/"buy", "bid"/"sell"
            
        Returns:
            价格（0-1之间）或 None
        """
        if not self.ws_price_service:
            logger.warning("WebSocket 价格服务不可用，无法获取价格 (market_id: %s)", market_id)
            return None
        
        # 通过 market_id 获取所有 token_id
        clob_token_ids = self.get_clob_token_ids(market_id)
        if not isinstance(clob_token_ids, dict) or len(clob_token_ids) == 0:
            logger.debug("无法获取 market_id %s 的 token_ids", market_id)
            return None
        
        # 对于每个 token_id，尝试获取价格（通常一个市场有2个 token，Yes 和 No）
        # 我们返回第一个有价格的 token 的价格
        token_ids = list(clob_token_ids.values())
        
        # 批量订阅所有 token
        self.ws_price_service.subscribe_tokens(token_ids)
        
        # 尝试获取价格（先不等待）
        for token_id in token_ids:
            price = self.ws_price_service.get_price(token_id, side, wait_time=0.0)
            if price is not None and price > 0:
                logger.info("【Midpoint 获取成功】通过 market_id %s (token_id: %s) 从 WebSocket 缓存获取价格: %.4f", 
                           market_id, token_id[:20], price)
                return price
        
        # 如果缓存中没有，等待2秒
        logger.info("【Midpoint 获取】WebSocket 缓存中没有价格，等待 2 秒接收价格更新 (market_id: %s)", market_id)
        for token_id in token_ids:
            price = self.ws_price_service.get_price(token_id, side, wait_time=2.0)
            if price is not None and price > 0:
                logger.info("【Midpoint 获取成功】通过 market_id %s (token_id: %s) 从 WebSocket 获取价格: %.4f", 
                          market_id, token_id[:20], price)
                return price
        
        logger.warning("WebSocket 无法获取价格 (market_id: %s)", market_id)
        return None

    def get_midpoint(self, market_id: str) -> Optional[float]:
        """
        获取市场的中间价格（midpoint）- 保持向后兼容，使用 WebSocket
        
        Args:
            market_id: 市场 ID
            
        Returns:
            中间价格（0-1之间）或 None
        """
        return self.get_best_price(market_id, side="midpoint")



class PolygonBalanceProvider:
    """
    Reads ERC20 balances directly from Polygon. Falls back to cached values on failure.
    """

    def __init__(
        self,
        rpc_url: str,
        token_address: str = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",
        decimals: int = 6,
        cache_ttl: int = 120,
        token_symbol: str = "pUSD",
    ) -> None:
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))
        middleware = _poa_middleware()
        if middleware:
            try:
                self.web3.middleware_onion.inject(middleware, layer=0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping POA middleware injection: %s", exc)
        else:
            logger.debug("POA middleware not available in current web3 version; continuing without injection.")
        self.token_address = Web3.to_checksum_address(token_address)
        self.decimals = decimals
        self.cache_ttl = cache_ttl
        self.token_symbol = token_symbol
        self._cache: dict[str, tuple[float, float]] = {}
        # Extended ABI to include allowance and approve functions
        self.contract = self.web3.eth.contract(
            address=self.token_address,
            abi=[
                {
                    "constant": True,
                    "inputs": [{"name": "_owner", "type": "address"}],
                    "name": "balanceOf",
                    "outputs": [{"name": "balance", "type": "uint256"}],
                    "type": "function",
                },
                {
                    "constant": True,
                    "inputs": [
                        {"name": "_owner", "type": "address"},
                        {"name": "_spender", "type": "address"}
                    ],
                    "name": "allowance",
                    "outputs": [{"name": "remaining", "type": "uint256"}],
                    "type": "function",
                },
                {
                    "constant": False,
                    "inputs": [
                        {"name": "_spender", "type": "address"},
                        {"name": "_value", "type": "uint256"}
                    ],
                    "name": "approve",
                    "outputs": [{"name": "", "type": "bool"}],
                    "type": "function",
                },
            ],
        )

    def get_balance(self, address: str) -> Optional[float]:
        now = time.time()
        cached = self._cache.get(address.lower())
        if cached and cached[1] > now:
            logger.debug("Using cached balance for %s: %.2f %s", address, cached[0], self.token_symbol)
            return cached[0]
        try:
            checksum_address = Web3.to_checksum_address(address)
            logger.debug("Fetching balance from Polygon for address: %s", checksum_address)
            raw = self.contract.functions.balanceOf(checksum_address).call()
            value = raw / float(10**self.decimals)
            logger.info("Balance fetched for %s: raw=%d, value=%.2f %s", address, raw, value, self.token_symbol)
            self._cache[address.lower()] = (value, now + self.cache_ttl)
            return value
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read balance for %s: %s", address, exc, exc_info=True)
            if cached:
                logger.debug("Using cached balance (from previous fetch) for %s: %.2f %s", address, cached[0], self.token_symbol)
                return cached[0]
            logger.warning("No balance available for %s (no cache), returning None", address)
            return None

    def get_allowance(self, owner_address: str, spender_address: str) -> Optional[float]:
        """Get the allowance of the configured collateral token for a spender contract."""
        try:
            owner = Web3.to_checksum_address(owner_address)
            spender = Web3.to_checksum_address(spender_address)
            raw = self.contract.functions.allowance(owner, spender).call()
            value = raw / float(10**self.decimals)
            logger.info(
                "Allowance for %s -> %s: raw=%d, value=%.2f %s",
                owner_address, spender_address, raw, value, self.token_symbol
            )
            return value
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read allowance for %s -> %s: %s", owner_address, spender_address, exc)
            return None

    def approve(
        self,
        owner_private_key: str,
        spender_address: str,
        amount: float | None = None,
    ) -> Optional[str]:
        """
        Approve collateral spending for a spender contract.
        If amount is None, approves maximum (2^256 - 1).
        Returns transaction hash if successful, None otherwise.
        """
        try:
            account = self.web3.eth.account.from_key(owner_private_key)
            owner_address = account.address
            spender = Web3.to_checksum_address(spender_address)
            
            if amount is None:
                # Approve maximum (2^256 - 1)
                approve_amount = 2**256 - 1
                logger.info("Approving maximum %s allowance for %s -> %s", self.token_symbol, owner_address, spender_address)
            else:
                approve_amount = int(amount * 10**self.decimals)
                logger.info(
                    "Approving %.2f %s (raw: %d) for %s -> %s",
                    amount, self.token_symbol, approve_amount, owner_address, spender_address
                )
            
            # Build transaction
            nonce = self.web3.eth.get_transaction_count(owner_address)
            gas_price = self.web3.eth.gas_price
            
            transaction = self.contract.functions.approve(spender, approve_amount).build_transaction({
                "from": owner_address,
                "nonce": nonce,
                "gasPrice": gas_price,
            })
            
            # Estimate gas
            try:
                gas_estimate = self.web3.eth.estimate_gas(transaction)
                transaction["gas"] = gas_estimate
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to estimate gas, using default: %s", exc)
                transaction["gas"] = 100000  # Default gas limit for approve
            
            # Sign and send transaction
            signed_txn = self.web3.eth.account.sign_transaction(transaction, owner_private_key)
            raw_transaction = getattr(signed_txn, "raw_transaction", None) or getattr(signed_txn, "rawTransaction")
            tx_hash = self.web3.eth.send_raw_transaction(raw_transaction)
            
            logger.info("Approval transaction sent: %s", tx_hash.hex())
            
            # Wait for transaction receipt
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status == 1:
                logger.info("Approval successful! Transaction: %s", tx_hash.hex())
                return tx_hash.hex()
            else:
                logger.error("Approval transaction failed: %s", tx_hash.hex())
                return None
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to approve %s: %s", self.token_symbol, exc, exc_info=True)
            return None

