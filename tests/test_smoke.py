def test_database_is_reachable(subscription):
    assert subscription.pk is not None
    assert subscription.status == "trial"
