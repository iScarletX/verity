def get_user(cursor, user_id):
    query = "SELECT * FROM users WHERE id = " + user_id
    cursor.execute(query)
