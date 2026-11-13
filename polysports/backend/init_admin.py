"""本机唯一管理员初始化；密码只通过终端交互输入。"""
import argparse
import getpass
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .auth import hasher
from .config import get_settings
from .database import make_engine


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化唯一管理员；不会覆盖已有账户")
    parser.add_argument("--username", required=True, help="管理员用户名")
    username = parser.parse_args().username.strip().casefold()
    if not username or len(username) > 100:
        print("用户名无效")
        return 1
    password = getpass.getpass("管理员密码（至少 12 个字符）：")
    confirmation = getpass.getpass("再次输入密码：")
    if password != confirmation or len(password) < 12 or len(password.encode("utf-8")) > 1024:
        print("密码不一致或长度不符合要求")
        return 1
    engine = make_engine(get_settings())
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO admin_users (id,username,password_hash) VALUES (:id,:username,:password_hash)"), {"id":uuid.uuid4(), "username":username, "password_hash":hasher.hash(password)})
        print("管理员已初始化")
        return 0
    except IntegrityError:
        print("管理员已存在；未修改现有账户")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
