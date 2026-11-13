"""
生成新的以太坊钱包私钥和地址
用于 Polymarket 自动交易机器人
"""

from web3 import Web3
import secrets

def generate_wallet():
    """生成新的以太坊钱包"""
    # 生成随机私钥
    private_key = secrets.token_hex(32)
    private_key_with_prefix = f"0x{private_key}"
    
    # 从私钥创建账户
    account = Web3().eth.account.from_key(private_key_with_prefix)
    address = account.address
    
    print("=" * 60)
    print("新钱包已生成！")
    print("=" * 60)
    print(f"私钥 (PRIVATE_KEY): {private_key_with_prefix}")
    print(f"地址 (FUNDER_ADDRESS): {address}")
    print("=" * 60)
    print("\n重要提示：")
    print("1. 请妥善保管私钥，任何人获得私钥都可以控制你的钱包！")
    print("2. 不要将私钥分享给任何人或上传到任何地方！")
    print("3. 建议将私钥保存在安全的地方（如密码管理器）")
    print("4. 确保钱包中有足够的 USDC（在 Polygon 网络上）用于交易")
    print("5. 将私钥和地址添加到 .env 文件中：")
    print(f"   POLY_PRIVATE_KEY={private_key_with_prefix}")
    print(f"   POLY_FUNDER_ADDRESS={address}")
    print("=" * 60)
    
    return private_key_with_prefix, address

if __name__ == "__main__":
    generate_wallet()
