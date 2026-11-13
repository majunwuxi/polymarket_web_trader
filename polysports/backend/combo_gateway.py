"""已验证钱包的 Combo Builder SDK 客户端。"""
from polymarket import BuilderApiKey

from .trading_gateway import Gateway, TradingError


class ComboGateway(Gateway):
    def sdk_api_key(self, credential):
        if self.wallet.wallet_type != 'DEPOSIT_WALLET':
            raise TradingError('combo_wallet_unsupported', 'Builder Combo 需要已部署的 Deposit Wallet')
        fields = (credential.builder_api_key, credential.builder_api_secret, credential.builder_api_passphrase)
        if any(value is None or not value.get_secret_value() for value in fields):
            raise TradingError('builder_credentials_missing', '未配置完整的 Builder 凭证')
        return BuilderApiKey(*(value.get_secret_value() for value in fields))

    def ensure_funding(self, quote, direction):
        """只读验证实际转移所需余额及 Exchange V3 授权。"""
        from decimal import Decimal, localcontext
        from web3 import Web3
        from .trading_gateway import TradingError
        try:
            url=self.settings.polygon_rpc_url.get_secret_value()
            w3=Web3(Web3.HTTPProvider(url,request_kwargs={'timeout':10}))
            if w3.eth.chain_id!=137:raise ValueError('wrong chain')
            wallet=Web3.to_checksum_address(self.wallet.wallet_address)
            exchange=Web3.to_checksum_address('0xe3333700cA9d93003F00f0F71f8515005F6c00Aa')
            with localcontext() as ctx:
                ctx.prec=80
                raw=Decimal(quote.total_required)*Decimal(10**6)
            if raw<=0 or raw!=raw.to_integral_value():raise ValueError('invalid amount')
            if direction=='BUY':
                abi=[{'type':'function','name':'balanceOf','stateMutability':'view','inputs':[{'name':'owner','type':'address'}],'outputs':[{'type':'uint256'}]},{'type':'function','name':'allowance','stateMutability':'view','inputs':[{'name':'owner','type':'address'},{'name':'spender','type':'address'}],'outputs':[{'type':'uint256'}]}]
                contract=w3.eth.contract(address=Web3.to_checksum_address('0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB'),abi=abi)
                balance=contract.functions.balanceOf(wallet).call();approved=contract.functions.allowance(wallet,exchange).call()>=int(raw)
            else:
                abi=[{'type':'function','name':'balanceOf','stateMutability':'view','inputs':[{'name':'account','type':'address'},{'name':'id','type':'uint256'}],'outputs':[{'type':'uint256'}]},{'type':'function','name':'isApprovedForAll','stateMutability':'view','inputs':[{'name':'account','type':'address'},{'name':'operator','type':'address'}],'outputs':[{'type':'bool'}]}]
                contract=w3.eth.contract(address=Web3.to_checksum_address('0x006F54F7f9A22e0000CC2AB60031000000ae9fEF'),abi=abi)
                balance=contract.functions.balanceOf(wallet,int(quote.position_id)).call();approved=contract.functions.isApprovedForAll(wallet,exchange).call() is True
            if balance<int(raw):raise TradingError('balance_insufficient','组合交易余额不足')
            if not approved:raise TradingError('allowance_insufficient','组合交易授权不足，请在外部完成授权')
        except TradingError:raise
        except Exception as exc:raise TradingError('combo_funding_unknown','无法验证组合余额与授权') from exc
