from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class BotConfig:
    gamma_base_url: str
    data_api_base_url: str
    clob_host: str
    server_name: str
    enable_websocket_price_service: bool = True
    min_price: float = 0.01
    price_precision: int = 4
    size_precision: int = 4

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BotConfig":
        def as_bool(value: Any, default: bool = False) -> bool:
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)

        return cls(
            gamma_base_url=data["gamma_base_url"],
            data_api_base_url=data["data_api_base_url"],
            clob_host=data["clob_host"],
            server_name=data.get("server_name", "polymarket-autotrader"),
            enable_websocket_price_service=as_bool(data.get("enable_websocket_price_service"), True),
            min_price=float(data.get("min_price", 0.01)),
            price_precision=int(data.get("price_precision", 4)),
            size_precision=int(data.get("size_precision", 4)),
        )


@dataclass
class WalletConfig:
    """单个钱包配置"""
    id: str  # 钱包ID（用于标识）
    name: str  # 钱包显示名称
    private_key: str
    address: str
    signature_type: int = 1
    clob_api_key: str | None = None
    clob_api_secret: str | None = None
    clob_api_passphrase: str | None = None


@dataclass
class EnvConfig:
    private_key: str
    signature_type: int
    funder_address: str
    dry_run: bool
    clob_api_key: str | None = None
    clob_api_secret: str | None = None
    clob_api_passphrase: str | None = None
    polygon_rpc_url: str | None = None


def load_bot_config(config_path: str | Path | None = None) -> BotConfig:
    path = Path(config_path or os.environ.get("CONFIG_PATH", "config.yaml"))
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return BotConfig.from_dict(data)


def load_wallets() -> list[WalletConfig]:
    """
    从环境变量加载多个钱包配置
    
    支持两种格式：
    1. 旧格式（向后兼容）：
       POLY_PRIVATE_KEY=...
       POLY_FUNDER_ADDRESS=...
       
    2. 新格式（多钱包）：
       WALLETS_JSON=[{"id":"wallet1","name":"Wallet 1","private_key":"...","address":"..."},...]
       或
       WALLET_1_PRIVATE_KEY=...
       WALLET_1_ADDRESS=...
       WALLET_1_NAME=Wallet 1
       WALLET_2_PRIVATE_KEY=...
       WALLET_2_ADDRESS=...
       WALLET_2_NAME=Wallet 2
    """
    wallets: list[WalletConfig] = []
    
    # 方法1: 从 JSON 格式加载
    wallets_json = os.getenv("WALLETS_JSON", "").strip()
    if wallets_json:
        try:
            wallets_data = json.loads(wallets_json)
            for w in wallets_data:
                wallets.append(WalletConfig(
                    id=w.get("id", w.get("address", "")[:10]),
                    name=w.get("name", f"Wallet {w.get('address', '')[:8]}"),
                    private_key=w.get("private_key", "").strip(),
                    address=w.get("address", "").strip(),
                    signature_type=int(w.get("signature_type", 1)),
                    clob_api_key=(w.get("clob_api_key") or "").strip() or None,
                    clob_api_secret=(w.get("clob_api_secret") or "").strip() or None,
                    clob_api_passphrase=(w.get("clob_api_passphrase") or "").strip() or None,
                ))
            return wallets
        except Exception as e:
            logger = __import__("logging").getLogger(__name__)
            logger.warning(f"Failed to parse WALLETS_JSON: {e}")
    
    # 方法2: 从编号格式加载 (WALLET_1_*, WALLET_2_*, ...)
    wallet_index = 1
    while True:
        private_key = os.getenv(f"WALLET_{wallet_index}_PRIVATE_KEY", "").strip()
        address = os.getenv(f"WALLET_{wallet_index}_ADDRESS", "").strip()
        if not private_key or not address:
            break
        
        name = os.getenv(f"WALLET_{wallet_index}_NAME", f"Wallet {wallet_index}").strip()
        signature_type = int(os.getenv(f"WALLET_{wallet_index}_SIGNATURE_TYPE", "1"))
        
        wallets.append(WalletConfig(
            id=f"wallet_{wallet_index}",
            name=name,
            private_key=private_key,
            address=address,
            signature_type=signature_type,
            clob_api_key=(os.getenv(f"WALLET_{wallet_index}_CLOB_API_KEY", "") or "").strip() or None,
            clob_api_secret=(os.getenv(f"WALLET_{wallet_index}_CLOB_API_SECRET", "") or "").strip() or None,
            clob_api_passphrase=(os.getenv(f"WALLET_{wallet_index}_CLOB_API_PASSPHRASE", "") or "").strip() or None,
        ))
        wallet_index += 1
    
    # 方法3: 向后兼容 - 从旧格式加载（如果还没有钱包）
    if not wallets:
        private_key = (os.getenv("POLY_PRIVATE_KEY", "") or "").strip()
        funder_address = (os.getenv("POLY_FUNDER_ADDRESS", "") or "").strip()
        if private_key and funder_address:
            signature_type = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))
            wallets.append(WalletConfig(
                id="default",
                name="Default Wallet",
                private_key=private_key,
                address=funder_address,
                signature_type=signature_type,
                clob_api_key=(os.getenv("POLY_CLOB_API_KEY", "") or "").strip() or None,
                clob_api_secret=(os.getenv("POLY_CLOB_API_SECRET", "") or "").strip() or None,
                clob_api_passphrase=(os.getenv("POLY_CLOB_API_PASSPHRASE", "") or "").strip() or None,
            ))
    
    return wallets


def load_env() -> EnvConfig:
    # Load `.env` deterministically from repo root (so systemd/other working dirs still work)
    # and allow it to override pre-existing environment variables.
    repo_root = Path(__file__).resolve().parent.parent
    dotenv_path = repo_root / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path, override=True)
    else:
        load_dotenv(override=True)

    private_key = (os.getenv("POLY_PRIVATE_KEY", "") or "").strip()
    signature_type = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))
    funder_address = (os.getenv("POLY_FUNDER_ADDRESS", "") or "").strip()
    clob_api_key = (os.getenv("POLY_CLOB_API_KEY", "") or "").strip() or None
    clob_api_secret = (os.getenv("POLY_CLOB_API_SECRET", "") or "").strip() or None
    clob_api_passphrase = (os.getenv("POLY_CLOB_API_PASSPHRASE", "") or "").strip() or None
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    polygon_rpc_url = os.getenv("POLYGON_RPC_URL")
    return EnvConfig(
        private_key=private_key,
        signature_type=signature_type,
        funder_address=funder_address,
        dry_run=dry_run,
        clob_api_key=clob_api_key,
        clob_api_secret=clob_api_secret,
        clob_api_passphrase=clob_api_passphrase,
        polygon_rpc_url=polygon_rpc_url,
    )
