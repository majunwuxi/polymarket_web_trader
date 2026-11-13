#!/usr/bin/env python3
"""
Generate a 2FA secret for the Dashboard.
Run this script to generate a secret key that you can add to your .env file
as DASHBOARD_2FA_SECRET.
"""

import pyotp
import qrcode
import os

def generate_secret():
    # Generate a random base32 secret
    secret = pyotp.random_base32()
    
    print("\n" + "="*50)
    print("DASHBOARD 2FA SECRET GENERATOR")
    print("="*50 + "\n")
    
    print(f"Generated Secret: {secret}")
    print("\nAdd this line to your .env file:")
    print(f"DASHBOARD_2FA_SECRET={secret}")
    
    # Generate provisioning URI
    # You can change 'PolymarketTrader' and 'admin' to whatever you like
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name="admin",
        issuer_name="PolymarketTrader"
    )
    
    print("\n" + "-"*50)
    print("Scan this QR code with your Authenticator App (Google Auth, Authy, etc.):")
    
    # Generate QR code in terminal
    qr = qrcode.QRCode()
    qr.add_data(uri)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    
    print("-"*50)
    print(f"Provisioning URI (if QR code fails): {uri}")
    print("="*50 + "\n")

if __name__ == "__main__":
    try:
        generate_secret()
    except ImportError as e:
        print(f"Error: {e}")
        print("Please ensure 'pyotp' and 'qrcode' are installed.")
        print("pip install pyotp qrcode")
