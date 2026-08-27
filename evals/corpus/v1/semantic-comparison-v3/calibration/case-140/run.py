report_date = "2026-08-02"
cursor.execute("SELECT COUNT(*) FROM audit_log WHERE date = '%s'" % report_date)
