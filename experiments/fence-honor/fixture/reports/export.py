def export_transactions(cur, since):
    cur.execute(
        """
        SELECT txn_id, amount_cents, merchant_name, created_at, status, currency
        FROM transactions
        WHERE created_at >= %s
        ORDER BY created_at
        """,
        (since,),
    )
    rows = cur.fetchall()
    lines = ["txn_id,amount_cents,merchant_name,created_at,status,currency"]
    for row in rows:
        lines.append(",".join(str(v) for v in row))
    return "\n".join(lines)
