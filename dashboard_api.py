#!/usr/bin/env python3
"""
Polymarket 监控面板 API 服务
提供持仓查询和按比例平仓功能的 REST API
"""

from __future__ import annotations
from decimal import Decimal, ROUND_DOWN
import math

import json
import logging
import time
import hmac
import hashlib
import base64
import struct
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, Optional

from flask import Flask, jsonify, send_from_directory, request, abort, session, redirect, url_for
from flask_cors import CORS

import os
from dotenv import load_dotenv

from polymarket_autotrader.clients import PolymarketDataClient, PolygonBalanceProvider, RetryConfig
from polymarket_autotrader.config import BotConfig, EnvConfig, WalletConfig, load_bot_config, load_env, load_wallets
from polymarket_autotrader.execution import CLOBOrderExecutor
SELL = "SELL"
BUY = "BUY"

load_dotenv()

app = Flask(__name__, static_folder="dashboard", static_url_path="")
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())  # Session密钥
CORS(app)  # 允许跨域请求

logger = logging.getLogger(__name__)

# 全局变量
data_client: Optional[PolymarketDataClient] = None
balance_provider: Optional[PolygonBalanceProvider] = None
config: Optional[BotConfig] = None
env_config: Optional[EnvConfig] = None
clob_executor: Optional[CLOBOrderExecutor] = None
auth_tokens: Dict[str, float] = {}
positions_cache: Optional[Dict[str, Any]] = None
positions_cache_ts: float = 0.0
positions_cache_lock = threading.Lock()
positions_refresh_inflight = False
positions_refresh_lock = threading.Lock()
positions_last_refresh_error: Optional[str] = None
positions_refresh_generation = 0
positions_refresh_generation_lock = threading.Lock()

# token_id -> {"ts": float, "info": dict}
market_info_cache: Dict[str, Dict[str, Any]] = {}
market_info_cache_lock = threading.Lock()
market_info_prefetch_inflight: set[str] = set()
market_info_prefetch_lock = threading.Lock()

# 多钱包管理
wallets: Dict[str, WalletConfig] = {}  # {wallet_id: WalletConfig}
current_wallet_id: Optional[str] = None  # 当前选中的钱包ID
wallet_executors: Dict[str, CLOBOrderExecutor] = {}  # {wallet_id: CLOBOrderExecutor}


def _parse_positive_float(value: Any, field_name: str) -> tuple[Optional[float], Optional[tuple[Any, int]]]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, (jsonify({"error": f"Invalid {field_name}"}), 400)
    if not math.isfinite(parsed) or parsed <= 0:
        return None, (jsonify({"error": f"{field_name} must be a finite number > 0"}), 400)
    return parsed, None


def get_current_wallet() -> Optional[WalletConfig]:
    """获取当前选中的钱包配置"""
    if current_wallet_id and current_wallet_id in wallets:
        return wallets[current_wallet_id]
    # 如果没有选中钱包，返回第一个钱包
    if wallets:
        return list(wallets.values())[0]
    return None


def get_dashboard_wallet_address() -> str:
    """获取Dashboard监控的钱包地址"""
    # 优先使用当前选中的钱包
    current_wallet = get_current_wallet()
    if current_wallet:
        return current_wallet.address
    
    # 向后兼容：尝试从环境变量读取
    load_dotenv()
    dashboard_address = os.getenv("DASHBOARD_WALLET_ADDRESS", "").strip()
    if dashboard_address:
        return dashboard_address
    if env_config and env_config.funder_address:
        return env_config.funder_address
    funder_address = os.getenv("POLY_FUNDER_ADDRESS", "").strip()
    if funder_address:
        return funder_address
    logger.warning("未配置钱包地址")
    return ""


def _get_positions_cache_ttl() -> float:
    raw = (os.getenv("DASHBOARD_POSITIONS_CACHE_SECONDS") or "5").strip()
    try:
        ttl = float(raw)
    except Exception:  # noqa: BLE001
        ttl = 5.0
    return max(0.0, ttl)


def _get_first_response_timeout_seconds() -> float:
    raw = (os.getenv("DASHBOARD_POSITIONS_FIRST_RESPONSE_TIMEOUT") or "4.0").strip()
    try:
        v = float(raw)
    except Exception:  # noqa: BLE001
        v = 4.0
    return max(0.5, min(10.0, v))


def _get_market_info_cache_ttl() -> float:
    raw = (os.getenv("DASHBOARD_MARKET_INFO_CACHE_SECONDS") or "86400").strip()
    try:
        ttl = float(raw)
    except Exception:  # noqa: BLE001
        ttl = 86400.0
    return max(60.0, ttl)


def _get_market_info_max_lookups_per_refresh() -> int:
    raw = (os.getenv("DASHBOARD_MARKET_INFO_MAX_LOOKUPS_PER_REFRESH") or "10").strip()
    try:
        n = int(raw)
    except Exception:  # noqa: BLE001
        n = 10
    return max(0, min(200, n))


def _get_market_info_cached(token_id: str) -> Dict[str, Any]:
    ttl = _get_market_info_cache_ttl()
    now = time.time()
    with market_info_cache_lock:
        entry = market_info_cache.get(str(token_id))
        if entry and (now - float(entry.get("ts") or 0)) < ttl:
            info = entry.get("info") or {}
            return info if isinstance(info, dict) else {}

    if not data_client:
        return {}

    info: Dict[str, Any] = {}
    try:
        info = data_client.get_market_info_for_token(str(token_id)) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Market info lookup failed for token %s: %s", str(token_id)[:16], exc)
        info = {}

    with market_info_cache_lock:
        market_info_cache[str(token_id)] = {"ts": now, "info": info}
    return info


def _prefetch_market_info_async(token_ids: list[str]) -> None:
    if not token_ids or not data_client:
        return

    max_lookups = _get_market_info_max_lookups_per_refresh()
    if max_lookups <= 0:
        return

    unique: list[str] = []
    now = time.time()
    ttl = _get_market_info_cache_ttl()
    with market_info_cache_lock:
        for tid in token_ids:
            tid = str(tid)
            entry = market_info_cache.get(tid)
            if entry and (now - float(entry.get("ts") or 0)) < ttl:
                continue
            unique.append(tid)

    if not unique:
        return

    unique = unique[:max_lookups]
    with market_info_prefetch_lock:
        pending = [tid for tid in unique if tid not in market_info_prefetch_inflight]
        market_info_prefetch_inflight.update(pending)
    if not pending:
        return

    def _worker(batch: list[str]) -> None:
        try:
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(batch)))) as pool:
                futures = {pool.submit(_get_market_info_cached, tid): tid for tid in batch}
                for fut in as_completed(futures):
                    _ = fut.result()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Market info prefetch worker error: %s", exc)
        finally:
            with market_info_prefetch_lock:
                for tid in batch:
                    market_info_prefetch_inflight.discard(tid)

    threading.Thread(target=_worker, args=(pending,), daemon=True).start()


