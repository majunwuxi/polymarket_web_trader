"""已验证钱包的官方 SDK 赎回客户端；不自动授权或部署钱包。"""
from __future__ import annotations

import re

from polymarket import BuilderApiKey, RelayerApiKey, RequestRejectedError, UserInputError
from web3 import Web3

from .trading_gateway import Gateway, TradingError

POSITION_MANAGER = '0x006F54F7f9A22e0000CC2AB60031000000ae9fEF'
CTF = '0x4D97DCd97eC945f40cF65F87097ACe5EA0476045'
PROTOCOL_V2_ROUTER = '0x12121212006e4CD160D18e3f00711DA5c3372600'
CTF_COLLATERAL_ADAPTER = '0xAdA100Db00Ca00073811820692005400218FcE1f'
NEG_RISK_CTF_COLLATERAL_ADAPTER = '0xadA2005600Dec949baf300f4C6120000bDB6eAab'

_APPROVAL_ABI = [{
    'type': 'function',
    'name': 'isApprovedForAll',
    'stateMutability': 'view',
    'inputs': [{'name': 'account', 'type': 'address'}, {'name': 'operator', 'type': 'address'}],
    'outputs': [{'type': 'bool'}],
}]


class RedeemUnknown(Exception):
    def __init__(self, tx_hash: str | None = None, transaction_id: str | None = None):
        self.tx_hash = tx_hash
        self.transaction_id = transaction_id
        super().__init__('redemption result unknown')


class RedeemGateway(Gateway):
    def sdk_api_key(self, credential):
        if self.wallet.wallet_type == 'EOA':
            return None
        builder = (credential.builder_api_key, credential.builder_api_secret, credential.builder_api_passphrase)
        if all(value is not None and value.get_secret_value() for value in builder):
            return BuilderApiKey(*(value.get_secret_value() for value in builder))
        if credential.relayer_api_key and credential.relayer_api_key_address:
            return RelayerApiKey(key=credential.relayer_api_key.get_secret_value(),
                                 address=credential.relayer_api_key_address)
        raise TradingError('redeem_credentials_missing',
                           '智能钱包赎回需要 Relayer API Key，或完整的 Builder 凭证')

    def __enter__(self):
        super().__enter__()
        if not hasattr(self.client, 'redeem_positions'):
            self.client.close()
            self.client = None
            raise TradingError('redeem_sdk_unsupported', '当前官方 SDK 不支持仓位赎回')
        return self

    def preflight_approval(self, *, target_type: str, asset_kind: str | None = None, negative_risk: bool | None = None):
        if not self.settings.polygon_rpc_url:
            raise TradingError('rpc_unavailable', '未配置 Polygon RPC，无法核对赎回授权')
        if target_type == 'combo' or asset_kind == 'position':
            ledger, operator = POSITION_MANAGER, PROTOCOL_V2_ROUTER
        elif asset_kind == 'ctf_token':
            if type(negative_risk) is not bool:
                raise TradingError('redeem_approval_unknown', '无法确认 CTF 市场类型和赎回授权')
            ledger = CTF
            operator = NEG_RISK_CTF_COLLATERAL_ADAPTER if negative_risk else CTF_COLLATERAL_ADAPTER
        else:
            raise TradingError('redeem_approval_unknown', '无法确认仓位类型和赎回授权')
        try:
            web3 = Web3(Web3.HTTPProvider(self.settings.polygon_rpc_url.get_secret_value(), request_kwargs={'timeout': 10}))
            if web3.eth.chain_id != 137:
                raise TradingError('rpc_wrong_chain', 'RPC 不是 Polygon 主网')
            contract = web3.eth.contract(address=Web3.to_checksum_address(ledger), abi=_APPROVAL_ABI)
            approved = contract.functions.isApprovedForAll(
                Web3.to_checksum_address(self.wallet.wallet_address),
                Web3.to_checksum_address(operator),
            ).call()
            if approved is not True:
                raise TradingError('redeem_approval_required', '赎回授权不足，请先在外部完成 Router 或 Adapter 授权')
        except TradingError:
            raise
        except Exception as exc:
            raise TradingError('redeem_approval_unknown', '无法核对赎回授权，未发送交易') from exc

    def redeem(self, *, condition_id: str | None = None, position_id: str | None = None):
        arguments = {'condition_id': condition_id} if condition_id is not None else {'position_id': position_id}
        try:
            handle = self.client.redeem_positions(**arguments)
        except (UserInputError, RequestRejectedError) as exc:
            raise TradingError('redeem_rejected', '赎回请求在发送前被拒绝，请刷新后重试') from exc
        except Exception as exc:
            raise RedeemUnknown() from exc
        offered_hash = getattr(handle, 'transaction_hash', None)
        offered_id = getattr(handle, 'transaction_id', None)
        try:
            outcome = handle.wait()
        except Exception as exc:
            raise RedeemUnknown(offered_hash, offered_id) from exc
        tx_hash = str(getattr(outcome, 'transaction_hash', None) or offered_hash or '')
        transaction_id = getattr(outcome, 'transaction_id', None) or offered_id
        if not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx_hash):
            raise RedeemUnknown(offered_hash, offered_id)
        return tx_hash.lower(), str(transaction_id) if transaction_id is not None else None
