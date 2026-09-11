"""Minimal placeholder — map API needs live app+DB; covered by unit GeoJSON helpers offline."""

def test_placeholder_map_offline():
    from app.utils.geo_utils import feature_collection

    payload = feature_collection([])
    assert payload["type"] == "FeatureCollection"
    assert payload["features"] == []