def _get_cash_balance_fast(wallet_address: str, timeout_seconds: float = 2.0) -> Optional[float]:
    """
    Try to fetch cash balance without blocking the request too long.
    Returns None if it can't finish within timeout_seconds.
    """
    if not wallet_address:
        return None

    def _chain_call() -> Optional[float]:
        if balance_provider:
            try:
                return balance_provider.get_balance(wallet_address)
            except Exception:  # noqa: BLE001
                return None
        return None

    result_box: dict[str, Any] = {"v": None}

    def _run():
        result_box["v"] = _chain_call()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=max(0.1, timeout_seconds))
    if not t.is_alive():
        v = result_box.get("v")
        if v is not None:
            return float(v)

    return None


def _build_positions_payload(wallet_address: str) -> Dict[str, Any]:
    if not data_client:
        raise RuntimeError("Data client not initialized")

    positions_from_api = data_client.fetch_positions_for_user(wallet_address)

    positions_by_token: Dict[str, Dict[str, Any]] = {}
    missing_market_info_tokens: list[str] = []
    total_value_all = 0.0
    total_pnl_all = 0.0

    for pos in positions_from_api:
        token_id = pos.get("asset") or pos.get("token_id") or pos.get("tokenId") or pos.get("token")
        if not token_id:
            continue
        token_id = str(token_id)

        position_size = float(pos.get("size") or pos.get("amount") or pos.get("quantity") or 0)
        if position_size <= 0:
            continue

        avg_price = float(pos.get("avgPrice") or pos.get("avg_price") or pos.get("avgCost") or pos.get("avg_cost") or 0)
        current_value = float(pos.get("currentValue") or pos.get("current_value") or 0)

        market_title = pos.get("title") or pos.get("question") or pos.get("marketTitle") or ""
        outcome_name = pos.get("outcome") or pos.get("outcomeName") or ""
        market_id = pos.get("market") or pos.get("marketId") or pos.get("market_id") or ""

        # Use cached market info when fields are missing; do not block on network lookups here.
        if not market_title or not outcome_name or not market_id:
            cached = {}
            with market_info_cache_lock:
                entry = market_info_cache.get(token_id)
                if entry and isinstance(entry.get("info"), dict):
                    cached = entry["info"]
            if cached:
                market_title = market_title or cached.get("market_title") or cached.get("title") or ""
                outcome_name = outcome_name or cached.get("outcome_name") or cached.get("outcome") or ""
                market_id = market_id or cached.get("market_id") or ""
            else:
                missing_market_info_tokens.append(token_id)

        cur_price = pos.get("current_price")
        if cur_price is None:
            cur_price = pos.get("curPrice") or pos.get("currentPrice") or pos.get("cur_price")
        if cur_price is not None:
            try:
                cur_price = float(cur_price)
            except (ValueError, TypeError):
                cur_price = None

        initial_value = float(pos.get("initialValue") or pos.get("initial_value") or avg_price * position_size)
        cash_pnl = float(pos.get("cashPnl") or pos.get("cash_pnl") or pos.get("pnl") or 0)
        percent_pnl = float(pos.get("percentPnl") or pos.get("percent_pnl") or 0)

        if cash_pnl == 0 and cur_price is not None:
            current_value = cur_price * position_size
            cash_pnl = current_value - initial_value
        if percent_pnl == 0 and cur_price is not None and avg_price > 0:
            percent_pnl = ((cur_price - avg_price) / avg_price * 100)

        # Totals should include all positions (even those hidden in the UI).
        try:
            if current_value is not None:
                total_value_all += float(current_value)
        except (TypeError, ValueError):
            pass
        try:
            if cash_pnl is not None:
                total_pnl_all += float(cash_pnl)
        except (TypeError, ValueError):
            pass

        positions_by_token[token_id] = {
            "size": position_size,
            "avg_cost": avg_price,
            "current_price": cur_price,
            "market_title": market_title,
            "outcome_name": outcome_name,
            "market_id": market_id,
            "initial_value": initial_value,
            "current_value": current_value,
            "cash_pnl": cash_pnl,
            "percent_pnl": percent_pnl,
        }

    _prefetch_market_info_async(missing_market_info_tokens)

    result: list[dict[str, Any]] = []
    filtered_out = 0
    for token_id, pos in positions_by_token.items():
        size = float(pos.get("size", 0))
        avg_cost = float(pos.get("avg_cost", 0))
        current_price = pos.get("current_price")

        market_title = pos.get("market_title") or f"Token {token_id[:12]}..."
        outcome_name = pos.get("outcome_name") or ""
        market_id = pos.get("market_id") or ""
        slug = ""

        initial_value = pos.get("initial_value")
        current_value = pos.get("current_value")
        cash_pnl = pos.get("cash_pnl")
        percent_pnl = pos.get("percent_pnl")

        if cash_pnl is None and initial_value is not None and current_value is not None:
            cash_pnl = current_value - initial_value
        if percent_pnl is None and current_price is not None and avg_cost > 0:
            percent_pnl = ((current_price - avg_cost) / avg_cost * 100)

        result.append({
            "token_id": token_id,
            "market_id": market_id,
            "slug": slug,
            "market_title": market_title,
            "outcome": outcome_name,
            "avg_cost": round(avg_cost, 4),
            "size": round(size, 2),
            "current_price": round(current_price, 4) if current_price is not None else None,
            "initial_value": round(initial_value, 2) if initial_value is not None else None,
            "current_value": round(current_value, 2) if current_value is not None else None,
            "pnl": round(cash_pnl, 2) if cash_pnl is not None else None,
            "pnl_percent": round(percent_pnl, 2) if percent_pnl is not None else None,
        })

    result.sort(key=lambda x: x["size"], reverse=True)

    cash_balance = _get_cash_balance_fast(wallet_address, timeout_seconds=1.0)

    positions_value = round(float(total_value_all), 2)
    if cash_balance is not None:
        cash_balance = round(float(cash_balance), 2)

    current_wallet = get_current_wallet()
    wallet_info = {
        "address": wallet_address,
        "name": current_wallet.name if current_wallet else "Unknown",
        "id": current_wallet_id if current_wallet_id else None,
    }

    return {
        "positions": result,
        "total_count": len(result),
        "total_pnl": round(float(total_pnl_all), 2),
        "cash_balance": cash_balance,
        "positions_value": positions_value,
        "wallet": wallet_info,
        "_meta": {
            "generated_at": time.time(),
            "market_info_prefetch_scheduled": len(missing_market_info_tokens),
            "filtered_out_positions": filtered_out,
        },
    }


def _set_positions_cache(payload: Dict[str, Any]) -> None:
    global positions_cache, positions_cache_ts
    with positions_cache_lock:
        positions_cache = payload
        positions_cache_ts = time.time()


