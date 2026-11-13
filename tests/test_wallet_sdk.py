import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from eth_account import Account
from polymarket_autotrader.config import EnvConfig
from polymarket_autotrader.execution import CLOBOrderExecutor

KEY = '0x' + '01' * 32
SIGNER = Account.from_key(KEY).address
WALLET = '0x' + '12' * 20

def environment(kind, wallet=None, **kwargs):
    return EnvConfig(private_key=KEY, signature_type=kind, funder_address=wallet or (SIGNER if kind == 0 else WALLET), dry_run=False, **kwargs)

class WalletSDKTests(unittest.TestCase):
    def executor(self, env):
        return CLOBOrderExecutor('https://clob.polymarket.com', env, 4, 4)

    @patch('polymarket.SecureClient.create')
    @patch('polymarket_autotrader.execution.Web3')
    def test_wallet_types_and_cache(self, web3, create):
        web3.is_address.return_value = True
        web3.to_checksum_address.side_effect = lambda x: x
        web3.return_value.eth.chain_id = 137
        web3.return_value.eth.get_code.return_value = b'code'
        for kind, name in [(0,'EOA'),(1,'POLY_PROXY'),(2,'GNOSIS_SAFE'),(3,'DEPOSIT_WALLET')]:
            with self.subTest(kind=kind):
                env=environment(kind)
                client=Mock(wallet=env.funder_address, wallet_type=name)
                create.return_value=client
                executor=self.executor(env)
                self.assertFalse(executor.has_api_creds)
                self.assertIs(executor._get_client(),client)
                self.assertIs(executor._get_client(),client)
                self.assertTrue(executor.has_api_creds)
        self.assertEqual(create.call_count,4)

    @patch('polymarket.SecureClient.create')
    @patch('polymarket_autotrader.execution.Web3')
    def test_missing_deployment_mismatch_and_partial_creds_fail_closed(self, web3, create):
        web3.is_address.return_value=True
        web3.to_checksum_address.side_effect=lambda x:x
        web3.return_value.eth.chain_id=137
        web3.return_value.eth.get_code.return_value=b''
        with self.assertRaisesRegex(ValueError,'未部署'):
            self.executor(environment(3))._get_client()
        create.assert_not_called()
        with self.assertRaisesRegex(ValueError,'凭证不完整'):
            self.executor(environment(3, clob_api_key='key'))._get_client()
        create.assert_not_called()
        web3.return_value.eth.get_code.return_value=b'code'
        client=Mock(wallet=WALLET,wallet_type='POLY_PROXY')
        create.return_value=client
        with self.assertRaisesRegex(ValueError,'不匹配'):
            self.executor(environment(3))._get_client()
        client.close.assert_called_once()

    @patch('polymarket.SecureClient.create')
    @patch('polymarket_autotrader.execution.Web3')
    def test_complete_credentials_are_passed_without_cross_wallet_cache(self, web3, create):
        web3.is_address.return_value=True
        web3.to_checksum_address.side_effect=lambda x:x
        client=Mock(wallet=SIGNER,wallet_type='EOA')
        create.return_value=client
        first=self.executor(environment(0,clob_api_key='k',clob_api_secret='s',clob_api_passphrase='p'))
        second=self.executor(environment(0))
        first._get_client(); second._get_client()
        self.assertEqual(create.call_count,2)
        self.assertIsNotNone(create.call_args_list[0].kwargs['credentials'])
        self.assertIsNone(create.call_args_list[1].kwargs['credentials'])

if __name__ == '__main__': unittest.main()
