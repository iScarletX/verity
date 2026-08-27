account_id = "internal-account-1"
cursor.execute(f"SELECT balance FROM accounts WHERE id = '{account_id}'")