def _refresh_positions_cache_worker(generation: int, force: bool = False) -> None:
    global positions_refresh_inflight, positions_last_refresh_error
    try:
        wallet_address = get_dashboard_wallet_address()
        if not wallet_address:
            raise RuntimeError("Wallet address not configured")
        payload = _build_positions_payload(wallet_address)
        with positions_refresh_generation_lock:
            if generation != positions_refresh_generation:
                return
        _set_positions_cache(payload)
        positions_last_refresh_error = None
    except Exception as exc:  # noqa: BLE001
        positions_last_refresh_error = str(exc)
        logger.warning("Positions refresh failed: %s", exc)
    finally:
        with positions_refresh_lock:
            positions_refresh_inflight = False


def _trigger_positions_refresh(force: bool = False) -> None:
    global positions_refresh_inflight
    with positions_refresh_lock:
        if positions_refresh_inflight:
            return
        positions_refresh_inflight = True
    with positions_refresh_generation_lock:
        generation = positions_refresh_generation
    threading.Thread(target=_refresh_positions_cache_worker, args=(generation, force), daemon=True).start()


def init_services() -> None:
    """初始化服务"""
    global data_client, balance_provider, config, env_config, clob_executor
    global wallets, current_wallet_id, wallet_executors
    
    try:
        config = load_bot_config()
        env_config = load_env()

        # Dashboard prioritizes low latency: use smaller retry/timeouts than the trading engine.
        dash_retry = RetryConfig(
            max_attempts=int((os.getenv("DASHBOARD_API_MAX_ATTEMPTS") or "1").strip()),
            timeout=(
                float((os.getenv("DASHBOARD_API_CONNECT_TIMEOUT") or "2").strip()),
                float((os.getenv("DASHBOARD_API_READ_TIMEOUT") or "4").strip()),
            ),
            linear_backoff=float((os.getenv("DASHBOARD_API_LINEAR_BACKOFF") or "0.5").strip()),
            max_rate_limit_wait=float((os.getenv("DASHBOARD_API_MAX_RATE_LIMIT_WAIT") or "5").strip()),
        )

        data_client = PolymarketDataClient(
            gamma_base_url=config.gamma_base_url,
            data_api_base_url=config.data_api_base_url,
            clob_host=config.clob_host,
            retry_config=dash_retry,
            enable_websocket=False,
        )
        
        # 加载多个钱包配置
        wallet_list = load_wallets()
        wallets = {w.id: w for w in wallet_list}
        wallet_executors = {}
        
        # 为每个钱包创建独立的 CLOBOrderExecutor
        for wallet_id, wallet in wallets.items():
            # 创建临时 EnvConfig 用于该钱包
            wallet_env = EnvConfig(
                private_key=wallet.private_key,
                signature_type=wallet.signature_type,
                funder_address=wallet.address,
                dry_run=env_config.dry_run,
                clob_api_key=wallet.clob_api_key,
                clob_api_secret=wallet.clob_api_secret,
                clob_api_passphrase=wallet.clob_api_passphrase,
                polygon_rpc_url=env_config.polygon_rpc_url,
            )
            wallet_executors[wallet_id] = CLOBOrderExecutor(
                clob_host=config.clob_host,
                env=wallet_env,
                price_precision=config.price_precision,
                size_precision=config.size_precision,
                dry_run=env_config.dry_run,
            )
            logger.info("Initialized executor for wallet: %s (%s)", wallet.name, wallet.address[:20] + "...")
        
        # 设置默认钱包（第一个）
        if wallets:
            current_wallet_id = list(wallets.keys())[0]
            clob_executor = wallet_executors[current_wallet_id]
            logger.info("Default wallet selected: %s", wallets[current_wallet_id].name)
        else:
            # 向后兼容：如果没有多钱包配置，使用旧的单钱包方式
            clob_executor = CLOBOrderExecutor(
                clob_host=config.clob_host,
                env=env_config,
                price_precision=config.price_precision,
                size_precision=config.size_precision,
                dry_run=env_config.dry_run,
            )
            logger.warning("No wallets configured, using legacy single wallet mode")
        
        # 初始化链上余额查询器（使用 Polygon RPC）
        polygon_rpc = os.getenv("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")
        balance_provider = PolygonBalanceProvider(
            rpc_url=polygon_rpc,
            token_address="0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",  # pUSD
            decimals=6,
            cache_ttl=120,
            token_symbol="pUSD",
        )
        logger.info("Initialized PolygonBalanceProvider with RPC: %s", polygon_rpc)
        
        # 验证钱包地址配置
        wallet_address = get_dashboard_wallet_address()

        if wallet_address:
            logger.info("Dashboard API services initialized, monitoring wallet: %s", wallet_address[:20] + "...")
        else:
            logger.warning("Dashboard wallet address not configured, some features may not work")

        # Warm positions cache in background so the first page load is fast.
        _trigger_positions_refresh(force=True)
    except Exception as exc:
        logger.error("Failed to initialize services: %s", exc, exc_info=True)
        raise


@app.before_request
def require_auth():
    """保护所有路由，除了登录页面和登录API"""
    # 允许访问的路径
    allowed_paths = ['/login', '/api/auth/login', '/api/auth/check', '/api/auth/logout']
    static_paths = ['/static/', '/favicon.ico']
    
    # 检查是否是允许的路径
    if any(request.path.startswith(path) for path in static_paths):
        return None
    
    if request.path in allowed_paths:
        return None
    
    # 检查是否已登录
    if not session.get('authenticated'):
        # 如果是API请求，返回401
        if request.path.startswith('/api/'):
            return jsonify({"error": "Authentication required", "authenticated": False}), 401
        # 否则重定向到登录页
        return redirect(url_for('login_page'))


@app.route("/login")
def login_page():
    """登录页面"""
    return send_from_directory("dashboard", "login.html")


@app.route("/")
def index():
    """前端页面（需要登录）"""
    if not session.get('authenticated'):
        return redirect(url_for('login_page'))
    return send_from_directory("dashboard", "index.html")


