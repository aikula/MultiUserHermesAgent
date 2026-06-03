"""Tests: Hard quota — P0-3 from spec 01."""


class TestHardQuota:
    """Preflight quota check blocks requests when exhausted."""

    def test_check_quota_allows_when_sufficient(self, test_user):
        from app.quota import check_quota
        ok, msg = check_quota(test_user)
        assert ok is True
        assert msg == ""

    def test_check_quota_blocks_when_exhausted(self, test_user, db):
        from app.quota import check_quota
        # Set quota to 0
        db.execute("UPDATE users SET quota_remaining=0 WHERE uid=?", (test_user,))
        db.commit()
        ok, msg = check_quota(test_user)
        assert ok is False
        assert "исчерпана" in msg.lower() or "квота" in msg.lower()

    def test_quota_never_negative_with_hard_quota(self, test_user, db):
        from app.quota import check_quota
        # Set quota to 0
        db.execute("UPDATE users SET quota_remaining=0 WHERE uid=?", (test_user,))
        db.commit()
        ok, _ = check_quota(test_user)
        assert ok is False
        # Verify quota didn't go negative
        row = db.execute("SELECT quota_remaining FROM users WHERE uid=?", (test_user,)).fetchone()
        assert row["quota_remaining"] >= 0