@app.route("/api/status")
def api_status():
    """获取系统状态"""
    return jsonify({
        "status": "ok",
        "timestamp": time.time(),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/positions")
def api_positions():
    """获取当前钱包持仓。"""
    global positions_cache, positions_cache_ts
    if not data_client:
        return jsonify({"error": "Data client not initialized"}), 500
    ttl = _get_positions_cache_ttl()
    force = bool(request.args.get("t")) or (str(request.args.get("force") or "").strip().lower() in ("1", "true", "yes"))

    with positions_cache_lock:
        cached = positions_cache
        cached_ts = positions_cache_ts

    now = time.time()
    age = (now - cached_ts) if cached and cached_ts else None
    if ttl > 0 and cached and age is not None and age < ttl and not force:
        return jsonify(cached)

    # If cache exists but is stale, return it immediately and refresh in background.
    if cached:
        _trigger_positions_refresh(force=force)
        enriched = dict(cached)
        meta = dict(enriched.get("_meta") or {})
        meta.update({
            "stale": True,
            "cache_age_seconds": age,
            "refreshing": positions_refresh_inflight,
            "last_refresh_error": positions_last_refresh_error,
        })
        enriched["_meta"] = meta
        return jsonify(enriched)

    # No cache yet: trigger refresh and wait briefly for first payload; otherwise return fast.
    wallet_address = get_dashboard_wallet_address()
    if not wallet_address:
        return jsonify({"error": "Wallet address not configured"}), 500

    _trigger_positions_refresh(force=True)
    timeout = _get_first_response_timeout_seconds()
    start = time.time()
    while time.time() - start < timeout:
        with positions_cache_lock:
            cached = positions_cache
        if cached:
            return jsonify(cached)
        time.sleep(0.05)

    error = positions_last_refresh_error or "持仓数据正在加载，请稍后重试"
    return jsonify({"error": error, "refreshing": positions_refresh_inflight}), 503



@app.route("/api/wallets")
def api_wallets():
    """获取所有钱包列表"""
    wallet_list = []
    for wallet_id, wallet in wallets.items():
        wallet_list.append({
            "id": wallet_id,
            "name": wallet.name,
            "address": wallet.address,
            "is_current": wallet_id == current_wallet_id,
        })
    return jsonify({
        "wallets": wallet_list,
        "current_wallet_id": current_wallet_id,
    })


@app.route("/api/wallets/switch", methods=["POST"])
def api_wallets_switch():
    """切换当前钱包"""
    global current_wallet_id, clob_executor
    
    data = request.json or {}
    wallet_id = data.get("wallet_id")
    
    if not wallet_id:
        return jsonify({"error": "wallet_id is required"}), 400
    
    if wallet_id not in wallets:
        return jsonify({"error": f"Wallet {wallet_id} not found"}), 404
    
    # 切换钱包
    current_wallet_id = wallet_id
    clob_executor = wallet_executors.get(wallet_id)
    
    # 清除持仓缓存，强制刷新
    global positions_cache, positions_cache_ts
    global positions_refresh_generation, positions_refresh_inflight
    with positions_refresh_generation_lock:
        positions_refresh_generation += 1
    # Allow an immediate refresh for the new wallet; old in-flight workers won't overwrite cache (generation guard).
    with positions_refresh_lock:
        positions_refresh_inflight = False
    with positions_cache_lock:
        positions_cache = None
        positions_cache_ts = 0.0
    _trigger_positions_refresh(force=True)
    
    logger.info("Switched to wallet: %s (%s)", wallets[wallet_id].name, wallets[wallet_id].address[:20] + "...")
    
    return jsonify({
        "status": "success",
        "current_wallet_id": wallet_id,
        "wallet_name": wallets[wallet_id].name,
        "wallet_address": wallets[wallet_id].address,
    })


@app.route("/api/wallets/status")
def api_wallets_status():
    wallet_status_list = []
    for wallet_id, executor in wallet_executors.items():
        wallet = wallets.get(wallet_id)
        has_creds = False
        try:
            has_creds = executor.has_api_creds
        except Exception:  # noqa: BLE001
            pass
        wallet_status_list.append({
            "id": wallet_id,
            "name": wallet.name if wallet else "Unknown",
            "is_current": wallet_id == current_wallet_id,
            "has_api_creds": has_creds,
        })
    return jsonify({
        "wallets": wallet_status_list,
        "current_wallet_id": current_wallet_id,
    })


# --- Auth & Secure Operations ---

def verify_totp(secret: str, code: str) -> bool:
    """Verify TOTP code against secret (base32)"""
    try:
        if not secret: return True # If no secret configured, skip 2FA
        
        # Clean and pad secret
        secret = secret.upper().replace(" ", "")
        padding = len(secret) % 8
        if padding != 0:
            secret += "=" * (8 - padding)
        
        key = base64.b32decode(secret, casefold=True)
        
        # Current time step (30s)
        t = int(time.time() / 30)
        
        # Check window of +/- 1 step
        for i in range(-1, 2):
            msg = struct.pack(">Q", t + i)
            digest = hmac.new(key, msg, hashlib.sha1).digest()
            offset = digest[-1] & 0x0f
            binary = struct.unpack(">L", digest[offset:offset+4])[0] & 0x7fffffff
            otp = binary % 1000000
            if str(otp).zfill(6) == str(code).strip():
                return True
    except Exception as e:
        logger.error(f"TOTP Check Error: {e}")
    return False

def check_auth(token: str) -> bool:
    """Check if auth token is valid"""
    if not token:
        return False
    expiry = auth_tokens.get(token)
    if not expiry:
        return False
    if time.time() > expiry:
        del auth_tokens[token]
        return False
    return True

@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """Login with Password + 2FA"""
    data = request.json or {}
    password = data.get("password", "")
    code = data.get("code", "")
    
    load_dotenv()
    env_pass = os.getenv("DASHBOARD_PASSWORD")
    env_secret = os.getenv("DASHBOARD_2FA_SECRET")
    
    if not env_pass:
        return jsonify({"error": "Dashboard password not configured on server"}), 500
        
    if password != env_pass:
        return jsonify({"error": "Invalid password"}), 401
        
    if env_secret:
        # Check if code provided
        if not code:
            # If 2FA configured but not provided, fail
            return jsonify({"error": "2FA code required"}), 401
            
        if not verify_totp(env_secret, code):
             return jsonify({"error": "Invalid 2FA code"}), 401
    elif code:
         # If 2FA NOT configured but code provided, ignore code (or warn)
         pass
         
    # Set session
    session['authenticated'] = True
    session['login_time'] = time.time()
    session.permanent = True
    app.permanent_session_lifetime = 3600 * 24  # 24 hours
    
    return jsonify({"status": "success", "message": "Login successful"})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """Logout"""
    session.clear()
    return jsonify({"status": "success", "message": "Logged out"})


@app.route("/api/auth/check", methods=["GET"])
def api_auth_check():
    """Check if user is authenticated"""
    if session.get('authenticated'):
        return jsonify({"authenticated": True})
    return jsonify({"authenticated": False}), 401

@app.route("/api/trade/sell", methods=["POST"])
def api_trade_sell():
    """Sell position (requires auth)"""
    # Auth is now handled by before_request hook
        
    data = request.json or {}
    token_id = data.get("token_id")
    percentage, parse_error = _parse_positive_float(data.get("percentage"), "percentage")
    if parse_error:
        return parse_error
    
    if not token_id or percentage is None or percentage > 1:
        return jsonify({"error": "Invalid parameters"}), 400
        
    if not clob_executor or not data_client:
        return jsonify({"error": "Execution services not initialized"}), 500
        
    try:
        # 1. Get current position size
        wallet_address = get_dashboard_wallet_address()
        positions = data_client.fetch_positions_for_user(wallet_address)
        position = next((p for p in positions if p.get("asset") == token_id or p.get("token_id") == token_id or p.get("token") == token_id), None)
        
        if not position:
             return jsonify({"error": "Position not found"}), 404
             
        current_size = float(position.get("size") or position.get("amount") or 0)
        if current_size <= 0:
            return jsonify({"error": "Position size is 0"}), 400
            
        # 2. Calculate sell size
        sell_size = float((Decimal(str(current_size)) * Decimal(str(percentage))).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
            
        # 3. Get Best Price
        # We try to get the best BID price from the orderbook
        orderbook = data_client.get_order_book(token_id)
        bids = orderbook.bids if orderbook else []
        
        if not bids:
             # Try fallback: check if we can get price from other sources or force a price?
             # For now, let's just log and return error
             logger.warning(f"No orderbook found for token {token_id}")
             # Return 404 but with clear message
             if not orderbook:
                 return jsonify({"error": f"Orderbook not found for token {token_id} (market might be closed or invalid)"}), 404
             else:
                 return jsonify({"error": "No liquidity (no bids) found for token"}), 400
        
        best_bid_price = 0.0
        # Scan bids to find the highest price, handling multiple formats
        def _px(item: Any) -> float:
            try:
                if hasattr(item, "price"):
                    return float(getattr(item, "price"))
                if isinstance(item, dict):
                    v = item.get("price") or item.get("px") or item.get("p")
                    return float(v) if v is not None else 0.0
                if isinstance(item, (list, tuple)) and item:
                    return float(item[0])
                return float(item)
            except Exception:
                return 0.0
        if isinstance(bids, list) and bids:
            for it in bids:
                p = _px(it)
                if p > best_bid_price:
                    best_bid_price = p
        
        if best_bid_price <= 0:
             return jsonify({"error": "Could not determine best bid price"}), 400
             
        # 4. Execute Order (使用当前选中的钱包)
        current_wallet = get_current_wallet()
        if not current_wallet:
            return jsonify({"error": "No wallet selected"}), 400
        
        executor = wallet_executors.get(current_wallet_id) if current_wallet_id in wallet_executors else clob_executor
        if not executor:
            return jsonify({"error": "Wallet executor not initialized"}), 500
        
        logger.info(f"Executing SELL {sell_size} of {token_id} at {best_bid_price} using wallet {current_wallet.name} ({current_wallet.address[:20]}...)")
        
        result = executor.place_order(price=best_bid_price, size=sell_size, side=SELL, market_id=token_id, token_id=token_id)
        order_id = result.get("orderId", "")
        if not order_id:
            return jsonify({"status": "failed", "error": result.get("error") or "订单提交失败或无法确认"}), 400
        return jsonify({
            "status": "success",
            "order_id": order_id,
            "message": f"Sell order submitted: {sell_size:.2f} at {best_bid_price}"
        })
        
    except Exception as e:
        logger.error(f"Sell failed: {e}")
        error_str = str(e)
        if "425" in error_str or "service not ready" in error_str.lower():
            error_str = "CLOB API 凭证未就绪 (425)。请检查钱包是否已在 Polymarket 注册并授权。如问题持续，请重启服务重试。"
        return jsonify({"error": error_str}), 500

import re


def _extract_slug_from_url(url: str) -> Optional[str]:
    """从 Polymarket URL 中提取 slug"""
    if not url:
        return None

    raw = url.strip()
    # 去除常见协议和 www 前缀
    clean = re.sub(r'^[\s]*(?:https?://)?(?:www\.)?', '', raw, flags=re.IGNORECASE)
    logger.debug("_extract_slug_from_url: raw=%r, clean=%r", raw, clean)

    # 匹配 polymarket.com 开头
    if not re.search(r'^polymarket\.com', clean, re.IGNORECASE):
        # 如果输入不包含域名，可能直接是 slug
        slug = raw.strip('/')
        logger.debug("No domain found, treating as direct slug=%r", slug)
        if slug:
            return slug
        return None

    # 去掉域名部分，只保留路径
    path = re.sub(r'^polymarket\.com', '', clean, flags=re.IGNORECASE)
    # 去掉开头的 /
    path = path.lstrip('/')
    logger.debug("Path after domain strip: %r", path)

    # 将路径按 / 分割
    segments = [s for s in path.split('/') if s and s not in ('zh', 'en', 'ja', 'ko')]
    logger.debug("Path segments: %s", segments)

    if not segments:
        logger.warning("No path segments found in URL: %r", raw)
        return None

    # 优先取最后一个段作为 slug
    last_segment = segments[-1].rstrip('/')
    last_segment = re.split(r'[?#]', last_segment, maxsplit=1)[0]
    # 倒数第二段如果是 event/market/markets/sports，说明最后一段是 slug
    path_keywords = ('event', 'market', 'markets', 'sports', 'outcomes')
    if len(segments) >= 2 and segments[-2].lower() in path_keywords:
        if len(last_segment) > 3 and last_segment.lower() not in path_keywords:
            logger.debug("Using last segment as slug (parent is %r): %r", segments[-2], last_segment)
            return last_segment

    # 兜底：如果只有一段，且不是关键字，直接作为 slug
    if len(segments) == 1:
        seg = segments[0].rstrip('/')
        if len(seg) > 3 and seg.lower() not in path_keywords:
            logger.debug("Single segment slug: %r", seg)
            return seg

    # 兜底：取最后一段（只要不是关键字）
    if len(last_segment) > 3 and last_segment.lower() not in path_keywords:
        logger.debug("Fallback last segment: %r", last_segment)
        return last_segment

    logger.warning("Could not extract valid slug from URL: %r, segments=%s", raw, segments)
    return None


def _parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


def _market_is_yes_no(market: dict[str, Any]) -> bool:
    outcomes = [str(x).strip().lower() for x in _parse_json_list(market.get("outcomes"))]
    return len(outcomes) == 2 and set(outcomes) == {"yes", "no"}


def _is_draw_market(market: dict[str, Any]) -> bool:
    text = " ".join(
        str(market.get(key) or "")
        for key in ("question", "title", "slug", "groupItemTitle")
    ).lower()
    return any(word in text for word in (" draw", "-draw", " tie", "-tie", "end in a draw"))


def _moneyline_team_name(market: dict[str, Any]) -> str:
    question = str(market.get("question") or market.get("title") or "").strip()
    match = re.match(r"^Will\s+(.+?)\s+win(?:\s+on\b.*)?\??$", question, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return str(market.get("groupItemTitle") or question or market.get("slug") or "Team").strip()


def _select_event_moneyline_markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    markets = event.get("markets") or []
    if not isinstance(markets, list):
        return []

    candidates: list[dict[str, Any]] = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        if not _market_is_yes_no(market):
            continue
        if _is_draw_market(market):
            continue
        question = str(market.get("question") or market.get("title") or "").lower()
        if " win" not in question:
            continue
        candidates.append(market)

    event_title = str(event.get("title") or "")
    if event_title and candidates:
        candidates.sort(
            key=lambda market: (
                event_title.find(_moneyline_team_name(market))
                if _moneyline_team_name(market) in event_title
                else 10_000
            )
        )

    return candidates[:2] if len(candidates) >= 2 else []


def _price_snapshot_for_token(token_id: str) -> dict[str, Any]:
    orderbook = data_client.get_order_book(token_id) if data_client else None
    best_bid = None
    best_ask = None
    midpoint = None

    if orderbook:
        def _px(item: Any) -> Optional[float]:
            try:
                if isinstance(item, dict):
                    v = item.get("price") or item.get("px") or item.get("p")
                    return float(v) if v is not None else None
                if isinstance(item, (list, tuple)) and item:
                    return float(item[0])
                return float(item)
            except Exception:  # noqa: BLE001
                return None

        bids = orderbook.bids or []
        asks = orderbook.asks or []

        if isinstance(bids, list) and bids:
            best_bid = max((_px(b) for b in bids if _px(b) is not None), default=None)
        if isinstance(asks, list) and asks:
            best_ask = min((_px(a) for a in asks if _px(a) is not None), default=None)

        if best_bid is not None and best_ask is not None:
            midpoint = round((best_bid + best_ask) / 2.0, 4)
        elif best_ask is not None:
            midpoint = round(best_ask, 4)
        elif best_bid is not None:
            midpoint = round(best_bid, 4)

    return {
        "best_bid": round(best_bid, 4) if best_bid is not None else None,
        "best_ask": round(best_ask, 4) if best_ask is not None else None,
        "midpoint": midpoint,
        "spread": round(best_ask - best_bid, 4) if best_bid is not None and best_ask is not None else None,
    }


@app.route("/api/market/fetch", methods=["POST"])
def api_market_fetch():
    """Parse a Polymarket URL and return tradable outcomes with price snapshots."""
    data = request.json or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400

    if not data_client:
        return jsonify({"error": "Data client not initialized"}), 500

    try:
        slug = _extract_slug_from_url(url)
        if not slug:
            return jsonify({"error": "Could not extract slug from URL"}), 400

        event_info = data_client.get_event_for_slug(slug)
        moneyline_markets = _select_event_moneyline_markets(event_info)

        outcomes = []
        market_info: dict[str, Any] = {}
        market_id = ""
        market_ids: list[str] = []

        if moneyline_markets:
            market_info = event_info
            market_ids = [
                str(m.get("id") or m.get("marketId") or m.get("market_id") or "")
                for m in moneyline_markets
            ]
            market_ids = [mid for mid in market_ids if mid]
            market_id = market_ids[0] if market_ids else ""

            for market in moneyline_markets:
                mid = str(market.get("id") or market.get("marketId") or market.get("market_id") or "")
                if not mid:
                    continue
                team_name = _moneyline_team_name(market)
                clob_tokens = data_client.get_clob_token_ids(mid)
                if not isinstance(clob_tokens, dict) or len(clob_tokens) == 0:
                    continue
                for outcome_name, token_id in clob_tokens.items():
                    raw_outcome = str(outcome_name).strip()
                    display_name = (
                        f"{team_name} 赢"
                        if raw_outcome.lower() == "yes"
                        else f"{team_name} 不赢"
                    )
                    outcomes.append({
                        "name": display_name,
                        "raw_outcome": raw_outcome,
                        "market_id": mid,
                        "market_title": market.get("question") or market.get("title") or "",
                        "token_id": str(token_id),
                        **_price_snapshot_for_token(str(token_id)),
                    })
        else:
            market_id = data_client.get_market_id_for_slug(slug) or ""
            if not market_id:
                return jsonify({"error": f"Market not found for slug: {slug}"}), 404

            market_info = data_client.get_market_for_trade({"market": market_id, "slug": slug})
            clob_tokens = data_client.get_clob_token_ids(market_id)
            if not isinstance(clob_tokens, dict) or len(clob_tokens) == 0:
                return jsonify({"error": "No trading tokens found for this market"}), 404

            for outcome_name, token_id in clob_tokens.items():
                outcomes.append({
                    "name": outcome_name,
                    "market_id": market_id,
                    "token_id": str(token_id),
                    **_price_snapshot_for_token(str(token_id)),
                })

        if not outcomes:
            return jsonify({"error": "No trading tokens found for this market"}), 404

        wallet_address = get_dashboard_wallet_address()
        my_positions = [] if wallet_address else None
        if wallet_address:
            try:
                all_positions = data_client.fetch_positions_for_user(wallet_address)
                for pos in (all_positions or []):
                    pos_token = str(pos.get("asset") or pos.get("token_id") or pos.get("tokenId") or "")
                    for oc in outcomes:
                        if pos_token == oc["token_id"]:
                            my_positions.append({
                                "outcome": oc["name"],
                                "token_id": pos_token,
                                "size": float(pos.get("size") or pos.get("amount") or 0),
                                "avg_cost": float(pos.get("avgPrice") or pos.get("avg_price") or 0),
                                "current_value": float(pos.get("currentValue") or pos.get("current_value") or 0),
                            })
                            break
            except Exception as exc:
                logger.debug("Failed to fetch user positions: %s", exc)
                my_positions = None

        return jsonify({
            "status": "ok",
            "slug": slug,
            "market_id": market_id,
            "market_ids": market_ids or ([market_id] if market_id else []),
            "market_title": (
                f"{market_info.get('title') or slug} Moneyline"
                if moneyline_markets
                else market_info.get("question") or market_info.get("title") or slug
            ),
            "market_type": "moneyline" if moneyline_markets else "single",
            "outcomes": outcomes,
            "my_positions": my_positions,
        })

    except Exception as exc:
        logger.error("Market fetch failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


def _spx_slug_patterns(date_str: str) -> list[str]:
    """Generate candidate slugs for SPX daily close market on a given date.

    Polymarket uses various slug patterns.  We return several candidates so that
    the lookup can try them in order.
    """
    # date_str is expected in YYYY-MM-DD
    parts = date_str.split("-")
    if len(parts) != 3:
        return []
    y, m, d = parts

    # Build the human-readable month-day-year slug: spx-up-or-down-on-may-6-2026
    # Use fixed English month names. datetime.strftime("%B") is locale-dependent
    # and can generate non-English slugs on servers with a non-English locale.
    try:
        from datetime import datetime as _dt
        parsed = _dt.strptime(date_str, "%Y-%m-%d")
        month_names = (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        )
        month_name = month_names[parsed.month - 1]
        day_no = parsed.day
        year_no = parsed.year
        human_slug = f"spx-up-or-down-on-{month_name}-{day_no}-{year_no}"
    except Exception:
        human_slug = None

    candidates = [
        f"daily-s-p-500-market-move-{y}-{m}-{d}",
        f"daily-sp-500-market-move-{y}-{m}-{d}",
        f"s-p-500-daily-close-{y}-{m}-{d}",
        f"sp-500-daily-close-{y}-{m}-{d}",
        f"s-p-500-market-move-{y}-{m}-{d}",
        f"sp-500-market-move-{y}-{m}-{d}",
        f"daily-s-and-p-500-{y}-{m}-{d}",
        f"s-and-p-500-daily-{y}-{m}-{d}",
        f"s-p-500-{y}-{m}-{d}",
    ]
    if human_slug:
        candidates.insert(0, human_slug)
    return candidates


def _is_spx_up_down_market(market: dict[str, Any]) -> bool:
    """Return True if this market looks like an SPX up/down daily close market."""
    text = " ".join(
        str(market.get(k) or "")
        for k in ("question", "title", "groupItemTitle", "slug", "description", "outcomes")
    ).lower()
    spx_keywords = ("s&p 500", "s&p500", "sp 500", "sp500", "s-p-500", "s&p", "spx")
    direction_keywords = ("close above", "close below", "close up", "close down",
                          "close higher", "close lower", "daily close",
                          "market move up", "market move down", "rise", "fall",
                          "go up", "go down", "end up", "end down",
                          "up or down", "positive close", "negative close")
    has_spx = any(kw in text for kw in spx_keywords)
    has_direction = any(kw in text for kw in direction_keywords)
    return has_spx and has_direction


def _spx_event_from_market_payload(market: dict[str, Any]) -> dict[str, Any]:
    """Wrap a Gamma market payload as an event-like object for the SPX endpoint."""
    events = market.get("events")
    event = events[0] if isinstance(events, list) and events and isinstance(events[0], dict) else {}
    return {
        **event,
        "title": event.get("title") or market.get("question") or market.get("title"),
        "slug": event.get("slug") or market.get("slug"),
        "markets": [market],
    }


def _find_spx_market_for_date(target_date: str) -> Optional[dict[str, Any]]:
    """Search for an SPX daily close prediction market on the given date.

    Returns the event dict (with nested markets) if found, else None.
    """
    if not data_client:
        return None

    session = data_client.session
    gamma_base = data_client.gamma_base_url

    # Strategy 1: Try known slug patterns
    slug_candidates = _spx_slug_patterns(target_date)
    for slug in slug_candidates:
        try:
            event = data_client.get_event_for_slug(slug)
            if isinstance(event, dict) and event.get("markets"):
                return event
        except Exception:
            pass

        try:
            url = f"{gamma_base}/events"
            resp = retry_request(session, "GET", url, data_client.retry_config, params={"slug": slug})
            if resp.status_code == 200:
                data = resp.json()
                events = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
                for ev in events:
                    if isinstance(ev, dict) and ev.get("markets"):
                        return ev
        except Exception:
            pass

        try:
            market = data_client.get_market_for_trade({"slug": slug})
            if isinstance(market, dict) and market and _is_spx_up_down_market(market):
                return _spx_event_from_market_payload(market)
        except Exception:
            continue

    # Strategy 2: Search Gamma by tag / title keyword
    try:
        url = f"{gamma_base}/events"
        try:
            from datetime import datetime as _dt
            parsed = _dt.strptime(target_date, "%Y-%m-%d")
            month_names = (
                "january", "february", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december",
            )
            target_title_markers = {
                target_date,
                target_date.replace("-", ""),
                f"{month_names[parsed.month - 1]} {parsed.day}",
                f"{month_names[parsed.month - 1]} {parsed.day}, {parsed.year}",
            }
        except Exception:
            target_title_markers = {target_date, target_date.replace("-", "")}
        # Try tag-based search first
        for tag in ("spx", "s&p-500", "sp500", "s-p-500"):
            resp = retry_request(session, "GET", url, data_client.retry_config,
                                 params={"tag": tag, "closed": "false"})
            if resp.status_code != 200:
                continue
            data = resp.json()
            events = data if isinstance(data, list) else []
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                ev_title = str(ev.get("title") or "").lower()
                # Check if this event is for the target date
                compact_title = ev_title.replace("-", "")
                if any(marker in ev_title or marker in compact_title for marker in target_title_markers):
                    markets = ev.get("markets") or []
                    spx_markets = [m for m in markets if isinstance(m, dict) and _is_spx_up_down_market(m)]
                    if spx_markets:
                        return ev
    except Exception:
        pass

    # Strategy 3: Text search on the title
    try:
        url = f"{gamma_base}/events"
        formatted_date = target_date  # YYYY-MM-DD
        search_title = f"S&P 500 {formatted_date}"
        resp = retry_request(session, "GET", url, data_client.retry_config,
                             params={"title": search_title, "closed": "false"})
        if resp.status_code == 200:
            data = resp.json()
            events = data if isinstance(data, list) else []
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                markets = ev.get("markets") or []
                spx_markets = [m for m in markets if isinstance(m, dict) and _is_spx_up_down_market(m)]
                if spx_markets:
                    return ev
    except Exception:
        pass

    return None


@app.route("/api/market/spx", methods=["POST"])
def api_market_spx():
    """Fetch S&P 500 (SPX) daily close up/down prediction market for a given date."""
    data = request.json or {}
    target_date = data.get("date", "").strip()

    if not target_date:
        # Default to today (US Eastern time would be ideal, but we use UTC for simplicity)
        from datetime import date as _date
        target_date = _date.utcnow().strftime("%Y-%m-%d")

    # Validate date format
    try:
        from datetime import datetime as _dt
        parsed = _dt.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    if not data_client:
        return jsonify({"error": "Data client not initialized"}), 500

    try:
        event_info = _find_spx_market_for_date(target_date)
        if not event_info:
            return jsonify({"error": f"No SPX daily close market found for {target_date}"}), 404

        markets = event_info.get("markets") or []
        # Filter to SPX up/down markets
        spx_markets = [m for m in markets if isinstance(m, dict) and _is_spx_up_down_market(m)]

        if not spx_markets:
            return jsonify({"error": f"No SPX up/down market found for {target_date}"}), 404

        outcomes = []
        for market in spx_markets:
            mid = str(market.get("id") or market.get("marketId") or market.get("market_id") or "")
            if not mid:
                continue

            question = str(market.get("question") or market.get("title") or "")
            clob_tokens = data_client.get_clob_token_ids(mid)
            if not isinstance(clob_tokens, dict) or len(clob_tokens) == 0:
                continue

            for outcome_name, token_id in clob_tokens.items():
                raw_outcome = str(outcome_name).strip()
                # For SPX markets, label UP/DOWN based on outcome
                lower = raw_outcome.lower()
                if lower == "yes":
                    # Determine if "Yes" means "UP" or "DOWN" from the question
                    q_lower = question.lower()
                    if any(kw in q_lower for kw in ("close above", "close up", "rise", "go up", "higher", "positive")):
                        display = "上涨 (UP)"
                    elif any(kw in q_lower for kw in ("close below", "close down", "fall", "go down", "lower", "negative")):
                        display = "下跌 (DOWN)"
                    else:
                        display = f"Yes ({question[:50]}...)"
                elif lower == "no":
                    q_lower = question.lower()
                    if any(kw in q_lower for kw in ("close above", "close up", "rise", "go up", "higher", "positive")):
                        display = "下跌 (DOWN)"
                    elif any(kw in q_lower for kw in ("close below", "close down", "fall", "go down", "lower", "negative")):
                        display = "上涨 (UP)"
                    else:
                        display = f"No ({question[:50]}...)"
                else:
                    display = raw_outcome

                outcomes.append({
                    "name": display,
                    "raw_outcome": raw_outcome,
                    "market_id": mid,
                    "market_title": question,
                    "token_id": str(token_id),
                    **_price_snapshot_for_token(str(token_id)),
                })

        if not outcomes:
            return jsonify({"error": f"No tradeable tokens found for SPX market on {target_date}"}), 404

        # Check user's existing positions
        wallet_address = get_dashboard_wallet_address()
        my_positions = [] if wallet_address else None
        if wallet_address:
            try:
                all_positions = data_client.fetch_positions_for_user(wallet_address)
                for pos in (all_positions or []):
                    pos_token = str(pos.get("asset") or pos.get("token_id") or pos.get("tokenId") or "")
                    for oc in outcomes:
                        if pos_token == oc["token_id"]:
                            my_positions.append({
                                "outcome": oc["name"],
                                "token_id": pos_token,
                                "size": float(pos.get("size") or pos.get("amount") or 0),
                                "avg_cost": float(pos.get("avgPrice") or pos.get("avg_price") or 0),
                                "current_value": float(pos.get("currentValue") or pos.get("current_value") or 0),
                            })
                            break
            except Exception as exc:
                logger.debug("Failed to fetch user positions for SPX: %s", exc)
                my_positions = None

        event_title = str(event_info.get("title") or f"S&P 500 Daily Close {target_date}")
        event_slug = str(event_info.get("slug") or "")
        event_url = f"https://polymarket.com/event/{event_slug}" if event_slug else ""

        return jsonify({
            "status": "ok",
            "date": target_date,
            "market_type": "spx",
            "market_title": event_title,
            "event_url": event_url,
            "outcomes": outcomes,
            "my_positions": my_positions,
        })

    except Exception as exc:
        logger.error("SPX market fetch failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/trade/place", methods=["POST"])
def api_trade_place():
    """执行买入或卖出订单 (YES/NO)"""
    data = request.json or {}
    token_id = data.get("token_id")
    side = data.get("side", "").upper()  # "BUY" or "SELL"
    size, parse_error = _parse_positive_float(data.get("size"), "size")
    if parse_error:
        return parse_error
    price = data.get("price")

    if not token_id or side not in ("BUY", "SELL") or size is None:
        return jsonify({"error": "Invalid parameters: need token_id, side (BUY/SELL), size > 0"}), 400

    if price is not None:
        try:
            price = float(price)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid price"}), 400
        if not math.isfinite(price) or price <= 0:
            return jsonify({"error": "price must be a finite number > 0"}), 400

    current_wallet = get_current_wallet()
    if not current_wallet:
        return jsonify({"error": "No wallet selected"}), 400

    executor = wallet_executors.get(current_wallet_id) if current_wallet_id in wallet_executors else clob_executor
    if not executor:
        return jsonify({"error": "Wallet executor not initialized"}), 500

    try:
        # 如果未提供 price，尝试从订单簿获取最佳价格
        if price is None:
            if not data_client:
                return jsonify({"error": "Data client not initialized"}), 500
            orderbook = data_client.get_order_book(token_id)
            if orderbook:
                def _px(item):
                    try:
                        if isinstance(item, dict):
                            v = item.get("price") or item.get("px") or item.get("p")
                            return float(v) if v is not None else None
                        if isinstance(item, (list, tuple)) and item:
                            return float(item[0])
                        return float(item)
                    except Exception:
                        return None

                if side == "BUY":
                    asks = orderbook.asks or []
                    if isinstance(asks, list) and asks:
                        price = min((_px(a) for a in asks if _px(a) is not None), default=None)
                else:  # SELL
                    bids = orderbook.bids or []
                    if isinstance(bids, list) and bids:
                        price = max((_px(b) for b in bids if _px(b) is not None), default=None)

            if price is None or price <= 0:
                return jsonify({"error": "Could not determine market price from orderbook"}), 400
            price = float(price)

        logger.info(
            "Executing %s %.4f of %s at %.4f using wallet %s (%s...)",
            side, size, token_id[:20], price,
            current_wallet.name, current_wallet.address[:20]
        )

        result = executor.place_order(
            side=side,
            price=price,
            size=size,
            market_id=token_id,
            token_id=token_id
        )

        status = result.get("status", "unknown")
        order_id = result.get("orderId") or result.get("order_id") or ""
        error_msg = result.get("error")

        if status == "failed" and error_msg:
            user_error = error_msg
            if "425" in error_msg or "service not ready" in error_msg.lower():
                user_error = "CLOB API 凭证未就绪 (425)。请检查钱包是否已在 Polymarket 注册并授权。如问题持续，请重启服务重试。"
            return jsonify({"error": user_error, "status": "failed"}), 400

        # 查询订单成交状态
        filled_size = result.get("filled_size")
        if order_id and executor:
            try:
                order_status = executor.get_order(order_id)
                if order_status:
                    for key in ("size_matched", "sizeMatched", "filledSize", "filled_size"):
                        if order_status.get(key) is not None:
                            try:
                                matched = float(order_status[key])
                                if 0 <= matched <= size:
                                    filled_size = matched
                                    status = "filled" if matched >= size else "pending"
                            except (TypeError, ValueError):
                                pass
                            break
            except Exception:
                pass

        return jsonify({
            "status": "success" if status in ("success", "filled", "pending", "simulated") else status,
            "order_id": order_id,
            "order_status": status,
            "side": side,
            "size": size,
            "price": round(price, 4),
            "filled_size": filled_size,
            "wallet": current_wallet.name,
            "message": f"{side} order placed: {size} @ {price}",
        })

    except Exception as exc:
        logger.error("Trade place failed: %s", exc, exc_info=True)
        error_str = str(exc)
        if "425" in error_str or "service not ready" in error_str.lower():
            error_str = "CLOB API 凭证未就绪 (425)。请检查钱包是否已在 Polymarket 注册并授权。如问题持续，请重启服务重试。"
        return jsonify({"error": error_str}), 500


def get_dashboard_port() -> int:
    """
    Get dashboard listen port from env.

    Priority:
    - DASHBOARD_PORT
    - PORT (common PaaS convention)
    - default 5000
    """
    load_dotenv()
    raw = (os.getenv("DASHBOARD_PORT") or os.getenv("PORT") or "5000").strip()
    try:
        port = int(raw)
    except Exception:  # noqa: BLE001
        port = 5000
    if port < 1 or port > 65535:
        return 5000
    return port


if __name__ == "__main__":
    # Print startup banner
    print("="*50)
    print("Polymarket Dashboard API Starting...")
    print(f"Time: {datetime.now().isoformat()}")
    print("Checking PolymarketDataClient.get_order_book...")
    try:
        if hasattr(PolymarketDataClient, 'get_order_book'):
            print("OK: PolymarketDataClient.get_order_book exists.")
        else:
            print("ERROR: PolymarketDataClient.get_order_book MISSING!")
    except Exception as e:
        print(f"Error checking method: {e}")
    print("="*50)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )
    
    init_services()
    
    port = get_dashboard_port()
    logger.info("Starting Dashboard API server on http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
